"""In-process registry that relays a held CAPTCHA to a human via the dashboard.

When an auto-reaction hits a CAPTCHA, the browser thread keeps the live Playwright
page open and registers a session here. The web layer reads the latest screenshot and
pushes input commands (taps, scrolls, keystrokes); the *same* browser thread drains
those commands and applies them to the page. This keeps every Playwright call on the
thread that created the page (sync Playwright objects are thread-affine) while letting a
remote human solve only the challenge. Automation resumes once the challenge clears or
the human confirms.
"""

from __future__ import annotations

import contextlib
import html
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from playwright.sync_api import Page

from app.config import Settings

logger = logging.getLogger(__name__)

# Fixed viewport so normalized tap coordinates map 1:1 onto CSS pixels.
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 900

# Only these keys may be relayed to the page; anything else is ignored.
_ALLOWED_KEYS = {
    "Enter",
    "Backspace",
    "Tab",
    "Space",
    "Delete",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Escape",
}
_MAX_QUEUE = 200
_POLL_INTERVAL_SECONDS = 0.7


@dataclass
class SessionMeta:
    submission_id: int
    listing_title: str
    listing_url: str
    source_display: str


@dataclass
class InteractiveCaptchaSession:
    meta: SessionMeta
    deadline_monotonic: float
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _frame: bytes | None = None
    commands: queue.Queue[dict[str, object]] = field(
        default_factory=lambda: queue.Queue(maxsize=_MAX_QUEUE)
    )
    solved: threading.Event = field(default_factory=threading.Event)
    confirmed: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)

    def set_frame(self, png: bytes) -> None:
        with self._lock:
            self._frame = png

    def frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def push(self, command: dict[str, object]) -> bool:
        try:
            self.commands.put_nowait(command)
            return True
        except queue.Full:
            return False

    def remaining_seconds(self) -> int:
        return max(0, int(self.deadline_monotonic - time.monotonic()))


_registry: dict[int, InteractiveCaptchaSession] = {}
_registry_lock = threading.Lock()


def get_session(submission_id: int) -> InteractiveCaptchaSession | None:
    with _registry_lock:
        return _registry.get(submission_id)


def _register(session: InteractiveCaptchaSession) -> None:
    with _registry_lock:
        # A stale session for the same submission is superseded by the new attempt.
        existing = _registry.get(session.meta.submission_id)
        if existing is not None:
            existing.cancelled.set()
        _registry[session.meta.submission_id] = session


def _unregister(submission_id: int) -> None:
    with _registry_lock:
        _registry.pop(submission_id, None)


def solve_interactively(
    page: Page,
    settings: Settings,
    meta: SessionMeta,
    challenge_active: Callable[[Page], bool],
) -> bool:
    """Hold the page open for a human to solve the challenge.

    Returns True if the challenge cleared (or the human confirmed completion) before the
    timeout, so the caller may resume the automated flow. Returns False on timeout or
    cancellation, so the caller falls back to the manual assistance queue.

    Must be called from the thread that owns ``page``.
    """
    deadline = time.monotonic() + settings.captcha_solve_timeout_seconds
    session = InteractiveCaptchaSession(meta=meta, deadline_monotonic=deadline)
    _register(session)
    _notify_hold(settings, session)
    try:
        _capture(page, session)
        while time.monotonic() < deadline:
            if session.cancelled.is_set():
                return False
            applied = _drain(page, session)
            if applied:
                _capture(page, session)
            if session.confirmed.is_set() or not _still_active(page, challenge_active):
                session.solved.set()
                return True
            time.sleep(_POLL_INTERVAL_SECONDS)
            _capture(page, session)
        logger.info(
            "interactive captcha timed out",
            extra={"context": {"submission_id": meta.submission_id}},
        )
        return False
    finally:
        _unregister(meta.submission_id)


def _still_active(page: Page, challenge_active: Callable[[Page], bool]) -> bool:
    try:
        return challenge_active(page)
    except Exception:
        # If the page navigated away mid-check, treat the challenge as cleared.
        return False


def _capture(page: Page, session: InteractiveCaptchaSession) -> None:
    try:
        session.set_frame(page.screenshot(timeout=5_000))
    except Exception:
        logger.debug("interactive captcha screenshot failed", exc_info=True)


def _drain(page: Page, session: InteractiveCaptchaSession) -> bool:
    applied = False
    while True:
        try:
            command = session.commands.get_nowait()
        except queue.Empty:
            break
        with contextlib.suppress(Exception):
            _apply(page, command)
            applied = True
    return applied


def _apply(page: Page, command: dict[str, object]) -> None:
    kind = command.get("type")
    if kind == "click":
        x = _clamp(command.get("x")) * VIEWPORT_WIDTH
        y = _clamp(command.get("y")) * VIEWPORT_HEIGHT
        page.mouse.click(x, y)
    elif kind == "scroll":
        dy = int(_as_float(command.get("dy"), 0.0))
        page.mouse.wheel(0, max(-2_000, min(2_000, dy)))
    elif kind == "text":
        value = str(command.get("value", ""))[:200]
        if value:
            page.keyboard.type(value)
    elif kind == "key":
        value = str(command.get("value", ""))
        if value in _ALLOWED_KEYS:
            page.keyboard.press(value)


def _clamp(value: object) -> float:
    number = _as_float(value, 0.0)
    return max(0.0, min(1.0, number))


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _notify_hold(settings: Settings, session: InteractiveCaptchaSession) -> None:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    # Import locally to avoid a circular import with the DB/session layer.
    from app.db import SessionLocal
    from app.models import SearchConfig
    from app.schemas import Criteria

    try:
        with SessionLocal() as db:
            config = db.get(SearchConfig, 1)
            if config is not None and not Criteria.model_validate(config.config).telegram_notify_assistance:
                return
    except Exception:
        logger.debug("captcha hold notification config lookup failed", exc_info=True)

    meta = session.meta
    solve_url = f"{settings.public_base_url.rstrip('/')}/assistance/solve/{meta.submission_id}"
    minutes = max(1, settings.captcha_solve_timeout_seconds // 60)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": (
            f"<b>CAPTCHA los nu op</b>\n{html.escape(meta.listing_title or 'Woningreactie')}\n"
            f"Bron: {html.escape(meta.source_display)}\n"
            f"De reactie staat klaar en wacht op je CAPTCHA. Los binnen ~{minutes} min op, "
            "dan verstuurt de agent automatisch."
        ),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "Los CAPTCHA op", "url": solve_url},
                    {"text": "Open advertentie", "url": meta.listing_url},
                ]
            ]
        },
    }
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("captcha hold notification failed", exc_info=True)


__all__ = [
    "VIEWPORT_HEIGHT",
    "VIEWPORT_WIDTH",
    "InteractiveCaptchaSession",
    "SessionMeta",
    "get_session",
    "solve_interactively",
]
