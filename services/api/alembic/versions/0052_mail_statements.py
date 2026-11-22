"""bank statements found in the person's mail, waiting to be imported with a typed password

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "money_mail_statements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "mailbox_id",
            sa.Uuid(),
            sa.ForeignKey("money_mailboxes.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("institution", sa.String(60), nullable=True),
        sa.Column("mask", sa.String(8), nullable=True),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="new"),
        sa.Column("import_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "message_id", name="uq_money_mail_statements_message"),
    )


def downgrade() -> None:
    op.drop_table("money_mail_statements")
