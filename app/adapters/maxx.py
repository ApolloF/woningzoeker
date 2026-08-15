from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class MaxxGroningenAdapter(SourceAdapter):
    source_name = "maxx_groningen"
    display_name = "Maxx Groningen"
    search_url = "https://maxxhuren.nl/woonruimte-huren/?city=Groningen"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        for card in soup.select("a[href*='/objects/ads/view/id-']"):
            try:
                listings.append(self._parse_card(card))
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        url = urljoin(self.search_url, str(card.get("href")))
        id_match = re.search(r"id-(\d+)", url)
        if not id_match:
            raise ValueError("missing Maxx ID")
        lines = [line.strip() for line in card.get_text("\n", strip=True).splitlines() if line.strip()]
        price_line = next((line for line in lines if "€" in line), "")
        area_line = next((line for line in lines if re.search(r"m²", line)), "")
        rooms_line = next((line for line in lines if "kamer" in line.lower()), "")
        city_index = next((i for i, line in enumerate(lines) if line.lower() == "groningen"), -1)
        if city_index < 1:
            raise ValueError("missing Maxx address")
        status = lines[0]
        address = lines[city_index - 1]
        property_type = lines[city_index + 1].lower() if len(lines) > city_index + 1 else None
        image = card.select_one("img[src]")
        is_available = "beschikbaar" in status.lower() and not any(
            word in status.lower() for word in ("bijna verhuurd", "reeds verhuurd")
        )
        return NormalizedListing(
            source_name=self.source_name,
            external_id=id_match.group(1),
            url=url,
            title=f"{address}, Groningen",
            address=address,
            city="Groningen",
            property_type=property_type,
            rent_base=parse_nl_currency(price_line),
            rent_total=parse_nl_currency(price_line),
            area_m2=parse_decimal(area_line),
            rooms=parse_int(rooms_line),
            availability_text=status,
            is_available=is_available,
            image_url=str(image.get("src")) if image else None,
            raw_data={"card_lines": lines[:15]},
        )
