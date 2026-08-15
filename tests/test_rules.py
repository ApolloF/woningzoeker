from datetime import UTC, datetime, timedelta
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


def test_fast_mode_accepts_soft_margin_and_small_area_while_careful_mode_does_not() -> None:
    candidate = listing(rent_total=Decimal("1725"), area_m2=Decimal("32"))
    assert (
        RuleEngine().evaluate(
            candidate,
            Criteria(auto_react_aggressiveness="fast", auto_react_min_score=60),
        ).decision
        is Decision.AUTO_REACT
    )
    assert (
        RuleEngine().evaluate(
            candidate,
            Criteria(auto_react_aggressiveness="careful", auto_react_min_score=60),
        ).decision
        is Decision.REVIEW
    )


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


def test_fast_mode_still_blocks_income_above_configured_limit() -> None:
    result = RuleEngine().evaluate(
        listing(description="Minimaal bruto maandinkomen van € 5.000 vereist."),
        Criteria(
            max_required_monthly_income=Decimal("4500"),
            auto_react_aggressiveness="fast",
        ),
    )
    assert result.decision is Decision.REVIEW


def test_room_and_expired_listing_are_ignored() -> None:
    engine = RuleEngine()
    assert engine.evaluate(listing(property_type="kamer"), Criteria()).decision is Decision.IGNORE
    assert engine.evaluate(listing(is_available=False), Criteria()).decision is Decision.IGNORE


def test_old_source_timestamp_is_ignored_but_unknown_timestamp_is_allowed() -> None:
    old = datetime.now(UTC) - timedelta(minutes=181)
    criteria = Criteria(max_listing_age_minutes=180)
    result = RuleEngine().evaluate(listing(published_at=old), criteria)
    assert result.decision is Decision.IGNORE
    assert result.rules[0].rule == "listing_age"
    assert RuleEngine().evaluate(listing(published_at=None), criteria).decision is Decision.AUTO_REACT


def test_auto_react_age_is_separate_from_listing_visibility() -> None:
    published = datetime.now(UTC) - timedelta(minutes=31)
    criteria = Criteria(max_listing_age_minutes=180, max_auto_react_age_minutes=30)
    result = RuleEngine().evaluate(listing(published_at=published), criteria)
    assert result.decision is Decision.REVIEW
    assert any(rule.rule == "auto_react_age" for rule in result.rules)
