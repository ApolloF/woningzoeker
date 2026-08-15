from decimal import Decimal

from app.schemas import ApplicantProfileData, NormalizedListing
from app.services.response import DeterministicDutchResponseProvider


def test_draft_mentions_real_listing_characteristics() -> None:
    listing = NormalizedListing(
        source_name="test",
        external_id="x",
        url="https://example.test/x",
        title="Havenstraat 5-C",
        address="Havenstraat 5-C",
        city="Groningen",
        area_m2=Decimal("37"),
        bedrooms=1,
    )
    profile = ApplicantProfileData(
        applicants=["Florian", "Sara"],
        current_city="Groningen",
        current_situation="Samenwonend.",
        applicant_details=["We werken en studeren in Groningen."],
        financial_wording="Financiële tekst.",
        guarantor_wording="Garantstellertekst.",
        lifestyle=["niet-rokers", "rustig"],
        desired_tenure="een woning voor langere tijd",
    )
    draft = DeterministicDutchResponseProvider().generate(listing, profile)
    assert "Havenstraat 5-C" in draft
    assert "37 m²" in draft
    assert "1 slaapkamer" in draft
    assert "Garantstellertekst" not in draft
