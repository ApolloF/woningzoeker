from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class BultenVastgoedAdapter(SourceAdapter):
    """Discover rental offers from Bulten's current HomeSearch portal."""

    source_name = "bulten_vastgoed"
    display_name = "Bulten Vastgoed"
    search_url = "https://aanbod.bultenvastgoed.nl/"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        for link in soup.select("a[href*='/te-huur/']"):
            try:
                listing = self._parse_link(link)
            except (AttributeError, ValueError):
                continue
            if listing.external_id not in seen:
                listings.append(listing)
                seen.add(listing.external_id)
        return listings

    def _parse_link(self, link: Tag) -> NormalizedListing:
        url = urljoin(self.search_url, str(link.get("href")))
        identifier = re.search(r"/te-huur/(\d+)/", urlparse(url).path)
        if not identifier:
            raise ValueError("missing Bulten listing ID")
        address = link.get_text(" ", strip=True)
        if not address:
            raise ValueError("missing Bulten address")
        card = self._card_for(link)
        full_text = card.get_text(" ", strip=True)
        city_match = re.search(r"\b(Groningen|Haren)\b", full_text, re.I)
        city = city_match.group(1).title() if city_match else "Groningen"
        unavailable = any(marker in full_text.lower() for marker in ("verhuurd", "ingetrokken"))
        image = card.select_one("img[src], img[data-src]")
        image_url = str(image.get("src") or image.get("data-src")) if image else None
        return NormalizedListing(
            source_name=self.source_name,
            external_id=identifier.group(1),
            url=url,
            title=f"{address}, {city}",
            address=address,
            city=city,
            property_type=self._property_type(full_text),
            rent_base=parse_nl_currency(full_text),
            rent_total=parse_nl_currency(full_text),
            area_m2=self._area_value(full_text),
            bedrooms=self._feature_value(full_text, "slaapkamer"),
            rooms=self._feature_value(full_text, "kamer"),
            availability_text="verhuurd" if unavailable else "beschikbaar",
            is_available=not unavailable,
            image_url=image_url,
            raw_data={"card_text": full_text[:1200]},
        )

    @staticmethod
    def _card_for(link: Tag) -> Tag:
        card: Tag = link
        for _ in range(6):
            parent = card.parent
            if not isinstance(parent, Tag):
                break
            card = parent
            text = card.get_text(" ", strip=True)
            if "m2" in text.lower() or "m²" in text.lower() or "verhuurd" in text.lower():
                return card
        return card

    @staticmethod
    def _area_value(text: str) -> Decimal | None:
        match = re.search(r"\d+(?:[.,]\d+)?\s*m(?:²|2)", text, re.I)
        return parse_decimal(match.group()) if match else None

    @staticmethod
    def _feature_value(text: str, label: str) -> int | None:
        match = re.search(rf"(\d+)\s*{label}", text, re.I)
        return parse_int(match.group(1)) if match else None

    @staticmethod
    def _property_type(text: str) -> str | None:
        for kind in ("appartement", "studio", "kamer", "huis", "woning"):
            if kind in text.lower():
                return kind
        return None