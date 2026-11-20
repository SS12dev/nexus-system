"""money: a read-only view of finances from alert emails, statements and CAS files

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user() -> sa.Column:
    return sa.Column(
        "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def _account() -> sa.Column:
    return sa.Column(
        "account_id", sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="SET NULL")
    )


def upgrade() -> None:
    op.create_table(
        "money_settings",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("gmail_sync", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "assistant_access", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("cas_password_enc", sa.Text()),
        sa.Column("rules", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("gmail_synced_until", sa.DateTime(timezone=True)),
        sa.Column("gmail_synced_at", sa.DateTime(timezone=True)),
        sa.Column("cas_message_id", sa.String(64)),
        sa.Column("cas_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "money_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("institution", sa.String(60)),
        sa.Column("mask", sa.String(8)),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_money_accounts_user", "money_accounts", ["user_id"])
    op.create_table(
        "money_txns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        _account(),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("merchant", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("category_by", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("narration_enc", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(64)),
        sa.Column("ref", sa.String(40)),
        sa.Column("balance_paise", sa.BigInteger()),
        sa.Column("dedupe_key", sa.String(40), nullable=False),
        sa.Column("alt_key", sa.String(40)),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "dedupe_key"),
    )
    op.create_index("ix_money_txns_user_day", "money_txns", ["user_id", "occurred_on"])
    op.create_index("ix_money_txns_account", "money_txns", ["account_id"])
    op.create_table(
        "money_budgets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("monthly_paise", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "category"),
    )
    op.create_table(
        "money_holdings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("folio", sa.String(40), nullable=False),
        sa.Column("amc", sa.String(120), nullable=False),
        sa.Column("scheme", sa.String(300), nullable=False),
        sa.Column("isin", sa.String(16)),
        sa.Column("amfi_code", sa.String(16)),
        sa.Column("units", sa.Numeric(20, 4), nullable=False),
        sa.Column("nav", sa.Numeric(20, 4)),
        sa.Column("nav_date", sa.Date()),
        sa.Column("value_paise", sa.BigInteger()),
        sa.Column("cost_paise", sa.BigInteger()),
        sa.Column("as_of", sa.Date()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "folio", "scheme"),
    )
    op.create_table(
        "money_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("filename_enc", sa.Text(), nullable=False),
        _account(),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_money_imports_user", "money_imports", ["user_id", "created_at"])


def downgrade() -> None:
    for table in (
        "money_imports",
        "money_holdings",
        "money_budgets",
        "money_txns",
        "money_accounts",
        "money_settings",
    ):
        op.drop_table(table)
