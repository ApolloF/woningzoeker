from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.models import Decision


class NormalizedListing(BaseModel):
    source_name: str
    external_id: str
    url: HttpUrl
    title: str
    address: str
    postcode: str | None = None
    city: str
    property_type: str | None = None
    rent_base: Decimal | None = None
    service_costs: Decimal | None = None
    rent_total: Decimal | None = None
    area_m2: Decimal | None = None
    bedrooms: int | None = None
    rooms: int | None = None
    description: str | None = None
    availability_text: str | None = None
    is_available: bool = True
    image_url: HttpUrl | None = None
    published_at: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rent_total", mode="after")
    @classmethod
    def total_must_be_positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("rent_total must be positive")
        return value


class Criteria(BaseModel):
    accepted_cities: list[str] = Field(default_factory=lambda: ["Groningen", "Haren"])
    allow_shared_rooms: bool = False
    allow_home_swap: bool = False
    min_area_m2: Decimal = Decimal("35")
    target_total_monthly: Decimal = Decimal("1650")
    soft_price_margin: Decimal = Decimal("150")
    accepted_property_types: list[str] = Field(
        default_factory=lambda: [
            "appartement",
            "studio",
            "huis",
            "woning",
            "tussenwoning",
            "bovenwoning",
            "benedenwoning",
            "eengezinswoning",
        ]
    )
    prefer_quiet: bool = True
    suitable_for_two_required: bool = True
    review_hard_income_requirements: bool = True
    max_required_monthly_income: Decimal | None = Field(default=None, ge=0)
    max_listing_age_minutes: int | None = Field(default=180, ge=5, le=10080)
    max_auto_react_age_minutes: int | None = Field(default=180, ge=1, le=10080)
    auto_react_aggressiveness: Literal["careful", "balanced", "fast"] = "balanced"
    auto_react_min_score: int = Field(default=75, ge=40, le=100)
    auto_accept_legal_confirmations: bool = False
    telegram_listing_filter: Literal["all", "auto_react", "score", "auto_react_or_score", "off"] = (
        "auto_react_or_score"
    )
    telegram_min_score: int = Field(default=75, ge=0, le=100)
    telegram_notify_assistance: bool = True
    telegram_notify_source_failures: bool = True
    telegram_notify_sent_reactions: bool = True


class RuleResult(BaseModel):
    rule: str
    outcome: str
    detail: str
    score_delta: int = 0


class Evaluation(BaseModel):
    decision: Decision
    score: int = Field(ge=0, le=100)
    rules: list[RuleResult]
    summary: str


class ApplicantProfileData(BaseModel):
    applicants: list[str]
    current_city: str
    current_situation: str
    applicant_details: list[str]
    financial_wording: str
    guarantor_wording: str
    lifestyle: list[str]
    desired_tenure: str
    standard_message: str = Field(default="", max_length=1800)
    sender_name: str = Field(default="Florian", min_length=1, max_length=100)
    message_perspective: Literal["sender", "joint"] = "sender"
    message_rewrite_mode: Literal["exact", "light", "adaptive"] = "exact"
    always_include_financial: bool = True
    always_include_guarantor: bool = True
    required_message_points: list[str] = Field(default_factory=list, max_length=12)


class PrivateContactData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=150)
    initials: str = Field(min_length=1, max_length=20)
    email: str = Field(
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    phone: str = Field(min_length=6, max_length=40)
    address: str = Field(min_length=1, max_length=200)
    house_number: str = Field(min_length=1, max_length=30)
    city: str = Field(min_length=1, max_length=100)


class SourceCredentialData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=500)
    storage_state: dict[str, Any] | None = None

    @model_validator(mode="after")
    def has_a_complete_auth_method(self) -> SourceCredentialData:
        if bool(self.username) != bool(self.password):
            raise ValueError("username and password must be provided together")
        if not (self.storage_state or (self.username and self.password)):
            raise ValueError("credentials or a browser session are required")
        return self


class LLMClassification(BaseModel):
    suitable_for_two: bool | None
    unusual_requirements: list[str]
    needs_review: bool
    explanation: str
