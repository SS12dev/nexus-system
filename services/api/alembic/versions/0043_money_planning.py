"""money planning: account balances and card bills, subscription decisions, savings, loans,
goals and the forecast's assumptions; the assistant reads Money by default

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACCOUNT_COLUMNS = (
    ("balance_paise", sa.BigInteger()),
    ("balance_on", sa.Date()),
    ("credit_limit_paise", sa.BigInteger()),
    ("available_paise", sa.BigInteger()),
    ("available_on", sa.Date()),
    ("due_paise", sa.BigInteger()),
    ("min_due_paise", sa.BigInteger()),
    ("due_on", sa.Date()),
    ("statement_on", sa.Date()),
    ("paid_on", sa.Date()),
)


def _user() -> sa.Column:
    return sa.Column(
        "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    for name, kind in _ACCOUNT_COLUMNS:
        op.add_column("money_accounts", sa.Column(name, kind))
    op.add_column(
        "money_accounts",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "money_settings", sa.Column("plan", sa.JSON(), nullable=False, server_default="{}")
    )
    # Shivam asked for the assistant to have Money by default (2026-11-21).
    op.alter_column("money_settings", "assistant_access", server_default=sa.text("true"))
    op.execute("UPDATE money_settings SET assistant_access = true")

    op.create_table(
        "money_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("merchant", sa.String(120), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("amount_paise", sa.BigInteger()),
        sa.Column("cadence", sa.String(12), nullable=False),
        sa.Column("next_on", sa.Date()),
        sa.Column("remind_days", sa.Integer(), nullable=False),
        sa.Column("manage_url", sa.String(300)),
        sa.Column("note", sa.String(300)),
        sa.Column("cancelled_on", sa.Date()),
        *_stamps(),
        sa.UniqueConstraint("user_id", "key"),
    )
    op.create_table(
        "money_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("institution", sa.String(60)),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("rate_bp", sa.Integer(), nullable=False),
        sa.Column("start_on", sa.Date()),
        sa.Column("maturity_on", sa.Date()),
        sa.Column("balance_on", sa.Date()),
        sa.Column("contribution_paise", sa.BigInteger(), nullable=False),
        sa.Column("payout", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("note", sa.String(300)),
        *_stamps(),
    )
    op.create_index("ix_money_assets_user", "money_assets", ["user_id"])
    op.create_table(
        "money_loans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="SET NULL")),
        sa.Column("lender", sa.String(60)),
        sa.Column("principal_paise", sa.BigInteger(), nullable=False),
        sa.Column("rate_bp", sa.Integer(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("emi_paise", sa.BigInteger(), nullable=False),
        sa.Column("first_due_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("note", sa.String(300)),
        *_stamps(),
    )
    op.create_index("ix_money_loans_user", "money_loans", ["user_id"])
    op.create_table(
        "money_goals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("target_paise", sa.BigInteger(), nullable=False),
        sa.Column("by_on", sa.Date(), nullable=False),
        sa.Column("saved_paise", sa.BigInteger(), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False, server_default="[]"),
        *_stamps(),
    )
    op.create_index("ix_money_goals_user", "money_goals", ["user_id"])


def downgrade() -> None:
    for table in ("money_goals", "money_loans", "money_assets", "money_subscriptions"):
        op.drop_table(table)
    op.alter_column("money_settings", "assistant_access", server_default=sa.text("false"))
    op.drop_column("money_settings", "plan")
    op.drop_column("money_accounts", "hidden")
    for name, _ in reversed(_ACCOUNT_COLUMNS):
        op.drop_column("money_accounts", name)
