"""advice and cards: which card a subscription is on and where it is moving, card expiry, a
bank account's debit card, the account a credit card's bill is paid from, how often a saving
takes deposits, other Gmail accounts Money reads alerts from, and the state of each advice item
(done, snoozed or dismissed)

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _account(name: str) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey("money_accounts.id", ondelete="SET NULL"), nullable=True
    )


def upgrade() -> None:
    op.add_column("money_subscriptions", _account("account_id"))
    op.add_column("money_subscriptions", _account("move_to_id"))
    op.add_column("money_subscriptions", sa.Column("move_since", sa.Date(), nullable=True))
    op.add_column("money_subscriptions", sa.Column("moved_on", sa.Date(), nullable=True))
    op.add_column("money_accounts", sa.Column("expires_on", sa.Date(), nullable=True))
    op.add_column("money_accounts", sa.Column("card_mask", sa.String(8), nullable=True))
    op.add_column("money_accounts", _account("bill_from_id"))
    op.add_column("money_assets", sa.Column("contribution_cadence", sa.String(10), nullable=True))
    op.create_table(
        "money_mailboxes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("email_enc", sa.Text(), nullable=False),
        sa.Column("email_masked", sa.String(80), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "email_hash", name="uq_money_mailboxes_user_email"),
    )
    op.create_table(
        "advice_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("until", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_advice_states_user_key"),
    )


def downgrade() -> None:
    op.drop_table("advice_states")
    op.drop_table("money_mailboxes")
    op.drop_column("money_assets", "contribution_cadence")
    for column in ("bill_from_id", "card_mask", "expires_on"):
        op.drop_column("money_accounts", column)
    for column in ("moved_on", "move_since", "move_to_id", "account_id"):
        op.drop_column("money_subscriptions", column)
