from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CanonicalProperty
from app.schemas import NormalizedListing


def normalize_address(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value_without_postcode = re.sub(r"\b\d{4}\s?[A-Z]{2}\b", " ", ascii_value.upper())
    normalized = re.sub(r"[^A-Z0-9]+", " ", value_without_postcode).strip()
    replacements = {"STRAAT": "STR", "LAAN": "LN", "PLEIN": "PLN"}
    return " ".join(replacements.get(part, part) for part in normalized.split())


def make_dedup_key(listing: NormalizedListing) -> str:
    address = normalize_address(listing.address)
    postcode = (listing.postcode or "").replace(" ", "").upper()
    if postcode:
        identity = f"{postcode}|{address}"
    else:
        # Missing postcode is common in list cards. Price/area reduce false merges for vague addresses.
        rent = round(float(listing.rent_total or 0) / 25) * 25
        area = round(float(listing.area_m2 or 0) / 2) * 2
        identity = f"{listing.city.upper()}|{address}|{rent}|{area}"
    return hashlib.sha256(identity.encode()).hexdigest()


def find_or_create_canonical(db: Session, listing: NormalizedListing) -> CanonicalProperty:
    key = make_dedup_key(listing)
    existing = db.scalar(select(CanonicalProperty).where(CanonicalProperty.dedup_key == key))
    if existing:
        existing.last_seen_at = datetime.now(UTC)
        return existing

    normalized = normalize_address(listing.address)
    candidates = db.scalars(select(CanonicalProperty).where(CanonicalProperty.city.ilike(listing.city))).all()
    for candidate in candidates:
        similarity = SequenceMatcher(None, normalized, candidate.normalized_address).ratio()
        price_close = _difference_within(listing.rent_total, candidate.rent_total, 35)
        area_close = _difference_within(listing.area_m2, candidate.area_m2, 3)
        postcode_match = bool(
            listing.postcode
            and candidate.postcode
            and listing.postcode.replace(" ", "").upper() == candidate.postcode.replace(" ", "").upper()
        )
        if postcode_match or (similarity >= 0.94 and price_close and area_close):
            candidate.last_seen_at = datetime.now(UTC)
            return candidate

    canonical = CanonicalProperty(
        dedup_key=key,
        normalized_address=normalized,
        postcode=listing.postcode,
        city=listing.city,
        rent_total=listing.rent_total,
        area_m2=listing.area_m2,
        bedrooms=listing.bedrooms,
    )
    db.add(canonical)
    db.flush()
    return canonical


def _difference_within(left: object, right: object, tolerance: float) -> bool:
    if left is None or right is None:
        return True
    return abs(float(left) - float(right)) <= tolerance
