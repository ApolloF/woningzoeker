from decimal import Decimal
from pathlib import Path

from app.adapters import ALL_ADAPTERS
from app.adapters.bulten import BultenVastgoedAdapter
from app.adapters.campus_groningen import CampusGroningenAdapter
from app.adapters.funda import FundaRentalAdapter
from app.adapters.gruno import GrunoVastgoedAdapter
from app.adapters.huurwoningen import HuurwoningenAdapter
from app.adapters.maxx import MaxxGroningenAdapter
from app.adapters.pandomo import PandomoAdapter
from app.adapters.pararius import ParariusAdapter
from app.adapters.rotsvast import RotsvastGroningenAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_requested_sources_are_registered() -> None:
    names = {adapter.source_name for adapter in ALL_ADAPTERS}
    assert names == {
        "123wonen_groningen",
        "bulten_vastgoed",
        "campus_groningen",
        "funda_rentals",
        "gruno_vastgoed",
        "huurwoningen",
        "maxx_groningen",
        "pandomo",
        "pararius",
        "rotsvast_groningen",
        "woldring",
    }


def test_pararius_and_huurwoningen_shared_markup() -> None:
    html = fixture("pararius.html")
    for adapter in (ParariusAdapter(), HuurwoningenAdapter()):
        listing = adapter.parse(html)[0]
        assert listing.address == "Entensgang 4"
        assert listing.postcode == "9711 NE"
        assert listing.rent_total == 1450
        assert listing.area_m2 == 46


def test_funda_parser() -> None:
    listing = FundaRentalAdapter().parse(fixture("funda.html"))[0]
    assert listing.external_id == "44412406"
    assert listing.address == "Gedempte Kattendiep 21-c"
    assert listing.bedrooms == 2
    assert listing.rent_total == 1475


def test_maxx_availability_parser() -> None:
    listings = MaxxGroningenAdapter().parse(fixture("maxx.html"))
    assert listings[0].external_id == "24244"
    assert listings[0].is_available is True
    assert listings[0].rent_total == Decimal("950.66")
    assert listings[1].is_available is False


def test_bulten_adapter_parses_current_homesearch_offers() -> None:
    listings = BultenVastgoedAdapter().parse(fixture("bulten.html"))
    assert listings[0].external_id == "833963902"
    assert listings[0].address == "Oosterhamrikkade"
    assert listings[0].area_m2 == 60
    assert listings[0].is_available is True
    assert listings[1].is_available is False


def test_local_agency_parsers() -> None:
    gruno = GrunoVastgoedAdapter().parse(fixture("gruno.html"))[0]
    rotsvast = RotsvastGroningenAdapter().parse(fixture("rotsvast.html"))[0]
    pandomo = PandomoAdapter().parse(fixture("pandomo.html"))[0]
    assert (gruno.address, gruno.rooms, gruno.rent_total) == ("Helperpark 302-25", 1, 1180)
    assert (rotsvast.city, rotsvast.bedrooms, rotsvast.rent_total) == (
        "Groningen",
        3,
        2240,
    )
    assert (pandomo.address, pandomo.is_available, pandomo.area_m2) == (
        "Naberstraat 3",
        True,
        101,
    )


def test_campus_detail_closes_stale_overview_listing() -> None:
    adapter = CampusGroningenAdapter()
    overview = adapter.parse(fixture("campus.html"))[0]
    enriched = adapter._enrich(overview, fixture("campus_detail.html"))
    assert enriched.property_type == "appartement"
    assert enriched.area_m2 == 48
    assert enriched.bedrooms == 1
    assert enriched.is_available is False
