from __future__ import annotations

import pytest

from app.config import Settings
from app.services import interactive_captcha as ic
from app.services.interactive_captcha import (
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    InteractiveCaptchaSession,
    SessionMeta,
    get_session,
)


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []
        self.wheels: list[tuple[float, float]] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def wheel(self, dx: float, dy: float) -> None:
        self.wheels.append((dx, dy))


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []

    def type(self, value: str) -> None:
        self.typed.append(value)

    def press(self, value: str) -> None:
        self.pressed.append(value)


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.shots = 0

    def screenshot(self, timeout: int | None = None) -> bytes:
        self.shots += 1
        return b"PNG" + bytes([self.shots % 256])


class Clock:
    """Deterministic monotonic clock; sleep() just advances virtual time."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _meta(submission_id: int = 1) -> SessionMeta:
    return SessionMeta(
        submission_id=submission_id,
        listing_title="Mooie woning",
        listing_url="https://example.test/listing",
        source_display="Testbron",
    )


def test_apply_click_maps_normalized_to_viewport() -> None:
    page = FakePage()
    ic._apply(page, {"type": "click", "x": 0.5, "y": 0.25})
    assert page.mouse.clicks == [(0.5 * VIEWPORT_WIDTH, 0.25 * VIEWPORT_HEIGHT)]


def test_apply_click_clamps_out_of_range() -> None:
    page = FakePage()
    ic._apply(page, {"type": "click", "x": 5.0, "y": -1.0})
    assert page.mouse.clicks == [(float(VIEWPORT_WIDTH), 0.0)]


def test_apply_scroll_and_text() -> None:
    page = FakePage()
    ic._apply(page, {"type": "scroll", "dy": 400})
    ic._apply(page, {"type": "text", "value": "hello"})
    assert page.mouse.wheels == [(0, 400)]
    assert page.keyboard.typed == ["hello"]


def test_apply_key_allowlist_only() -> None:
    page = FakePage()
    ic._apply(page, {"type": "key", "value": "Enter"})
    ic._apply(page, {"type": "key", "value": "a"})  # not allowlisted
    assert page.keyboard.pressed == ["Enter"]


def test_drain_applies_all_queued_commands() -> None:
    page = FakePage()
    session = InteractiveCaptchaSession(meta=_meta(), deadline_monotonic=100.0)
    session.push({"type": "click", "x": 0.0, "y": 0.0})
    session.push({"type": "scroll", "dy": 100})
    applied = ic._drain(page, session)
    assert applied is True
    assert len(page.mouse.clicks) == 1
    assert page.mouse.wheels == [(0, 100)]
    assert ic._drain(page, session) is False  # queue now empty


def test_solve_returns_true_when_challenge_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(ic.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ic.time, "sleep", clock.sleep)
    page = FakePage()
    settings = Settings(_env_file=None)

    def cleared(_page: object) -> bool:
        return False

    assert ic.solve_interactively(page, settings, _meta(7), cleared) is True
    assert get_session(7) is None  # unregistered after completion
    assert page.shots >= 1  # at least one frame captured


def test_solve_returns_false_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(ic.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ic.time, "sleep", clock.sleep)
    page = FakePage()
    settings = Settings(_env_file=None, captcha_solve_timeout_seconds=30)

    def never_clears(_page: object) -> bool:
        return True

    assert ic.solve_interactively(page, settings, _meta(8), never_clears) is False
    assert get_session(8) is None


def test_solve_honours_user_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(ic.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ic.time, "sleep", clock.sleep)
    page = FakePage()
    settings = Settings(_env_file=None, captcha_solve_timeout_seconds=30)
    meta = _meta(9)

    # Register a session up front, mark it confirmed, then run: the loop should exit True.
    session = InteractiveCaptchaSession(meta=meta, deadline_monotonic=clock.t + 30)
    session.confirmed.set()
    ic._register(session)

    def never_clears(_page: object) -> bool:
        return True

    # solve_interactively registers its own session (superseding), so drive confirmation
    # through a challenge predicate that flips confirmed on the live session instead.
    def confirm_via_predicate(_page: object) -> bool:
        live = get_session(9)
        if live is not None:
            live.confirmed.set()
        return True

    assert ic.solve_interactively(page, settings, meta, confirm_via_predicate) is True
    assert get_session(9) is None
