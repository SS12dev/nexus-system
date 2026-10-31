"""knowledge graph: question cards (the starter deck and the nightly cards where the graph is
unsure)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("shell", sa.Integer()),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("help", sa.Text(), server_default="", nullable=False),
        sa.Column("form", sa.String(8), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column("answer", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "key", name="uq_graph_questions_key"),
    )
    op.create_index("ix_graph_questions_open", "graph_questions", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_graph_questions_open", table_name="graph_questions")
    op.drop_table("graph_questions")
