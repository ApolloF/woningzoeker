from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

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

    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=500)
    storage_state: dict[str, Any] | None = None


class LLMClassification(BaseModel):
    suitable_for_two: bool | None
    unusual_requirements: list[str]
    needs_review: bool
    explanation: str
