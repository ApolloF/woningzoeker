"""Store the source-provided listing publication time.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_listings_published_at", "listings", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_listings_published_at", table_name="listings")
    op.drop_column("listings", "published_at")
