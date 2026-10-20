"""running apps: deploy state on projects

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("app_slug", sa.String(length=64), nullable=True))
    op.add_column("projects", sa.Column("app_state", sa.String(length=24), nullable=True))
    op.add_column("projects", sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("deploy_error", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("internet", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_projects_app_slug", "projects", ["app_slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_projects_app_slug", table_name="projects")
    for column in ("internet", "deploy_error", "deployed_at", "app_state", "app_slug"):
        op.drop_column("projects", column)
