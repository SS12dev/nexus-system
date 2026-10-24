"""new apps are public by default: links are for sharing, the code stays in private repos

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only the default changes; apps already deployed keep the visibility they have.
    op.alter_column("projects", "public", server_default=sa.true())


def downgrade() -> None:
    op.alter_column("projects", "public", server_default=sa.false())
