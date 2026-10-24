"""Onboarding answers on users, and routines: prompts that run on a schedule

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("timezone", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("profile", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    op.add_column(
        "users",
        sa.Column("share_usage", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    # Everyone who already uses Nexus has set it up their own way: no interview for them.
    op.execute(
        "UPDATE users SET onboarded_at = COALESCE(last_login_at, created_at) "
        "WHERE assistant_name IS NOT NULL"
    )

    op.create_table(
        "routines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule", sa.String(300), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("delivery", sa.JSON(), nullable=False),
        sa.Column("only_if_new", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_routines_due", "routines", ["enabled", "next_run_at"])
    op.create_index("ix_routines_user", "routines", ["user_id"])

    op.create_table(
        "routine_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "routine_id",
            sa.Uuid(),
            sa.ForeignKey("routines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("delivered", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("routine_id", "scheduled_for", name="uq_routine_runs_slot"),
    )
    op.create_index("ix_routine_runs_routine_started", "routine_runs", ["routine_id", "started_at"])


def downgrade() -> None:
    op.drop_table("routine_runs")
    op.drop_table("routines")
    for column in ("share_usage", "phone", "profile", "onboarded_at", "timezone"):
        op.drop_column("users", column)
