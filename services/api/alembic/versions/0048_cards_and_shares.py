"""cards that share one credit limit, and shares (stocks) held directly

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "money_accounts",
        sa.Column(
            "limit_with_id",
            sa.Uuid(),
            sa.ForeignKey("money_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_table(
        "money_stocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False, server_default="NSE"),
        sa.Column("name", sa.String(160), nullable=False, server_default=""),
        sa.Column("isin", sa.String(16), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("avg_price_paise", sa.BigInteger(), nullable=True),
        sa.Column("bought_on", sa.Date(), nullable=True),
        sa.Column("price_paise", sa.BigInteger(), nullable=True),
        sa.Column("price_on", sa.Date(), nullable=True),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "exchange", "symbol", name="uq_money_stocks_user_symbol"),
    )


def downgrade() -> None:
    op.drop_table("money_stocks")
    op.drop_column("money_accounts", "limit_with_id")
