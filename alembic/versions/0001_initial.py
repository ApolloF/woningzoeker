"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_item_count", sa.Integer()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_source_configs_name", "source_configs", ["name"])

    op.create_table(
        "canonical_properties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("normalized_address", sa.String(300), nullable=False),
        sa.Column("postcode", sa.String(16)),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("house_number", sa.String(32)),
        sa.Column("rent_total", sa.Numeric(10, 2)),
        sa.Column("area_m2", sa.Numeric(8, 2)),
        sa.Column("bedrooms", sa.Integer()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedup_key"),
    )
    op.create_index("ix_canonical_properties_dedup_key", "canonical_properties", ["dedup_key"])
    op.create_index(
        "ix_canonical_properties_normalized_address",
        "canonical_properties",
        ["normalized_address"],
    )
    op.create_index("ix_canonical_properties_postcode", "canonical_properties", ["postcode"])
    op.create_index("ix_canonical_properties_city", "canonical_properties", ["city"])

    op.create_table(
        "search_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "applicant_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("source_configs.id"), nullable=False),
        sa.Column(
            "canonical_property_id",
            sa.Integer(),
            sa.ForeignKey("canonical_properties.id"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("postcode", sa.String(16)),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("property_type", sa.String(80)),
        sa.Column("rent_base", sa.Numeric(10, 2)),
        sa.Column("service_costs", sa.Numeric(10, 2)),
        sa.Column("rent_total", sa.Numeric(10, 2)),
        sa.Column("area_m2", sa.Numeric(8, 2)),
        sa.Column("bedrooms", sa.Integer()),
        sa.Column("rooms", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("availability_text", sa.String(255)),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("rule_results", sa.JSON(), nullable=False),
        sa.Column("reasoning_summary", sa.Text()),
        sa.Column("response_draft", sa.Text()),
        sa.Column("response_sent", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
    )
    op.create_index("ix_listings_source_id", "listings", ["source_id"])
    op.create_index("ix_listings_canonical_property_id", "listings", ["canonical_property_id"])
    op.create_index("ix_listing_decision_first_seen", "listings", ["decision", "first_seen_at"])

    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("source_configs.id"), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "label", name="uq_credential_source_label"),
    )
    op.create_index("ix_credentials_source_id", "credentials", ["source_id"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("exact_text", sa.Text()),
        sa.Column("submitted_fields", sa.JSON(), nullable=False),
        sa.Column("browser_result", sa.JSON(), nullable=False),
        sa.Column("before_screenshot", sa.Text()),
        sa.Column("after_screenshot", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_submissions_listing_id", "submissions", ["listing_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id")),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("source_configs.id")),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_listing_id", "audit_events", ["listing_id"])
    op.create_index("ix_audit_events_source_id", "audit_events", ["source_id"])
    op.create_index("ix_audit_created_type", "audit_events", ["created_at", "event_type"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("submissions")
    op.drop_table("credentials")
    op.drop_table("listings")
    op.drop_table("applicant_profiles")
    op.drop_table("search_configs")
    op.drop_table("canonical_properties")
    op.drop_table("source_configs")
