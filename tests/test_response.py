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
    assert "Samenwonend." in draft
    assert "Garantstellertekst" in draft
    assert "Mijn naam is Florian" in draft
    assert draft.rstrip().endswith("Florian")


def test_optional_standard_message_is_used_as_the_draft_base() -> None:
    listing = NormalizedListing(
        source_name="test",
        external_id="x",
        url="https://example.test/x",
        title="Havenstraat",
        address="Havenstraat",
        city="Groningen",
    )
    profile = ApplicantProfileData(
        applicants=["Florian"],
        current_city="Groningen",
        current_situation="Samenwonend.",
        applicant_details=["Werkt in Groningen."],
        financial_wording="Financieel.",
        guarantor_wording="Garant.",
        lifestyle=["rustig"],
        desired_tenure="lang wonen",
        standard_message="Wij zijn een rustig stel.",
    )
    draft = DeterministicDutchResponseProvider().generate(listing, profile)
    assert "Wij zijn een rustig stel." in draft
    assert "Garant." in draft
    assert draft.count("Beste verhuurder") == 0
    assert draft.count("Met vriendelijke groet") == 0


def test_complete_standard_letter_is_not_wrapped_with_duplicate_greeting_or_closing() -> None:
    listing = NormalizedListing(
        source_name="test",
        external_id="x",
        url="https://example.test/x",
        title="Kleine Bergstraat",
        address="Kleine Bergstraat",
        city="Groningen",
    )
    letter = (
        "Beste verhuurder,\n\nZojuist zag ik jullie woning.\n\n"
        "Financieel in orde. Garantsteller beschikbaar.\n\n"
        "Met vriendelijke groet,\n\nFlorian Greeven & Sara Hutchinson"
    )
    profile = ApplicantProfileData(
        applicants=["Florian Greeven", "Sara Hutchinson"],
        current_city="Groningen",
        current_situation="We wonen samen.",
        applicant_details=["We werken in Groningen."],
        financial_wording="Financieel in orde.",
        guarantor_wording="Garantsteller beschikbaar.",
        lifestyle=["rustig"],
        desired_tenure="lang wonen",
        standard_message=letter,
    )
    draft = DeterministicDutchResponseProvider().generate(listing, profile)
    assert draft == letter
    assert draft.count("Beste verhuurder") == 1
    assert draft.count("Met vriendelijke groet") == 1
