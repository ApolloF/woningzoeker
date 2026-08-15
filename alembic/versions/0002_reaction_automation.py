"""Add guarded reaction automation state.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "private_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("credentials", sa.Column("last_verified_at", sa.DateTime(timezone=True)))
    op.add_column("credentials", sa.Column("last_error", sa.String(500)))
    op.add_column(
        "submissions",
        sa.Column("canonical_property_id", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE submissions SET canonical_property_id = listings.canonical_property_id "
        "FROM listings WHERE submissions.listing_id = listings.id"
    )
    op.alter_column("submissions", "canonical_property_id", nullable=False)
    op.create_foreign_key(
        "fk_submission_canonical_property",
        "submissions",
        "canonical_properties",
        ["canonical_property_id"],
        ["id"],
    )
    op.create_index(
        "ix_submissions_canonical_property_id",
        "submissions",
        ["canonical_property_id"],
    )
    op.create_unique_constraint(
        "uq_submission_canonical_property",
        "submissions",
        ["canonical_property_id"],
    )
    op.add_column(
        "submissions",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("submissions", sa.Column("error_code", sa.String(80)))
    op.add_column("submissions", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE submissions SET updated_at = created_at")
    op.alter_column("submissions", "updated_at", nullable=False)
    op.add_column("submissions", sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE source_configs SET poll_interval_seconds = 60 "
        "WHERE poll_interval_seconds > 60"
    )


def downgrade() -> None:
    op.drop_column("submissions", "last_attempt_at")
    op.drop_column("submissions", "updated_at")
    op.drop_column("submissions", "error_code")
    op.drop_column("submissions", "attempt_count")
    op.drop_constraint("uq_submission_canonical_property", "submissions", type_="unique")
    op.drop_index("ix_submissions_canonical_property_id", table_name="submissions")
    op.drop_constraint("fk_submission_canonical_property", "submissions", type_="foreignkey")
    op.drop_column("submissions", "canonical_property_id")
    op.drop_column("credentials", "last_error")
    op.drop_column("credentials", "last_verified_at")
    op.drop_table("private_contacts")
