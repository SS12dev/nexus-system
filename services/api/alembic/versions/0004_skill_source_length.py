"""longer skill source, to hold the run a learned skill came from

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("skills", "source", type_=sa.String(64), existing_type=sa.String(16))


def downgrade() -> None:
    op.alter_column("skills", "source", type_=sa.String(16), existing_type=sa.String(64))
