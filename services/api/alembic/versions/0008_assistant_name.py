"""each user names their own assistant

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("assistant_name", sa.String(24), nullable=True))
    # People who already use the app keep the name they know; new people choose one.
    op.execute("UPDATE users SET assistant_name = 'Jarvis' WHERE last_login_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("users", "assistant_name")
