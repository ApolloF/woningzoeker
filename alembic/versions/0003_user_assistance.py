"""Add persistent human assistance queue.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistance_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("user_note", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["source_configs.id"]),
        sa.UniqueConstraint("submission_id"),
    )
    op.create_index(
        "ix_assistance_requests_submission_id", "assistance_requests", ["submission_id"]
    )
    op.create_index("ix_assistance_requests_listing_id", "assistance_requests", ["listing_id"])
    op.create_index("ix_assistance_requests_source_id", "assistance_requests", ["source_id"])
    op.create_index(
        "ix_assistance_state_created", "assistance_requests", ["state", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("assistance_requests")
