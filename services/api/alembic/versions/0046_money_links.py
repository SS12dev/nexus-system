"""money links: transfers, card payments and refunds paired, money into and out of savings

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "money_txns",
        sa.Column(
            "pair_id", sa.Uuid(), sa.ForeignKey("money_txns.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.add_column("money_txns", sa.Column("pair_kind", sa.String(8), nullable=True))
    op.add_column(
        "money_txns",
        sa.Column(
            "asset_id",
            sa.Uuid(),
            sa.ForeignKey("money_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_money_txns_asset", "money_txns", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_money_txns_asset", table_name="money_txns")
    for column in ("asset_id", "pair_kind", "pair_id"):
        op.drop_column("money_txns", column)
