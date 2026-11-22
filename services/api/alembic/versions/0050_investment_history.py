"""what shares and funds were worth day by day, for the investments graph

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "money_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("value_paise", sa.BigInteger(), nullable=False),
        sa.Column("cost_paise", sa.BigInteger(), nullable=True),
        sa.Column("filled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("user_id", "item_id", "day", name="uq_money_snapshots_item_day"),
    )
    op.create_index("ix_money_snapshots_user_day", "money_snapshots", ["user_id", "day"])
    op.add_column("money_stocks", sa.Column("history_from", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("money_stocks", "history_from")
    op.drop_index("ix_money_snapshots_user_day", table_name="money_snapshots")
    op.drop_table("money_snapshots")
