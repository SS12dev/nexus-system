"""Widgets: a library of the ones you put away, and a size for each one you resized

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("widgets", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column("widgets", sa.Column("size", JSONB()))
    op.create_index("ix_widgets_user_removed", "widgets", ["user_id", "removed_at"])


def downgrade() -> None:
    op.drop_index("ix_widgets_user_removed", table_name="widgets")
    op.drop_column("widgets", "size")
    op.drop_column("widgets", "removed_at")
