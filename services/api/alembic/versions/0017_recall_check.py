"""recall check: questions about memories and past chats, and how well recall finds them

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recall_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), sa.ForeignKey("memories.id", ondelete="CASCADE")),
        sa.Column("episode_id", sa.Uuid(), sa.ForeignKey("episodes.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(memory_id is null) <> (episode_id is null)", name="ck_recall_questions_one_target"
        ),
    )
    op.create_index("ix_recall_questions_user", "recall_questions", ["user_id"])
    op.create_table(
        "recall_evals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("misses", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_recall_evals_user", "recall_evals", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_recall_evals_user", table_name="recall_evals")
    op.drop_table("recall_evals")
    op.drop_index("ix_recall_questions_user", table_name="recall_questions")
    op.drop_table("recall_questions")
