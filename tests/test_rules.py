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


def test_hard_income_requirement_is_configurable_review_criterion() -> None:
    result = RuleEngine().evaluate(
        listing(description="Minimaal bruto maandinkomen van € 5.000 vereist."),
        Criteria(max_required_monthly_income=Decimal("4500")),
    )
    assert result.decision is Decision.REVIEW
    assert any(rule.rule == "hard_income_requirement" for rule in result.rules)

    disabled = RuleEngine().evaluate(
        listing(description="Minimaal bruto maandinkomen van € 5.000 vereist."),
        Criteria(review_hard_income_requirements=False),
    )
    assert disabled.decision is Decision.AUTO_REACT


def test_room_and_expired_listing_are_ignored() -> None:
    engine = RuleEngine()
    assert engine.evaluate(listing(property_type="kamer"), Criteria()).decision is Decision.IGNORE
    assert engine.evaluate(listing(is_available=False), Criteria()).decision is Decision.IGNORE
