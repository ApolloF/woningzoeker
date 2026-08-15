from decimal import Decimal

from app.schemas import ApplicantProfileData, NormalizedListing
from app.services.response import DeterministicDutchResponseProvider


def test_draft_mentions_address_without_ai_like_number_recital() -> None:
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
    assert "37 m²" not in draft
    assert "1 slaapkamer" not in draft
    assert "Samenwonend." in draft
    assert "Garantstellertekst" in draft
    assert "Mijn naam is Florian" in draft
    assert draft.rstrip().endswith("Florian")


def test_narrow_dutch_polish_fixes_plural_and_removes_number_recital() -> None:
    draft = DeterministicDutchResponseProvider.polish_dutch(
        "Zojuist zag ik het appartement aan Entensgang 4, met 46 m² en 3 kamers, "
        "en ik was meteen enthousiast!\n\nWaar Sara en ik samen woon.\n\n"
        "Vooral de woonoppervlakte van 46 m² en de 3 kamers spreken ons aan."
    )
    assert "met 46 m² en 3 kamers" not in draft
    assert "woonoppervlakte" not in draft
    assert "Sara en ik samen wonen" in draft
    assert "Entensgang 4" in draft


def test_word_limit_keeps_required_guarantor_and_complete_sentences() -> None:
    profile = ApplicantProfileData(
        applicants=["Florian", "Sara"],
        current_city="Groningen",
        current_situation="We wonen samen.",
        applicant_details=["We werken in Groningen."],
        financial_wording="Ons gezamenlijke inkomen is stabiel.",
        guarantor_wording="Mijn oom kan altijd als garantsteller optreden.",
        lifestyle=["rustig"],
        desired_tenure="lang wonen",
    )
    draft = (
        "Beste verhuurder,\n\nWij reageren graag op deze woning. Dit is een extra lange "
        "optionele toelichting die mag vervallen.\n\nOns gezamenlijke inkomen is stabiel.\n\n"
        "Mijn oom kan altijd als garantsteller optreden.\n\nMet vriendelijke groet,\n\nFlorian"
    )
    limited = DeterministicDutchResponseProvider.apply_word_limit(draft, 30, profile)
    assert len(limited.split()) <= 30
    assert profile.guarantor_wording in limited
    assert profile.financial_wording in limited
    assert limited.endswith("Florian")


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
