"""imported chats: "talk" or "asked" (requests are now kept as light evidence)

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "imported_chats",
        sa.Column("kind", sa.String(8), server_default="talk", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("imported_chats", "kind")
