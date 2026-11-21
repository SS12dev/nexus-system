"""organiser: notes, tasks, projects, areas, resources and daily notes; links, revisions and
weekly reviews

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("org_items.id", ondelete="SET NULL")),
        sa.Column("due_on", sa.Date()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("day", sa.Date()),
        sa.Column("source", sa.String(16), nullable=False, server_default="user"),
        sa.Column("suggestion", sa.JSON()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("embedding", Vector(1024)),
        sa.Column("embedding_model", sa.String(64)),
        sa.Column(
            "tsv",
            sa.dialects.postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', title || ' ' || body)", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_org_items_user_type", "org_items", ["user_id", "type", "status"])
    op.create_index("ix_org_items_parent", "org_items", ["parent_id"])
    op.create_index("ix_org_items_due", "org_items", ["user_id", "due_on"])
    op.create_index(
        "uq_org_items_daily",
        "org_items",
        ["user_id", "day"],
        unique=True,
        postgresql_where=sa.text("type = 'daily'"),
    )
    op.create_index("ix_org_items_tsv", "org_items", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_org_items_embedding",
        "org_items",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "org_links",
        sa.Column(
            "src_id",
            sa.Uuid(),
            sa.ForeignKey("org_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "dst_id",
            sa.Uuid(),
            sa.ForeignKey("org_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_org_links_dst", "org_links", ["dst_id"])
    op.create_table(
        "org_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("org_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_org_revisions_item", "org_revisions", ["item_id", "created_at"])
    op.create_table(
        "org_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("week_of", sa.Date(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("focus", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "week_of"),
    )


def downgrade() -> None:
    op.drop_table("org_reviews")
    op.drop_table("org_revisions")
    op.drop_table("org_links")
    op.drop_table("org_items")
