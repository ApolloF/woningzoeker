from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, extract_published_at, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class RotsvastGroningenAdapter(SourceAdapter):
    source_name = "rotsvast_groningen"
    display_name = "Rotsvast Groningen"
    search_url = "https://www.rotsvast.nl/huren/?department=rotsvast-groningen"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        for card in soup.select("a.card--house[href*='/huren/']"):
            try:
                listings.append(self._parse_card(card))
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        url = str(card.get("href"))
        address = self._text(card.select_one(".card-house__title"))
        text_nodes = card.select(".card-house__text")
        city = self._text(text_nodes[0]) if text_nodes else None
        if not address or not city:
            raise ValueError("missing Rotsvast address")
        full_text = card.get_text(" ", strip=True)
        area_node = card.select_one(".icon-surface")
        bed_node = card.select_one(".icon-bed")
        availability_node = card.select_one(".icon-clock")
        image = card.select_one("img[src]")
        external_match = re.search(r"-(h\d+)/?$", urlparse(url).path, re.I)
        external_id = external_match.group(1) if external_match else urlparse(url).path.strip("/")
        availability = self._parent_text(availability_node)
        return NormalizedListing(
            published_at=extract_published_at(card),
            source_name=self.source_name,
            external_id=external_id,
            url=url,
            title=f"{address}, {city}",
            address=address,
            city=city,
            rent_base=parse_nl_currency(full_text),
            rent_total=parse_nl_currency(full_text),
            area_m2=parse_decimal(self._parent_text(area_node)),
            bedrooms=parse_int(self._parent_text(bed_node)),
            availability_text=availability,
            is_available=not any(word in full_text.lower() for word in ("verhuurd", "onder optie")),
            image_url=str(image.get("src")) if image else None,
            raw_data={"card_text": full_text[:800]},
        )

    @staticmethod
    def _text(node: Tag | None) -> str | None:
        return node.get_text(" ", strip=True) if node else None

    @staticmethod
    def _parent_text(node: Tag | None) -> str | None:
        return node.parent.get_text(" ", strip=True) if node and isinstance(node.parent, Tag) else None
