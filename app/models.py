from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Decision(enum.StrEnum):
    AUTO_REACT = "AUTO_REACT"
    REVIEW = "REVIEW"
    IGNORE = "IGNORE"


class SourceMode(enum.StrEnum):
    MONITOR_ONLY = "MONITOR_ONLY"
    DRAFT_ONLY = "DRAFT_ONLY"
    AUTO_REACT = "AUTO_REACT"


class SubmissionState(enum.StrEnum):
    INTENDED = "INTENDED"
    PREPARING = "PREPARING"
    DRY_RUN_STOPPED = "DRY_RUN_STOPPED"
    SENT = "SENT"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class SourceConfig(Base):
    __tablename__ = "source_configs"
    __table_args__ = (
        UniqueConstraint("name"),
        Index("ix_source_configs_name", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    mode: Mapped[str] = mapped_column(String(30), default=SourceMode.DRAFT_ONLY.value)
    response_word_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_item_count: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    listings: Mapped[list[Listing]] = relationship(back_populates="source")


class CanonicalProperty(Base):
    __tablename__ = "canonical_properties"
    __table_args__ = (
        UniqueConstraint("dedup_key"),
        Index("ix_canonical_properties_dedup_key", "dedup_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(64))
    normalized_address: Mapped[str] = mapped_column(String(300), index=True)
    postcode: Mapped[str | None] = mapped_column(String(16), index=True)
    city: Mapped[str] = mapped_column(String(120), index=True)
    house_number: Mapped[str | None] = mapped_column(String(32))
    rent_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listings: Mapped[list[Listing]] = relationship(back_populates="canonical_property")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        Index("ix_listing_decision_first_seen", "decision", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_configs.id"), index=True)
    canonical_property_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_properties.id"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(400))
    address: Mapped[str] = mapped_column(String(300))
    postcode: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str] = mapped_column(String(120))
    property_type: Mapped[str | None] = mapped_column(String(80))
    rent_base: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    service_costs: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    rent_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    rooms: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    availability_text: Mapped[str | None] = mapped_column(String(255))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    image_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(30), default=Decision.REVIEW.value)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    response_draft: Mapped[str | None] = mapped_column(Text)
    response_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    source: Mapped[SourceConfig] = relationship(back_populates="listings")
    canonical_property: Mapped[CanonicalProperty] = relationship(back_populates="listings")
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="listing")


class SearchConfig(Base):
    __tablename__ = "search_configs"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApplicantProfile(Base):
    __tablename__ = "applicant_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PrivateContact(Base):
    __tablename__ = "private_contacts"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("source_id", "label", name="uq_credential_source_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_configs.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("canonical_property_id", name="uq_submission_canonical_property"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    canonical_property_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_properties.id"), index=True
    )
    state: Mapped[str] = mapped_column(String(30))
    exact_text: Mapped[str | None] = mapped_column(Text)
    submitted_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    browser_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    before_screenshot: Mapped[str | None] = mapped_column(Text)
    after_screenshot: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_created_type", "created_at", "event_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source_configs.id"), index=True)
    summary: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing | None] = relationship(back_populates="audit_events")
