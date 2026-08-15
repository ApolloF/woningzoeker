from decimal import Decimal

from app.models import Decision
from app.schemas import Criteria, NormalizedListing
from app.services.rules import RuleEngine


def listing(**changes: object) -> NormalizedListing:
    values: dict[str, object] = {
        "source_name": "test",
        "external_id": "1",
        "url": "https://example.test/home/1",
        "title": "Teststraat 1",
        "address": "Teststraat 1",
        "city": "Groningen",
        "property_type": "appartement",
        "rent_total": Decimal("1400"),
        "area_m2": Decimal("55"),
    }
    values.update(changes)
    return NormalizedListing.model_validate(values)


def test_strong_listing_is_auto_react_candidate() -> None:
    result = RuleEngine().evaluate(listing(), Criteria())
    assert result.decision is Decision.AUTO_REACT
    assert result.score >= 75


def test_slightly_expensive_listing_goes_to_review_not_ignore() -> None:
    assert RuleEngine().evaluate(listing(rent_total=Decimal("1725")), Criteria()).decision is Decision.REVIEW


def test_missing_data_goes_to_review() -> None:
    assert (
        RuleEngine().evaluate(listing(area_m2=None, property_type=None), Criteria()).decision
        is Decision.REVIEW
    )


def test_room_and_expired_listing_are_ignored() -> None:
    engine = RuleEngine()
    assert engine.evaluate(listing(property_type="kamer"), Criteria()).decision is Decision.IGNORE
    assert engine.evaluate(listing(is_available=False), Criteria()).decision is Decision.IGNORE
