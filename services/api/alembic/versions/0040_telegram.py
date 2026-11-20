"""telegram: linked Telegram chats

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_links",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("tg_username", sa.String(64)),
        sa.Column("tg_name", sa.String(128)),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("telegram_links")
