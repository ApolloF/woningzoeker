from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class CampusGroningenAdapter(SourceAdapter):
    source_name = "campus_groningen"
    display_name = "Campus Groningen (Groningse Panden)"
    search_url = "https://www.campusgroningen.com/huren-groningen"

    def discover(self) -> list[NormalizedListing]:
        listings = self.parse(self.fetch_html())
        headers = {"User-Agent": "Woningzoeker/0.2", "Accept-Language": "nl-NL,nl;q=0.9"}
        enriched: list[NormalizedListing] = []
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            for listing in listings:
                try:
                    response = client.get(str(listing.url))
                    response.raise_for_status()
                    enriched.append(self._enrich(listing, response.text))
                except httpx.HTTPError:
                    enriched.append(listing.model_copy(update={"is_available": False}))
        return enriched

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        for card in soup.select("a[href*='/woning/']"):
            try:
                listings.append(self._parse_card(card))
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        url = urljoin(self.search_url, str(card.get("href")))
        full_text = card.get_text(" ", strip=True)
        lines = list(card.stripped_strings)
        external_match = re.search(r"-(\d+)/?$", urlparse(url).path)
        if not external_match:
            raise ValueError("missing Campus listing ID")
        slug = urlparse(url).path.removeprefix("/woning/").rsplit("-", 1)[0]
        address_index = next((index for index, line in enumerate(lines) if "," in line), -1)
        if address_index >= 0:
            address, inline_city = (part.strip() for part in lines[address_index].split(",", 1))
            city = inline_city or (lines[address_index + 1].strip() if address_index + 1 < len(lines) else "")
        else:
            city = "Groningen"
            address = slug.removesuffix("-groningen").replace("-", " ").title()
        city = city or "Groningen"
        status = "verhuurd" if "verhuurd" in full_text.lower() else "overzicht: beschikbaar"
        image = card.select_one("img[src]")
        return NormalizedListing(
            source_name=self.source_name,
            external_id=external_match.group(1),
            url=url,
            title=f"{address}, {city}",
            address=address,
            city=city,
            rent_base=parse_nl_currency(full_text),
            rent_total=parse_nl_currency(full_text),
            description=full_text[:1200],
            availability_text=status,
            is_available=status != "verhuurd",
            image_url=str(image.get("src")) if image else None,
            raw_data={"successor_for": "Groningse Panden", "overview_text": full_text[:800]},
        )

    def _enrich(self, listing: NormalizedListing, html: str) -> NormalizedListing:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        features: dict[str, str] = {}
        for row in soup.select("table tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                features[cells[0].get_text(" ", strip=True).lower()] = cells[1].get_text(" ", strip=True)
        closed_markers = (
            "reageren op deze woning is daarom niet meer mogelijk",
            "inschrijving gesloten",
            "deze woning is verhuurd",
        )
        is_available = listing.is_available and not any(marker in text.lower() for marker in closed_markers)
        description_node = soup.select_one(".description, .omschrijving, [class*='description']")
        description = description_node.get_text(" ", strip=True) if description_node else text[:4000]
        property_type = features.get("woningtype")
        area = features.get("oppervlakte") or features.get("woonoppervlakte")
        rooms = features.get("aantal kamers")
        bedrooms = features.get("aantal slaapkamers")
        return listing.model_copy(
            update={
                "property_type": property_type.lower() if property_type else listing.property_type,
                "area_m2": parse_decimal(area),
                "rooms": parse_int(rooms),
                "bedrooms": parse_int(bedrooms),
                "description": description[:5000],
                "availability_text": "detail: beschikbaar" if is_available else "detail: reactie gesloten",
                "is_available": is_available,
                "raw_data": {**listing.raw_data, "detail_features": features},
            }
        )
