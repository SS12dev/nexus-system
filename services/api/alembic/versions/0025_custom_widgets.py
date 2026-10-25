"""Custom widgets: small HTML apps on the main screen, one row per version

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_widgets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("root_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("manifest", JSONB(), nullable=False, server_default="{}"),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_custom_widgets_root", "custom_widgets", ["root_id", "version"])


def downgrade() -> None:
    op.drop_table("custom_widgets")
