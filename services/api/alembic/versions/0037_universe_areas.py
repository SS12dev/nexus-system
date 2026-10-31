"""universe view: life areas (one galaxy each, found nightly from the graph) and each entity's
area

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "areas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(80), server_default="", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("domain", sa.String(16)),
        sa.Column("hue", sa.Float(), server_default="0.6", nullable=False),
        sa.Column("angle", sa.Float(), server_default="0", nullable=False),
        sa.Column("tilt", sa.Float(), server_default="0", nullable=False),
        sa.Column("size", sa.Integer(), server_default="0", nullable=False),
        sa.Column("activity", sa.Float(), server_default="0", nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("merged_into_id", sa.Uuid()),
        sa.Column("born_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_areas_user", "areas", ["user_id"])
    op.add_column(
        "entities",
        sa.Column("area_id", sa.Uuid(), sa.ForeignKey("areas.id", ondelete="SET NULL")),
    )
    # The old orb never wrote positions; the new layout starts from nothing.
    op.execute("update entities set pos = null")


def downgrade() -> None:
    op.drop_column("entities", "area_id")
    op.drop_index("ix_areas_user", table_name="areas")
    op.drop_table("areas")
