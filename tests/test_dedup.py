from decimal import Decimal

from app.schemas import NormalizedListing
from app.services.dedup import make_dedup_key, normalize_address


def item(source: str, address: str) -> NormalizedListing:
    return NormalizedListing(
        source_name=source,
        external_id=source,
        url=f"https://example.test/{source}",
        title=address,
        address=address,
        postcode="9712 AB",
        city="Groningen",
        rent_total=Decimal("1200"),
        area_m2=Decimal("50"),
    )


def test_same_property_on_two_sources_has_same_key() -> None:
    assert make_dedup_key(item("a", "Nieuwe Boteringestraat 10-A")) == make_dedup_key(
        item("b", "Nieuwe Boteringestraat 10 A")
    )


def test_address_normalization_is_stable() -> None:
    assert normalize_address("M.L. Kingstraat 4, 9712 AB") == "M L KINGSTRAAT 4"
