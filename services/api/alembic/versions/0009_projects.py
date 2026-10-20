"""software projects and their GitHub repos

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workbench_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("stack", sa.String(length=32), nullable=True),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("system_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("repo_owner", sa.String(length=100), nullable=True),
        sa.Column("repo_name", sa.String(length=100), nullable=True),
        sa.Column("repo_url", sa.String(length=300), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("merged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("transferred_to", sa.String(length=100), nullable=True),
        sa.Column("last_commit", sa.String(length=64), nullable=True),
        sa.Column("last_commit_message", sa.Text(), nullable=True),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_url", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["system_id"], ["agent_systems.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "workbench_id"),
    )
    op.create_index("ix_projects_user", "projects", ["user_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_projects_user", table_name="projects")
    op.drop_table("projects")
