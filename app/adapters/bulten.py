from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.adapters.base import SourceAdapter, extract_published_at, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class BultenVastgoedAdapter(SourceAdapter):
    """Discover and enrich rental offers from Bulten's HomeSearch portal."""

    source_name = "bulten_vastgoed"
    display_name = "Bulten Vastgoed"
    search_url = "https://aanbod.bultenvastgoed.nl/"
    closed_markers = ("verhuurd", "ingetrokken", "reageren niet mogelijk")

    def discover(self) -> list[NormalizedListing]:
        listings = self.parse(self.fetch_html())
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Woningzoeker/0.2)",
            "Accept-Language": "nl-NL,nl;q=0.9",
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            return [
                self._fetch_detail(client, listing) if listing.is_available else listing
                for listing in listings
            ]

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select(".card-title a[href*='/te-huur/']")
        if not links:
            links = soup.select("a[href*='/te-huur/']")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        for link in links:
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
        unavailable = any(marker in full_text.lower() for marker in self.closed_markers)
        image = card.select_one("img.photo_listing, img[src], img[data-src]")
        raw_image = str(image.get("src") or image.get("data-src")) if image else None
        image_url = urljoin(self.search_url, raw_image) if raw_image else None
        area = self._labelled_feature(card, "Woonoppervlakte") or self._area_value(full_text)
        bedrooms = self._labelled_feature(card, "Slaapkamers", integer=True)
        rooms = self._labelled_feature(card, "Kamers", integer=True)
        return NormalizedListing(
            published_at=extract_published_at(card),
            source_name=self.source_name,
            external_id=identifier.group(1),
            url=url,
            title=f"{address}, {city}",
            address=address,
            city=city,
            property_type=self._property_type(full_text),
            rent_base=self._rent_value(full_text),
            rent_total=self._rent_value(full_text),
            area_m2=area if isinstance(area, Decimal) else None,
            bedrooms=bedrooms if isinstance(bedrooms, int) else None,
            rooms=rooms if isinstance(rooms, int) else None,
            availability_text="reageren niet mogelijk" if unavailable else "beschikbaar",
            is_available=not unavailable,
            image_url=image_url,
            raw_data={"card_text": full_text[:1200]},
        )

    def _fetch_detail(self, client: httpx.Client, listing: NormalizedListing) -> NormalizedListing:
        try:
            response = client.get(str(listing.url))
            response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return listing
        return self._enrich(listing, response.text)

    def _enrich(self, listing: NormalizedListing, html: str) -> NormalizedListing:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        details: dict[str, str] = {}
        for row in soup.select("tr"):
            cells = row.select("th, td")
            if len(cells) >= 2:
                details[cells[0].get_text(" ", strip=True).lower()] = cells[-1].get_text(" ", strip=True)
        image = soup.select_one("img.gallery_img1, .gallery img, img.photo_listing")
        raw_image = str(image.get("src") or image.get("data-src")) if image else None
        description_node = soup.select_one("p.collapse, .object-description, .property-description")
        description = description_node.get_text(" ", strip=True) if description_node else None
        rent_text = details.get("huurprijs") or details.get("prijs")
        area_text = details.get("oppervlakte") or details.get("woonoppervlakte")
        bedroom_text = details.get("slaapkamers") or details.get("aantal slaapkamers")
        property_text = details.get("type woning") or details.get("woningtype")
        closed = any(marker in text.lower() for marker in self.closed_markers)
        postcode_match = re.search(r"\b\d{4}\s?[A-Z]{2}\b", text, re.I)
        return listing.model_copy(
            update={
                "postcode": postcode_match.group().upper() if postcode_match else listing.postcode,
                "property_type": property_text or listing.property_type,
                "rent_base": parse_nl_currency(rent_text) or listing.rent_base,
                "rent_total": parse_nl_currency(rent_text) or listing.rent_total,
                "area_m2": parse_decimal(area_text) or listing.area_m2,
                "bedrooms": parse_int(bedroom_text) if bedroom_text else listing.bedrooms,
                "description": description or listing.description,
                "image_url": urljoin(str(listing.url), raw_image) if raw_image else listing.image_url,
                "published_at": extract_published_at(soup) or listing.published_at,
                "availability_text": ("detail: reageren niet mogelijk" if closed else "detail: beschikbaar"),
                "is_available": listing.is_available and not closed,
                "raw_data": {**listing.raw_data, "detail_fields": details},
            }
        )

    @staticmethod
    def _card_for(link: Tag) -> Tag:
        explicit = link.find_parent(class_="card")
        if isinstance(explicit, Tag):
            return explicit
        card: Tag = link
        for _ in range(8):
            parent = card.parent
            if not isinstance(parent, Tag):
                break
            card = parent
            text = card.get_text(" ", strip=True).lower()
            if "m2" in text or "m²" in text or "verhuurd" in text:
                return card
        return card

    @staticmethod
    def _labelled_feature(card: Tag, label: str, *, integer: bool = False) -> Decimal | int | None:
        icon = card.select_one(f"img[alt='{label}']")
        if icon is None or not isinstance(icon.parent, Tag):
            return None
        value_node = icon.parent.select_one(".propertylist_feature-text")
        value = value_node.get_text(" ", strip=True) if value_node else icon.parent.get_text(" ", strip=True)
        return parse_int(value) if integer else parse_decimal(value)

    @staticmethod
    def _rent_value(text: str) -> Decimal | None:
        return parse_nl_currency(text) if "€" in text or "&euro;" in text else None

    @staticmethod
    def _area_value(text: str) -> Decimal | None:
        match = re.search(r"\d+(?:[.,]\d+)?\s*m(?:²|2)", text, re.I)
        return parse_decimal(match.group()) if match else None

    @staticmethod
    def _property_type(text: str) -> str | None:
        for kind in ("appartement", "studio", "kamer", "huis", "woning"):
            if kind in text.lower():
                return kind
        return None
