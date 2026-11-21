"""morning brief: when and where each person's brief goes

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brief_settings",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("send_at", sa.String(5), nullable=False),
        sa.Column("days", sa.String(10), nullable=False),
        sa.Column("push", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("telegram", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("telegram_money", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("place", sa.String(120)),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("last_sent_on", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("brief_settings")
