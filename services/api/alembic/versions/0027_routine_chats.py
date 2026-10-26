"""Routine chats leave the chat list: chats.kind is "chat" or "routine"

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("kind", sa.String(20), nullable=False, server_default="chat"))
    # Every routine already runs in a chat made for it; those leave the chat list.
    op.execute(
        "UPDATE chats SET kind = 'routine' "
        "WHERE id IN (SELECT chat_id FROM routines WHERE chat_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_column("chats", "kind")
