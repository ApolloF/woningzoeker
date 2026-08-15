from decimal import Decimal
from pathlib import Path

from app.adapters.one_two_three_wonen import OneTwoThreeWonenAdapter
from app.adapters.woldring import WoldringAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def test_123wonen_parser_extracts_and_marks_expired() -> None:
    listings = OneTwoThreeWonenAdapter().parse((FIXTURES / "123wonen.html").read_text())
    assert len(listings) == 2
    assert listings[0].external_id == "bergstraat-5394-2"
    assert listings[0].address == "Bergstraat 63"
    assert listings[0].rent_total == Decimal("1022.00")
    assert listings[0].area_m2 == Decimal("81")
    assert listings[0].bedrooms == 1
    assert listings[0].is_available is True
    assert listings[1].is_available is False


def test_woldring_parser_includes_service_costs_in_total() -> None:
    listings = WoldringAdapter().parse((FIXTURES / "woldring.html").read_text())
    assert len(listings) == 2
    assert listings[0].external_id == "31387"
    assert listings[0].rent_base == Decimal("1102.05")
    assert listings[0].service_costs == Decimal("87.95")
    assert listings[0].rent_total == Decimal("1190.00")
    assert listings[0].area_m2 == Decimal("37.00")
    assert listings[1].is_available is False
