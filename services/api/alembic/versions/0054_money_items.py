"""Money: what was bought in an order (items read from order emails or told by the person),
and where on a platform it came from (the restaurant on Zomato)

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("money_txns", sa.Column("place", sa.String(120), nullable=True))
    op.create_table(
        "money_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "txn_id",
            sa.Uuid(),
            sa.ForeignKey("money_txns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price_paise", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(8), nullable=False, server_default="email"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_money_items_txn", "money_items", ["txn_id"])
    op.create_index("ix_money_items_user", "money_items", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_money_items_user", "money_items")
    op.drop_index("ix_money_items_txn", "money_items")
    op.drop_table("money_items")
    op.drop_column("money_txns", "place")
