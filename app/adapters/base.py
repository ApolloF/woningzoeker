from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from bs4 import Tag
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.schemas import NormalizedListing


class AdapterError(RuntimeError):
    pass


class SourceChallengeError(AdapterError):
    """The source presented a challenge that must not be bypassed automatically."""


class SourceDriftError(AdapterError):
    """The source loaded, but its expected listing surface was no longer present."""


class SourceAdapter(ABC):
    source_name: str
    display_name: str
    search_url: str

    def __init__(self, timeout_seconds: float = 25.0) -> None:
        self.timeout_seconds = timeout_seconds

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_html(self) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; Woningzoeker/0.2; +self-hosted-personal-rental-monitor)"
            ),
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            response = client.get(self.search_url)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", ""):
                raise AdapterError(f"unexpected content type for {self.source_name}")
            return response.text

    def discover(self) -> list[NormalizedListing]:
        return self.parse(self.fetch_html())

    @abstractmethod
    def parse(self, html: str) -> list[NormalizedListing]:
        raise NotImplementedError


class BrowserSourceAdapter(SourceAdapter):
    """Fetch a public result page in a real browser for client-rendered sources."""

    ready_selector: str

    @retry(
        retry=retry_if_exception_type((PlaywrightTimeoutError, PlaywrightError)),
        wait=wait_exponential(multiplier=1, min=2, max=12),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    def fetch_html(self) -> str:
        settings = get_settings()
        launch_options: dict[str, object] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        if settings.chromium_executable_path:
            launch_options["executable_path"] = settings.chromium_executable_path

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options)  # type: ignore[arg-type]
            try:
                context = browser.new_context(
                    locale="nl-NL",
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.goto(
                    self.search_url,
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_seconds * 1000,
                )
                page.wait_for_selector(
                    self.ready_selector,
                    timeout=settings.browser_timeout_seconds * 1000,
                )
                body_text = page.locator("body").inner_text().lower()
                if any(
                    marker in body_text
                    for marker in ("captcha", "verify you are human", "controleer of je een mens")
                ):
                    raise SourceChallengeError(f"challenge shown by {self.source_name}")
                return page.content()
            finally:
                browser.close()


def parse_nl_currency(text: str | None) -> Decimal | None:
    if not text:
        return None
    # Prefer the first explicit euro amount so house numbers and surface areas are not mistaken for rent.
    for marker in ("\u20ac", "&euro;"):
        if marker in text:
            text = text[text.index(marker) :]
            break
    match = re.search(r"(?:\u20ac|&euro;)?\s*(\d[\d.\s]*)(?:,(\d{1,2}))?", text)
    if not match:
        return None
    whole = match.group(1).replace(".", "").replace(" ", "")
    cents = (match.group(2) or "0").ljust(2, "0")
    try:
        return Decimal(f"{whole}.{cents}")
    except InvalidOperation:
        return None


def parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def extract_published_at(node: Tag) -> datetime | None:
    """Extract explicitly labelled publication time, never an availability date."""
    selectors = (
        "time[itemprop='datePosted'],time[itemprop='datePublished'],"
        "[data-published-at],[data-date-posted],"
        "meta[itemprop='datePosted'],meta[itemprop='datePublished']"
    )
    for match in node.select(selectors):
        value: Any = (
            match.get("datetime")
            or match.get("content")
            or match.get("data-published-at")
            or match.get("data-date-posted")
        )
        if not value:
            continue
        parsed = _parse_published_datetime(str(value))
        if parsed is not None:
            return parsed
    for script in node.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for value in _published_values(payload):
            parsed = _parse_published_datetime(value)
            if parsed is not None:
                return parsed
    return None


def _published_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        direct = [str(value[key]) for key in ("datePosted", "datePublished") if value.get(key)]
        return direct + [item for child in value.values() for item in _published_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _published_values(child)]
    return []


def _parse_published_datetime(value: str) -> datetime | None:
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        match = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", cleaned)
        if not match:
            return None
        parsed = datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
