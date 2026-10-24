"""messages.recalled: what Nexus remembered for a reply, for the chat and orb to show

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("recalled", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "recalled")
