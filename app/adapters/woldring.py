from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, parse_decimal, parse_nl_currency
from app.schemas import NormalizedListing


class WoldringAdapter(SourceAdapter):
    source_name = "woldring"
    display_name = "Woldring Verhuur"
    search_url = "https://woldringverhuur.nl/"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        for card in soup.select(".living-area-tile"):
            try:
                listing = self._parse_card(card)
                if listing.external_id not in seen:
                    listings.append(listing)
                    seen.add(listing.external_id)
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        link = card.select_one(".primary_address a[href]")
        if not link:
            raise ValueError("missing address link")
        url = str(link.get("href"))
        address = link.get_text(" ", strip=True)
        card_id = str(card.get("id", ""))
        external_id = card_id.removeprefix("living-area-") or urlparse(url).path.strip("/")

        attributes: dict[str, str] = {}
        for row in card.select(".attributes tr"):
            cells = row.find_all("td")
            if len(cells) >= 3:
                label = cells[-2].get_text(" ", strip=True).rstrip(":").lower()
                attributes[label] = cells[-1].get_text(" ", strip=True)
            elif cells:
                text = row.get_text(" ", strip=True)
                attributes[text.lower()] = text

        price = parse_nl_currency(self._text(card.select_one(".price")))
        service = parse_nl_currency(attributes.get("exclusief servicekosten"))
        total = price + service if price is not None and service is not None else price
        status_node = card.select_one("[id^='available_from'], .banner")
        availability = self._text(status_node)
        full_text = card.get_text(" ", strip=True)
        unavailable_words = ("niet beschikbaar", "inschrijving gesloten")
        is_available = price is not None and not any(word in full_text.lower() for word in unavailable_words)
        image = card.select_one("img[src]")
        image_url = str(image.get("src")) if image else None
        property_type = self._infer_property_type(address, full_text)

        return NormalizedListing(
            source_name=self.source_name,
            external_id=external_id,
            url=url,
            title=f"{address}, Groningen",
            address=address,
            city="Groningen",
            property_type=property_type,
            rent_base=price,
            service_costs=service,
            rent_total=total,
            area_m2=parse_decimal(attributes.get("oppervlakte")),
            availability_text=availability,
            is_available=is_available,
            image_url=image_url,
            raw_data={"attributes": attributes, "status_text": full_text[:500]},
        )

    @staticmethod
    def _infer_property_type(address: str, text: str) -> str | None:
        combined = f"{address} {text}".lower()
        for property_type in ("appartement", "studio", "kamer"):
            if re.search(rf"\b{property_type}\b", combined):
                return property_type
        return None

    @staticmethod
    def _text(node: Tag | None) -> str | None:
        return node.get_text(" ", strip=True) if node else None
