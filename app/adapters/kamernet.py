from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.adapters.base import (
    AdapterError,
    SourceAdapter,
    extract_published_at,
    parse_decimal,
    parse_int,
    parse_nl_currency,
)
from app.schemas import NormalizedListing


class KamernetAdapter(SourceAdapter):
    source_name = "kamernet"
    display_name = "Kamernet"
    search_url = "https://kamernet.nl/huren/kamers-groningen"

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_html(self) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            response = client.get(self.search_url)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", ""):
                raise AdapterError(f"unexpected content type for {self.source_name}")
            return response.text

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()

        cards: list[Tag] = list(
            soup.select(
                ".listing-card, .room-card, .search-result-card, [data-listing-id], "
                "div[class*='listing-card'], article[class*='listing-card'], article[class*='room-card']"
            )
        )
        if not cards:
            cards = [
                a.parent for a in soup.select("a[href*='/huren/'], a[href*='/for-rent/']")
                if isinstance(a.parent, Tag)
            ]

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing.external_id not in seen:
                    listings.append(listing)
                    seen.add(listing.external_id)
            except (AttributeError, ValueError):
                continue

        return listings

    def _parse_card(self, card: Tag) -> NormalizedListing:
        link = card.select_one("a[href*='/huren/'], a[href*='/for-rent/'], a.listing-card__link, a[href]")
        if not link:
            raise ValueError("missing Kamernet listing link")
        url = urljoin(self.search_url, str(link.get("href")))

        listing_id = card.get("data-listing-id") or card.get("data-id")
        if not listing_id:
            match = re.search(r"(?:kamer|studio|appartement|woning|listing|property)[-_](\d+)", url, re.I)
            if match:
                listing_id = match.group(1)
            else:
                path_parts = [p for p in urlparse(url).path.split("/") if p]
                listing_id = path_parts[-1] if path_parts else None

        if not listing_id:
            raise ValueError("missing Kamernet listing id")

        external_id = str(listing_id)

        title_node = card.select_one(
            ".listing-card__title, .tile-title, h2, h3, .room-card__title, a[title]"
        )
        title = title_node.get_text(" ", strip=True) if title_node else link.get_text(" ", strip=True)

        street_node = card.select_one(
            ".listing-card__street, .tile-street, .street, .location, .address"
        )
        address = street_node.get_text(" ", strip=True) if street_node else ""
        if not address:
            address = re.sub(
                r"^(appartement|studio|kamer|woning|huis)\s+(aan\s+de\s+|in\s+de\s+|op\s+de\s+|te\s+)?",
                "",
                title,
                flags=re.I,
            ).strip()

        city_node = card.select_one(
            ".listing-card__city, .tile-city, .city, .postal-code"
        )
        location_text = city_node.get_text(" ", strip=True) if city_node else "Groningen"
        postcode_match = re.search(r"\b(\d{4}\s?[A-Z]{2})\b", location_text, re.I)
        postcode = postcode_match.group(1).upper().replace(" ", " ") if postcode_match else None
        city_match = re.search(r"\b(\d{4}\s?[A-Z]{2})\s+([A-Za-z\s]+)", location_text, re.I)
        city = (
            re.sub(r"\(.*?\)", "", city_match.group(2)).strip()
            if city_match
            else ("Groningen" if "groningen" in location_text.lower() else location_text.split(",")[0].strip())
        )
        if not city:
            city = "Groningen"

        full_text = card.get_text(" ", strip=True)
        price_node = card.select_one(
            ".listing-card__price, .price-value, .price, .tile-price, span[class*='price']"
        )
        price = parse_nl_currency(price_node.get_text(" ", strip=True) if price_node else full_text)

        area_node = card.select_one(".feature-surface, .surface, [class*='surface'], [class*='area']")
        area_text = area_node.get_text(" ", strip=True) if area_node else full_text
        area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", area_text, re.I)
        if not area_match:
            area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m2", area_text, re.I)
        area = parse_decimal(area_match.group(1)) if area_match else None

        rooms_node = card.select_one(".feature-rooms, .rooms")
        rooms_text = rooms_node.get_text(" ", strip=True) if rooms_node else full_text
        rooms_match = re.search(r"(\d+)\s*kamer", rooms_text, re.I)
        rooms = parse_int(rooms_match.group(1)) if rooms_match else None

        bedrooms_match = re.search(r"(\d+)\s*slaapkamer", full_text, re.I)
        bedrooms = parse_int(bedrooms_match.group(1)) if bedrooms_match else None

        property_type = self._property_type(title, url, full_text)

        unavailable = any(
            w in full_text.lower()
            for w in ("verhuurd", "niet meer beschikbaar", "ingetrokken", "onder voorbehoud")
        )
        status_node = card.select_one(".listing-card__status, .availability-tag, .status")
        availability_text = (
            status_node.get_text(" ", strip=True)
            if status_node
            else ("niet beschikbaar" if unavailable else "beschikbaar")
        )

        image = card.select_one("img[src], img[data-src], img[data-lazy-src]")
        image_url = str(image.get("src") or image.get("data-src") or image.get("data-lazy-src")) if image else None

        return NormalizedListing(
            published_at=extract_published_at(card),
            source_name=self.source_name,
            external_id=external_id,
            url=url,
            title=title or f"{address}, {city}",
            address=address or title,
            postcode=postcode,
            city=city,
            property_type=property_type,
            rent_base=price,
            rent_total=price,
            area_m2=area,
            bedrooms=bedrooms,
            rooms=rooms,
            availability_text=availability_text,
            is_available=not unavailable,
            image_url=image_url,
            raw_data={"card_text": full_text[:800]},
        )

    def _enrich(self, overview: NormalizedListing, detail_html: str) -> NormalizedListing:
        soup = BeautifulSoup(detail_html, "html.parser")
        description_node = soup.select_one(".listing-description, .description, #description")
        description = description_node.get_text(" ", strip=True) if description_node else overview.description

        price_node = soup.select_one(".listing-price, .detail-price, .price")
        price = parse_nl_currency(price_node.get_text(" ", strip=True)) if price_node else overview.rent_total

        return overview.model_copy(
            update={
                "description": description,
                "rent_total": price or overview.rent_total,
                "rent_base": price or overview.rent_base,
            }
        )

    @staticmethod
    def _property_type(title: str, url: str, full_text: str) -> str | None:
        combined = f"{title} {url} {full_text}".lower()
        for kind in ("appartement", "studio", "kamer", "huis", "woning"):
            if kind in combined:
                return kind
        return None
