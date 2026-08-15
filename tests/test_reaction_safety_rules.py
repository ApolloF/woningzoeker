from __future__ import annotations

from decimal import Decimal

from app.models import Decision
from app.schemas import Criteria, NormalizedListing
from app.services.rules import RuleEngine


def listing(description: str) -> NormalizedListing:
    return NormalizedListing(
        source_name="test",
        external_id="1",
        url="https://example.test/1",
        title="Woning",
        address="Teststraat 1",
        city="Groningen",
        property_type="appartement",
        rent_total=Decimal("1200"),
        area_m2=Decimal("60"),
        description=description,
    )


def test_explicit_one_person_listing_is_ignored() -> None:
    assert (
        RuleEngine().evaluate(listing("Uitsluitend geschikt voor één persoon."), Criteria()).decision
        is Decision.IGNORE
    )


def test_income_and_document_requirements_prevent_auto_reaction() -> None:
    result = RuleEngine().evaluate(
        listing("Een bruto maandinkomen en identiteitsbewijs zijn vereist."), Criteria()
    )
    assert result.decision is Decision.REVIEW


def test_home_swap_is_ignored_by_default() -> None:
    assert (
        RuleEngine().evaluate(listing("Deze woning wordt aangeboden voor woningruil."), Criteria()).decision
        is Decision.IGNORE
    )
