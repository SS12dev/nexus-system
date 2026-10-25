"""Widgets on the main screen: timers, alarms, stopwatches, reminders, clocks and notes

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widgets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
        sa.Column("state", JSONB(), nullable=False, server_default="{}"),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("fires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_widgets_user", "widgets", ["user_id", "slot"])
    op.create_index("ix_widgets_fires_at", "widgets", ["fires_at"])


def downgrade() -> None:
    op.drop_table("widgets")
