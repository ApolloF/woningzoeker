import json
from typing import Any

from app.config import Settings
from app.models import Decision
from app.schemas import ApplicantProfileData, NormalizedListing, RuleResult
from app.services.llm import (
    BaseHTTPProvider,
    ListingLLMResult,
    ListingLLMService,
    LLMRun,
    provider_json_schema,
)
from app.services.pipeline import Pipeline
from app.services.response import DeterministicDutchResponseProvider


def profile() -> ApplicantProfileData:
    return ApplicantProfileData(
        applicants=["Florian", "Sara"],
        current_city="Groningen",
        current_situation="We wonen samen.",
        applicant_details=["Florian studeert.", "Sara werkt."],
        financial_wording="Gezamenlijk inkomen circa €4.500 per maand.",
        guarantor_wording="Een draagkrachtige garantsteller is beschikbaar.",
        lifestyle=["niet-rokers"],
        desired_tenure="lang samenwonen",
    )


def listing() -> NormalizedListing:
    return NormalizedListing(
        source_name="test",
        external_id="1",
        url="https://example.com/1",
        title="Havenstraat 5, Groningen",
        address="Havenstraat 5",
        city="Groningen",
        property_type="appartement",
        rent_total=1200,
        area_m2=50,
        bedrooms=1,
        description="Rustig appartement geschikt voor een koppel.",
    )


def test_disabled_llm_uses_deterministic_fallback() -> None:
    settings = Settings(_env_file=None, llm_provider="disabled")
    fallback = DeterministicDutchResponseProvider().generate(listing(), profile())
    run = ListingLLMService(settings).generate(listing(), profile(), fallback)
    assert run.provider == "disabled"
    assert run.draft == fallback
    assert run.result is None


def test_llm_input_only_includes_explicitly_managed_financial_fields() -> None:
    sensitive_profile = profile().model_copy(
        update={"applicant_details": ["Florian studeert.", "Netto inkomen is 3500 euro."]}
    )
    raw = BaseHTTPProvider._input(listing(), sensitive_profile, "Veilig concept")
    payload = json.loads(raw)
    applicants = payload["applicants"]
    assert applicants["current_city"] == sensitive_profile.current_city
    assert applicants["current_situation"] == sensitive_profile.current_situation
    assert applicants["facts"] == ["Florian studeert."]
    assert applicants["financial_wording"] == sensitive_profile.financial_wording
    assert applicants["guarantor_wording"] == sensitive_profile.guarantor_wording
    assert str(listing().url) not in raw


def test_llm_receives_safe_optional_base_message() -> None:
    raw = BaseHTTPProvider._input(
        listing(),
        profile().model_copy(update={"standard_message": "Wij zijn een rustig stel."}),
        "Veilig concept",
    )
    assert json.loads(raw)["applicants"]["optional_base_message"] == "Wij zijn een rustig stel."


def test_provider_schema_only_uses_supported_structural_keywords() -> None:
    unsupported = {"default", "examples", "maxItems", "maxLength", "minItems", "minLength", "title"}

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()

    schema = provider_json_schema()
    assert unsupported.isdisjoint(keys(schema))
    assert schema["additionalProperties"] is False


def test_llm_can_only_downgrade_auto_react() -> None:
    run = LLMRun(
        draft="Concept",
        result=ListingLLMResult(
            suitable_for_two=None,
            unusual_requirements=["inkomenseis onduidelijk"],
            needs_review=True,
            explanation="De advertentie noemt een onduidelijke inkomenseis.",
            response_draft_nl="Concept",
        ),
        provider="openai",
        model="test-model",
    )
    decision, rules, summary = Pipeline._apply_llm_safety(
        Decision.AUTO_REACT,
        [RuleResult(rule="base", outcome="pass", detail="ok")],
        "Deterministisch passend.",
        run,
    )
    assert decision is Decision.REVIEW
    assert rules[-1].rule == "llm_analysis"
    assert "Handmatige controle" in summary


def test_cached_llm_review_cannot_flip_to_auto_react() -> None:
    decision, rules, summary = Pipeline._apply_cached_llm_safety(
        Decision.AUTO_REACT,
        [RuleResult(rule="base", outcome="pass", detail="ok")],
        "Deterministisch passend.",
        {
            "provider": "openai",
            "error": None,
            "needs_review": True,
            "explanation": "De advertentie bevat een onduidelijke voorwaarde.",
            "unusual_requirements": [],
        },
    )
    assert decision is Decision.REVIEW
    assert rules[-1].outcome == "review"
    assert "opgeslagen" in summary.lower()


def test_missing_cached_llm_result_fails_closed() -> None:
    decision, rules, _ = Pipeline._apply_cached_llm_safety(
        Decision.AUTO_REACT,
        [],
        "Deterministisch passend.",
        None,
    )
    assert decision is Decision.REVIEW
    assert rules[-1].rule == "llm_analysis"


def test_exact_mode_keeps_own_draft_and_light_mode_enforces_guarantor() -> None:
    own = "Mijn eigen bericht.\n\nEen garantsteller is altijd beschikbaar."
    exact = profile().model_copy(update={"message_rewrite_mode": "exact"})
    assert ListingLLMService._controlled_draft("Volledig herschreven.", own, exact) == own

    light = profile().model_copy(update={"message_rewrite_mode": "light"})
    controlled = ListingLLMService._controlled_draft("Licht aangepast.", own, light)
    assert controlled.startswith("Licht aangepast.")
    assert light.guarantor_wording in controlled
