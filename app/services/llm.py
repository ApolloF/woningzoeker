from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings
from app.schemas import ApplicantProfileData, NormalizedListing
from app.services.response import DeterministicDutchResponseProvider


class LLMUnavailable(RuntimeError):
    pass


class ListingLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suitable_for_two: bool | None
    unusual_requirements: list[str] = Field(max_length=12)
    needs_review: bool
    explanation: str = Field(max_length=800)
    response_draft_nl: str = Field(max_length=2200)


def provider_json_schema() -> dict[str, Any]:
    """Return the strict structural subset accepted by both configured providers."""
    unsupported = {"default", "examples", "maxItems", "maxLength", "minItems", "minLength", "title"}

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in unsupported}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    schema = clean(ListingLLMResult.model_json_schema())
    assert isinstance(schema, dict)
    return schema


class ListingLLMProvider(Protocol):
    provider_name: str

    def analyze(
        self,
        listing: NormalizedListing,
        profile: ApplicantProfileData,
        deterministic_draft: str,
        model: str,
    ) -> ListingLLMResult: ...


@dataclass(frozen=True)
class LLMRun:
    draft: str
    result: ListingLLMResult | None
    provider: str
    model: str | None
    error: str | None = None


class BaseHTTPProvider:
    provider_name: str
    SENSITIVE_PROFILE_PATTERN = re.compile(
        r"(inkomen|salaris|loon|bruto|netto|bank|iban|borg|garant|vermogen|schuld|bsn|paspoort)",
        re.I,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def _input(
        cls,
        listing: NormalizedListing,
        profile: ApplicantProfileData,
        deterministic_draft: str,
    ) -> str:
        # Credentials, contact details, session data, URLs and the current street address stay excluded.
        # The explicitly managed financial and guarantor wording is intentionally included.
        safe_facts = [
            fact for fact in profile.applicant_details if not cls.SENSITIVE_PROFILE_PATTERN.search(fact)
        ]
        standard_message = profile.standard_message.strip()
        payload = {
            "listing": {
                "source": listing.source_name,
                "title": listing.title,
                "address": listing.address,
                "city": listing.city,
                "property_type": listing.property_type,
                "rent_total": str(listing.rent_total) if listing.rent_total else None,
                "area_m2": str(listing.area_m2) if listing.area_m2 else None,
                "bedrooms": listing.bedrooms,
                "rooms": listing.rooms,
                "availability": listing.availability_text,
                "description": (listing.description or "")[:6000],
            },
            "applicants": {
                "names": profile.applicants,
                "current_city": profile.current_city,
                "current_situation": profile.current_situation,
                "facts": safe_facts,
                "lifestyle": profile.lifestyle,
                "desired_tenure": profile.desired_tenure,
                "financial_wording": profile.financial_wording,
                "guarantor_wording": profile.guarantor_wording,
                "optional_base_message": standard_message or None,
                "sender_name": profile.sender_name,
                "perspective": profile.message_perspective,
                "rewrite_mode": profile.message_rewrite_mode,
                "always_include_financial": profile.always_include_financial,
                "always_include_guarantor": profile.always_include_guarantor,
                "required_message_points": profile.required_message_points,
            },
            "safe_fallback_draft": deterministic_draft,
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _instructions() -> str:
        return (
            "Analyseer uitsluitend de meegegeven openbare woningadvertentie. Bepaal of de woning "
            "volgens de tekst voor twee personen geschikt lijkt en markeer elke ongewone, "
            "juridische, financiële, betaalde of ontbrekende vereiste voor handmatige controle. "
            "Schrijf een korte natuurlijke Nederlandse interesse-reactie en noem het adres op een "
            "normale manier. Som geen vierkante meters, kamer- of slaapkameraantallen op en gebruik "
            "zulke cijfers niet als reden voor enthousiasme; dat klinkt onnatuurlijk. Controleer "
            "Nederlandse grammatica zorgvuldig, waaronder meervoud bij 'Sara en ik ... wonen'. "
            "Verzin of wijzig nooit inkomen, "
            "contracten, werkgevers, garanties, documenten of toestemming. Gebruik de expliciet "
            "meegegeven financial_wording en guarantor_wording exact wanneer hun always_include-vlag "
            "waar is. Neem required_message_points altijd letterlijk op. Houd sender_name en het "
            "perspectief ik/namens ons of wij consequent; Florian is de afzender. Bij rewrite_mode "
            "light mag alleen de woninggerichte opening licht wijzigen en blijft de persoonlijke "
            "basistekst verder intact. Bij adaptive mag de formulering natuurlijker worden gemaakt "
            "zonder feiten of perspectief te veranderen. Als "
            "optional_base_message is ingevuld, gebruik die dan als persoonlijke basis en pas hem "
            "alleen aan op de concrete woning. Geef alleen het "
            "gevraagde JSON-object terug."
        )

    @staticmethod
    def _validated(text: str) -> ListingLLMResult:
        try:
            result = ListingLLMResult.model_validate_json(text)
        except ValidationError as exc:
            raise LLMUnavailable("LLM output failed schema validation") from exc
        if not result.response_draft_nl.strip():
            raise LLMUnavailable("LLM returned an empty draft")
        return result


class OpenAIResponsesProvider(BaseHTTPProvider):
    provider_name = "openai"

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    def analyze(
        self,
        listing: NormalizedListing,
        profile: ApplicantProfileData,
        deterministic_draft: str,
        model: str,
    ) -> ListingLLMResult:
        body = {
            "model": model,
            "store": False,
            "instructions": self._instructions(),
            "input": self._input(listing, profile, deterministic_draft),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "rental_listing_analysis",
                    "strict": True,
                    "schema": provider_json_schema(),
                }
            },
        }
        with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
            response = client.post(
                f"{self.settings.openai_base_url.rstrip('/')}/responses",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json=body,
            )
            response.raise_for_status()
        data = response.json()
        output_text = next(
            (
                part.get("text", "")
                for item in data.get("output", [])
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            ),
            "",
        )
        return self._validated(output_text)


