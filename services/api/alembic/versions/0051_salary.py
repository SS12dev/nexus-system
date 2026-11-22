"""take-home salary over time, to fill in months whose salary credit wasn't read

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "money_salaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("monthly_paise", sa.BigInteger(), nullable=False),
        sa.Column("from_on", sa.Date(), nullable=False),
        sa.Column("employer", sa.String(80), nullable=False, server_default=""),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("money_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pay_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "from_on", name="uq_money_salaries_user_from"),
    )


def downgrade() -> None:
    op.drop_table("money_salaries")
