from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.adapters.base import BrowserSourceAdapter, parse_decimal, parse_int, parse_nl_currency
from app.schemas import NormalizedListing


class FundaRentalAdapter(BrowserSourceAdapter):
    source_name = "funda_rentals"
    display_name = "Funda huur"
    search_url = "https://www.funda.nl/zoeken/huur?selected_area=groningen"
    ready_selector = "a[data-testid='listingDetailsAddress']"

    def parse(self, html: str) -> list[NormalizedListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        for link in soup.select("a[data-testid='listingDetailsAddress'][href*='/detail/huur/groningen/']"):
            try:
                listing = self._parse_link(link)
                if listing.external_id not in seen:
                    listings.append(listing)
                    seen.add(listing.external_id)
            except (AttributeError, ValueError):
                continue
        return listings

    def _parse_link(self, link: Tag) -> NormalizedListing:
        url = urljoin(self.search_url, str(link.get("href")))
        path = urlparse(url).path
        id_match = re.search(r"/(\d+)/?$", path)
        if not id_match:
            raise ValueError("missing Funda listing ID")
        divs = link.find_all("div", recursive=False)
        if len(divs) < 2:
            raise ValueError("missing Funda address")
        address = divs[0].get_text(" ", strip=True)
        location = divs[1].get_text(" ", strip=True)
        postcode_match = re.search(r"\b(\d{4}\s?[A-Z]{2})\b", location, re.I)
        postcode = postcode_match.group(1).upper() if postcode_match else None
        city = re.sub(r"^\d{4}\s?[A-Z]{2}\s*", "", location, flags=re.I).strip() or "Groningen"
        card = link.find_parent(class_=lambda value: value and "border-b" in value.split())
        if not isinstance(card, Tag):
            card = link.parent if isinstance(link.parent, Tag) else link
        text = card.get_text(" ", strip=True)
        price = parse_nl_currency(text)
        area_match = re.search(r"\d+(?:[.,]\d+)?\s*m²", text)
        area = parse_decimal(area_match.group()) if area_match else None
        items = card.select("li")
        bedrooms = next(
            (parse_int(item.get_text(" ", strip=True)) for item in items if "icon-bed" in str(item)),
            None,
        )
        if bedrooms is None and len(items) >= 2:
            bedrooms = parse_int(items[1].get_text(" ", strip=True))
        image = card.select_one("img[src]")
        image_url = str(image.get("src")) if image else None
        slug = path.split("/groningen/", 1)[-1].split("/", 1)[0]
        property_type = slug.split("-", 1)[0] if "-" in slug else None

        return NormalizedListing(
            source_name=self.source_name,
            external_id=id_match.group(1),
            url=url,
            title=f"{address}, {city}",
            address=address,
            postcode=postcode,
            city=city,
            property_type=property_type,
            rent_base=price,
            rent_total=price,
            area_m2=area,
            bedrooms=bedrooms,
            is_available=True,
            image_url=image_url,
            raw_data={"card_text": text[:800]},
        )
