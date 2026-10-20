"""private and public apps, and their screenshot

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("public", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("projects", sa.Column("screenshot_file_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_projects_screenshot", "projects", "files", ["screenshot_file_id"], ["id"],
        ondelete="SET NULL",
    )  # fmt: skip


def downgrade() -> None:
    op.drop_constraint("fk_projects_screenshot", "projects", type_="foreignkey")
    op.drop_column("projects", "screenshot_file_id")
    op.drop_column("projects", "public")
