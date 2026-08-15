from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class PandomoAdapter(SourceAdapter):
    source_name = "pandomo"
    display_name = "Pandomo (legacy rental feed)"
    # The current Pandomo site redirects its former rental path to the homepage.
    search_url = "https://old.pandomo.nl/huurwoningen/"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        for card in soup.select("li.results__item"):
            link = card.select_one("h3 a[href*='/huurwoningen/h/']")
            if not link:
                continue
            try:
                listings.append(self._parse_card(card, urljoin(self.search_url, str(link.get("href")))))
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag, url: str) -> NormalizedListing:
        full_text = card.get_text(" ", strip=True)
        title_link = card.select_one("h3 a[href*='/huurwoningen/h/']")
        address = title_link.get_text(" ", strip=True) if title_link else ""
        if not address:
            raise ValueError("missing Pandomo address")
        location_match = re.search(r"\b(\d{4}\s?[A-Z]{2})\s+([A-ZÀ-Ý][A-ZÀ-Ý ]+?)\s+€", full_text)
        postcode = location_match.group(1) if location_match else None
        city = location_match.group(2).strip().title() if location_match else "Groningen"
        area_match = re.search(r"\d+(?:[.,]\d+)?\s*m²", full_text)
        rooms_match = re.search(r"\d+\s*kamer", full_text, re.I)
        status_node = card.select_one(".results__item__image__label")
        status = status_node.get_text(" ", strip=True) if status_node else "beschikbaar"
        image = card.select_one("img[src], img[data-src]")
        image_url = (
            urljoin(self.search_url, str(image.get("src") or image.get("data-src"))) if image else None
        )
        external_match = re.search(r"-(\d+)/?$", urlparse(url).path)
        return NormalizedListing(
            source_name=self.source_name,
            external_id=external_match.group(1) if external_match else urlparse(url).path.strip("/"),
            url=url,
            title=f"{address}, {city}",
            address=address,
            postcode=postcode,
            city=city,
            rent_base=parse_nl_currency(full_text),
            rent_total=parse_nl_currency(full_text),
            area_m2=parse_decimal(area_match.group()) if area_match else None,
            rooms=parse_int(rooms_match.group()) if rooms_match else None,
            availability_text=status,
            is_available=status.lower() == "beschikbaar",
            image_url=image_url,
            raw_data={"legacy_feed": True, "card_text": full_text[:800]},
        )
