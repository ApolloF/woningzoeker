from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class GrunoVastgoedAdapter(SourceAdapter):
    source_name = "gruno_vastgoed"
    display_name = "Gruno Vastgoed / Gruno Verhuur"
    search_url = "https://www.grunoverhuur.nl/woningaanbod/huur"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        for card in soup.select("div.object"):
            try:
                listing = self._parse_card(card)
                if listing.external_id not in seen:
                    listings.append(listing)
                    seen.add(listing.external_id)
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        link = card.select_one("a.sys-property-link[href*='/woningaanbod/huur/']")
        location_node = card.select_one(".obj_sub_address")
        if not link or not location_node:
            raise ValueError("missing Gruno card fields")
        url = urljoin(self.search_url, str(link.get("href")))
        location = location_node.get_text(" ", strip=True)
        address, _, postcode_city = location.partition(",")
        parts = postcode_city.strip().split(maxsplit=1)
        postcode = parts[0].upper() if parts else None
        city = parts[1] if len(parts) > 1 else "Groningen"
        headline = self._text(card.select_one(".obj_address"))
        status = self._text(card.select_one(".object_status")) or "beschikbaar"
        full_text = card.get_text(" ", strip=True)
        image = card.select_one("img[src]")
        public_id = self._text(card.select_one(".object_publicid > span"))
        external_id = public_id or urlparse(url).path.strip("/")
        property_type = self._text(card.select_one(".obj_type_price + ul li, .object_data > ul li"))
        return NormalizedListing(
            source_name=self.source_name,
            external_id=external_id,
            url=url,
            title=f"{address.strip()}, {city}",
            address=address.strip(),
            postcode=postcode,
            city=city,
            property_type=property_type,
            rent_base=parse_nl_currency(self._text(card.select_one(".obj_price"))),
            rent_total=parse_nl_currency(self._text(card.select_one(".obj_price"))),
            area_m2=parse_decimal(self._text(card.select_one(".object_sqfeet"))),
            bedrooms=parse_int(self._text(card.select_one(".object_bed_rooms"))),
            rooms=parse_int(self._text(card.select_one(".object_rooms"))),
            availability_text=status,
            is_available="verhuurd" not in status.lower(),
            image_url=str(image.get("src")) if image else None,
            description=headline,
            raw_data={"headline": headline, "card_text": full_text[:800]},
        )

    @staticmethod
    def _text(node: Tag | None) -> str | None:
        return node.get_text(" ", strip=True) if node else None
