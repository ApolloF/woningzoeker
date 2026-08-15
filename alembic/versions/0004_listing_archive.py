"""Add reversible listing archiving.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_index("ix_listings_archived_at", "listings", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_listings_archived_at", table_name="listings")
    op.drop_column("listings", "archived_at")
