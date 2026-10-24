"""self-review: reviews of how Nexus is used and the improvements they propose

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "improvement_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("proposals", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_improvement_reviews_created", "improvement_reviews", ["created_at"])
    op.create_table(
        "improvement_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "review_id",
            sa.Uuid(),
            sa.ForeignKey("improvement_reviews.id", ondelete="SET NULL"),
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("impact", sa.String(16), nullable=False),
        sa.Column("effort", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_improvement_proposals_status_created",
        "improvement_proposals",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_improvement_proposals_fingerprint", "improvement_proposals", ["fingerprint"]
    )


def downgrade() -> None:
    op.drop_index("ix_improvement_proposals_fingerprint", "improvement_proposals")
    op.drop_index("ix_improvement_proposals_status_created", "improvement_proposals")
    op.drop_table("improvement_proposals")
    op.drop_index("ix_improvement_reviews_created", "improvement_reviews")
    op.drop_table("improvement_reviews")
