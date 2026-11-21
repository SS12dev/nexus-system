"""money details: account numbers, branch and nominee on savings, account numbers on loans

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("money_assets", sa.Column("branch", sa.String(80), nullable=True))
    op.add_column("money_assets", sa.Column("account_no_enc", sa.Text(), nullable=True))
    op.add_column("money_assets", sa.Column("account_last4", sa.String(4), nullable=True))
    op.add_column("money_assets", sa.Column("nominee_enc", sa.Text(), nullable=True))
    op.add_column("money_assets", sa.Column("nominee_relation", sa.String(30), nullable=True))
    op.add_column("money_loans", sa.Column("account_no_enc", sa.Text(), nullable=True))
    op.add_column("money_loans", sa.Column("account_last4", sa.String(4), nullable=True))


def downgrade() -> None:
    for column in ("account_last4", "account_no_enc"):
        op.drop_column("money_loans", column)
    for column in ("nominee_relation", "nominee_enc", "account_last4", "account_no_enc", "branch"):
        op.drop_column("money_assets", column)
