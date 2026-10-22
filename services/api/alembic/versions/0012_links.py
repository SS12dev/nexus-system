"""links in the library: pointers to GitHub, Google Docs, apps and web pages

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("preview", sa.JSON(), nullable=True),
        sa.Column("preview_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "url"),
    )
    op.create_index("ix_links_user_updated", "links", ["user_id", "updated_at"], unique=False)
    op.create_table(
        "message_links",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["link_id"], ["links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id", "link_id"),
    )


def downgrade() -> None:
    op.drop_table("message_links")
    op.drop_index("ix_links_user_updated", table_name="links")
    op.drop_table("links")
