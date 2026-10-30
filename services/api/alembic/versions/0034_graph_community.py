"""knowledge graph: each entity's community (its cluster, from the nightly Leiden pass)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("community", sa.Integer()))


def downgrade() -> None:
    op.drop_column("entities", "community")
