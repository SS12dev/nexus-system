"""30-day account deletion recovery.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("account_deleted_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("account_delete_expires_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("account_purge_started_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("account_reset_requested_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_users_account_delete_expiry",
        "users",
        ["account_delete_expires_at"],
        postgresql_where=sa.text("account_deleted_at IS NOT NULL"),
    )
    op.create_table(
        "account_recovery_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("avatar_url", sa.String(1000)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_queued_at", sa.DateTime(timezone=True)),
        sa.Column("decision_email_queued_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_account_recovery_pending_user",
        "account_recovery_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_account_recovery_status_created",
        "account_recovery_requests",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_recovery_status_created", table_name="account_recovery_requests")
    op.drop_index("uq_account_recovery_pending_user", table_name="account_recovery_requests")
    op.drop_table("account_recovery_requests")
    op.drop_index("ix_users_account_delete_expiry", table_name="users")
    op.drop_column("users", "account_reset_requested_at")
    op.drop_column("users", "account_purge_started_at")
    op.drop_column("users", "account_delete_expires_at")
    op.drop_column("users", "account_deleted_at")
