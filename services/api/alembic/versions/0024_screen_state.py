"""The main screen's saved layout: mode, what's open over it, and the person's own changes

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "screen_states",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.String(16), nullable=False, server_default="orb"),
        sa.Column("focus", JSONB()),
        sa.Column("overrides", JSONB(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("screen_states")
