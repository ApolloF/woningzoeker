"""Add an optional response word limit per source.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_configs", sa.Column("response_word_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("source_configs", "response_word_limit")
