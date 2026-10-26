"""Widgets can be dragged anywhere on the main screen: where each one was left

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("widgets", sa.Column("pos", JSONB()))


def downgrade() -> None:
    op.drop_column("widgets", "pos")
