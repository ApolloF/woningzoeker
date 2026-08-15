from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, extract_published_at, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class OneTwoThreeWonenAdapter(SourceAdapter):
    source_name = "123wonen_groningen"
    display_name = "123Wonen Groningen"
    search_url = "https://www.123wonen.nl/huurwoningen/in/groningen"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        for card in soup.select(".pandlist-container"):
            try:
                listings.append(self._parse_card(card))
            except (AttributeError, ValueError):
                # One malformed card should not suppress valid cards from the same page.
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        detail_link = card.select_one("a[href*='/huur/']")
        onclick = card.get("onclick", "")
        onclick_match = re.search(r"location\.href=['\"]([^'\"]+)", str(onclick))
        url = str(detail_link.get("href")) if detail_link else ""
        if not url and onclick_match:
            url = onclick_match.group(1)
        if not url:
            raise ValueError("missing listing URL")

        title_node = card.select_one(".pand-title")
        address_node = card.select_one(".pand-address")
        if not title_node or not address_node:
            raise ValueError("missing title/address")
        address = address_node.get_text(" ", strip=True)
        title_text = title_node.get_text(" ", strip=True)
        city = title_text.split(",", 1)[0].strip() or "Groningen"

        specs: dict[str, str] = {}
        for row in card.select(".pand-specs:not(.d-block) li"):
            spans = row.find_all("span", recursive=False)
            if len(spans) >= 2:
                specs[spans[0].get_text(" ", strip=True).lower()] = spans[1].get_text(" ", strip=True)

        price = parse_nl_currency(self._text(card.select_one(".pand-price")))
        property_type = specs.get("type")
        full_text = card.get_text(" ", strip=True)
        is_available = "verhuurd" not in full_text.lower()
        availability = specs.get("beschikbaarheid")
        image = card.select_one(".pand-image[data-src]")
        image_url = str(image.get("data-src")) if image else None
        external_id = urlparse(url).path.rstrip("/").split("/")[-1]

        return NormalizedListing(
            published_at=extract_published_at(card),
            source_name=self.source_name,
            external_id=external_id,
            url=url,
            title=f"{address}, {city}",
            address=address,
            city=city,
            property_type=property_type.lower() if property_type else None,
            rent_base=price,
            rent_total=price,
            area_m2=parse_decimal(specs.get("woonoppervlakte")),
            bedrooms=parse_int(specs.get("slaapkamers")),
            availability_text=availability,
            is_available=is_available,
            image_url=image_url,
            raw_data={"specs": specs, "status_text": full_text[:500]},
        )

    @staticmethod
    def _text(node: Tag | None) -> str | None:
        return node.get_text(" ", strip=True) if node else None
