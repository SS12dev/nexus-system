"""Notifications: notices on the main screen when a team finishes, fails or needs approval

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("link_id", sa.Uuid(), sa.ForeignKey("links.id", ondelete="SET NULL")),
        sa.Column("url", sa.String(300), nullable=False, server_default="/"),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
