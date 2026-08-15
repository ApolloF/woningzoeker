from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import extract_published_at, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class ParariusFamilyParser:
    """Parser shared by Pararius and Huurwoningen.nl's current card markup."""

    source_name: str
    search_url: str

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        for card in soup.select(".listing-search-item__content"):
            try:
                listing = self._parse_card(card)
                if listing.external_id not in seen:
                    listings.append(listing)
                    seen.add(listing.external_id)
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        link = card.select_one(".listing-search-item__link--title[href]")
        if not link:
            raise ValueError("missing listing link")
        url = urljoin(self.search_url, str(link.get("href")))
        title = link.get_text(" ", strip=True)
        property_type = self._property_type(title, url)
        address = re.sub(r"^(appartement|studio|kamer|huis|woning)\s+", "", title, flags=re.I).strip()
        location = self._text(card.select_one(".listing-search-item__sub-title")) or "Groningen"
        postcode_match = re.search(r"\b(\d{4}\s?[A-Z]{2})\b", location, re.I)
        postcode = postcode_match.group(1).upper().replace(" ", " ") if postcode_match else None
        city_match = re.search(r"\d{4}\s?[A-Z]{2}\s+([^,(]+)", location, re.I)
        city = city_match.group(1).strip() if city_match else "Groningen"
        features = card.select(".illustrated-features__item")
        area = next((parse_decimal(self._text(node)) for node in features if "m²" in node.text), None)
        rooms = next(
            (parse_int(self._text(node)) for node in features if "kamer" in node.text.lower()),
            None,
        )
        price = parse_nl_currency(self._text(card.select_one(".listing-search-item__price")))
        full_text = card.get_text(" ", strip=True)
        unavailable = any(word in full_text.lower() for word in ("verhuurd", "niet beschikbaar"))
        ancestor = card.find_parent(["li", "article"]) or card
        image = ancestor.select_one("img[src], img[data-src]")
        image_url = str(image.get("src") or image.get("data-src")) if image else None
        path_parts = [part for part in urlparse(url).path.split("/") if part]
        external_id = path_parts[-2] if len(path_parts) >= 2 else path_parts[-1]

        return NormalizedListing(
            published_at=extract_published_at(ancestor),
            source_name=self.source_name,
            external_id=external_id,
            url=url,
            title=f"{address}, {city}",
            address=address,
            postcode=postcode,
            city=city,
            property_type=property_type,
            rent_base=price,
            rent_total=price,
            area_m2=area,
            rooms=rooms,
            availability_text="beschikbaar" if not unavailable else "niet beschikbaar",
            is_available=not unavailable,
            image_url=image_url,
            raw_data={"card_text": full_text[:800]},
        )

    @staticmethod
    def _property_type(title: str, url: str) -> str | None:
        combined = f"{title} {url}".lower()
        for kind in ("appartement", "studio", "kamer", "huis", "woning"):
            if kind in combined:
                return kind
        return None

    @staticmethod
    def _text(node: Tag | None) -> str | None:
        return node.get_text(" ", strip=True) if node else None
