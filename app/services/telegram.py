from __future__ import annotations

import html
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings
from app.models import Decision, Listing, SourceConfig
from app.schemas import Criteria

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def notify_listing(self, listing: Listing, source: SourceConfig, criteria: Criteria) -> dict[str, Any]:
        if not self.configured:
            return {"sent": False, "reason": "telegram_not_configured"}
        if not self._listing_matches_filter(listing, criteria):
            return {"sent": False, "reason": "listing_filter"}
        total = f"EUR {listing.rent_total}" if listing.rent_total is not None else "onbekend"
        area = f"{listing.area_m2} m2" if listing.area_m2 is not None else "onbekend"
        dashboard_url = f"{self.settings.public_base_url.rstrip('/')}/listings/{listing.id}"
        text = (
            f"<b>{html.escape(listing.title)}</b>\n"
            f"Huur totaal: {html.escape(total)}\n"
            f"Servicekosten: {html.escape(str(listing.service_costs or 'onbekend'))}\n"
            f"Oppervlakte: {html.escape(area)}\n"
            f"Bron: {html.escape(source.display_name)}\n"
            f"Score/besluit: {listing.match_score}/100 - {listing.decision}\n"
            f"{'Geplaatst' if listing.published_at else 'Eerste detectie'}: "
            f"{(listing.published_at or listing.first_seen_at).isoformat()}\n"
            f"Reactie verzonden: {'ja' if listing.response_sent else 'nee'}"
        )
        return self._deliver(
            text,
            [
                {"text": "Open listing", "url": listing.url},
                {"text": "Open dashboard", "url": dashboard_url},
            ],
            listing_id=listing.id,
        )

    @staticmethod
    def _listing_matches_filter(listing: Listing, criteria: Criteria) -> bool:
        mode = criteria.telegram_listing_filter
        auto = listing.decision == Decision.AUTO_REACT.value
        score = listing.match_score >= criteria.telegram_min_score
        return {
            "all": True,
            "auto_react": auto,
            "score": score,
            "auto_react_or_score": auto or score,
            "off": False,
        }[mode]

    def notify_reaction_sent(self, listing: Listing, source: SourceConfig) -> dict[str, Any]:
        if not self.configured:
            return {"sent": False, "reason": "telegram_not_configured"}
        dashboard_url = f"{self.settings.public_base_url.rstrip('/')}/listings/{listing.id}"
        return self._deliver(
            f"<b>Automatisch gereageerd</b>\n{html.escape(listing.title)}\n"
            f"Bron: {html.escape(source.display_name)}\nScore: {listing.match_score}/100",
            [
                {"text": "Open advertentie", "url": listing.url},
                {"text": "Bekijk reactie", "url": dashboard_url},
            ],
            listing_id=listing.id,
        )

    def notify_source_failure(self, source: SourceConfig) -> dict[str, Any]:
        """Alert once when a source first crosses the sustained-failure threshold."""
        if not self.configured:
            return {"sent": False, "reason": "telegram_not_configured"}
        dashboard_url = f"{self.settings.public_base_url.rstrip('/')}/"
        text = (
            f"<b>Woningbron werkt niet meer betrouwbaar</b>\n"
            f"Bron: {html.escape(source.display_name)}\n"
            f"Mislukte controles op rij: {source.consecutive_failures}\n"
            f"Laatste fout: {html.escape(source.last_error or 'onbekend')}\n"
            "Andere bronnen blijven actief. Controleer de bronstatus en pas de adapter zo nodig aan."
        )
        return self._deliver(
            text,
            [
                {"text": "Open bron", "url": source.base_url},
                {"text": "Open dashboard", "url": dashboard_url},
            ],
        )

    def _deliver(
        self,
        text: str,
        buttons: list[dict[str, str]],
        *,
        listing_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            return self._send(text, buttons)
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception(
                "telegram delivery exhausted retries",
                extra={
                    "context": {
                        "listing_id": listing_id,
                        "error": type(exc).__name__,
                    }
                },
            )
            return {"sent": False, "reason": "delivery_failed", "error": type(exc).__name__}

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _send(self, text: str, buttons: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [buttons]},
        }
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        with httpx.Client(timeout=15) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
        return {"sent": True, "message_id": result.get("result", {}).get("message_id")}