class AnthropicMessagesProvider(BaseHTTPProvider):
    provider_name = "anthropic"

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    def analyze(
        self,
        listing: NormalizedListing,
        profile: ApplicantProfileData,
        deterministic_draft: str,
        model: str,
    ) -> ListingLLMResult:
        body = {
            "model": model,
            "max_tokens": 1400,
            "system": self._instructions(),
            "messages": [{"role": "user", "content": self._input(listing, profile, deterministic_draft)}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": provider_json_schema(),
                }
            },
        }
        with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
            response = client.post(
                f"{self.settings.anthropic_base_url.rstrip('/')}/messages",
                headers={
                    "x-api-key": self.settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=body,
            )
            response.raise_for_status()
        data = response.json()
        output_text = next(
            (part.get("text", "") for part in data.get("content", []) if part.get("type") == "text"),
            "",
        )
        return self._validated(output_text)


class ListingLLMService:
    AMBIGUOUS_PATTERN = re.compile(
        r"(inkomenseis|garantsteller|diplomatenclausule|tijdelijk contract|"
        r"één persoon|1 persoon|koppel|samenwon|student|werkenden|document|verklaring)",
        re.I,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider: ListingLLMProvider | None
        if settings.llm_provider == "openai":
            self.provider = OpenAIResponsesProvider(settings)
        elif settings.llm_provider == "anthropic":
            self.provider = AnthropicMessagesProvider(settings)
        else:
            self.provider = None

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def generate(
        self,
        listing: NormalizedListing,
        profile: ApplicantProfileData,
        deterministic_draft: str,
    ) -> LLMRun:
        if not self.provider:
            return LLMRun(deterministic_draft, None, "disabled", None)
        model = self._route_model(listing)
        try:
            result = self.provider.analyze(listing, profile, deterministic_draft, model)
            draft = self._controlled_draft(result.response_draft_nl, deterministic_draft, profile)
            return LLMRun(draft, result, self.provider.provider_name, model)
        except (httpx.HTTPError, LLMUnavailable, ValueError, TypeError) as exc:
            return LLMRun(
                deterministic_draft,
                None,
                self.provider.provider_name,
                model,
                f"{type(exc).__name__}: {str(exc)[:300]}",
            )

    @staticmethod
    def _controlled_draft(llm_draft: str, deterministic_draft: str, profile: ApplicantProfileData) -> str:
        if profile.message_rewrite_mode == "exact":
            return DeterministicDutchResponseProvider.polish_dutch(deterministic_draft)
        draft = llm_draft.strip()
        required = list(profile.required_message_points)
        if profile.always_include_financial:
            required.append(profile.financial_wording)
        if profile.always_include_guarantor:
            required.append(profile.guarantor_wording)
        normalized_draft = " ".join(draft.casefold().split())
        for item in required:
            item = item.strip()
            normalized_item = " ".join(item.casefold().split())
            if item and normalized_item not in normalized_draft:
                draft = f"{draft}\n\n{item}"
                normalized_draft = f"{normalized_draft} {normalized_item}"
        return DeterministicDutchResponseProvider.polish_dutch(draft)

    def _route_model(self, listing: NormalizedListing) -> str:
        cheap, standard, escalation = self.settings.llm_models()
        description = listing.description or ""
        if self.AMBIGUOUS_PATTERN.search(description):
            return escalation
        if not listing.property_type or listing.area_m2 is None or len(description) > 2500:
            return standard
        return cheap
