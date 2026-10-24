"""knowledge graph: entities, the relations between them, and what mentions them

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024


def _user():
    return sa.Column(
        "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("is_self", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("embedding_model", sa.String(64)),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', name || ' ' || summary)", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_entities_user", "entities", ["user_id"])
    op.create_index("ix_entities_tsv", "entities", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_entities_embedding",
        "entities",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # One entity for the user themselves.
    op.create_index(
        "uq_entities_self",
        "entities",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_self"),
    )

    op.create_table(
        "relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column(
            "src_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "dst_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("predicate", sa.String(80), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_relations_src", "relations", ["src_id"])
    op.create_index("ix_relations_dst", "relations", ["dst_id"])
    op.create_index("ix_relations_user", "relations", ["user_id"])

    op.create_table(
        "mentions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user(),
        sa.Column(
            "entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memory_id", sa.Uuid(), sa.ForeignKey("memories.id", ondelete="CASCADE")),
        sa.Column("episode_id", sa.Uuid(), sa.ForeignKey("episodes.id", ondelete="CASCADE")),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("files.id", ondelete="CASCADE")),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(memory_id, episode_id, file_id, chat_id) = 1",
            name="ck_mentions_one_target",
        ),
        sa.UniqueConstraint(
            "entity_id",
            "memory_id",
            "episode_id",
            "file_id",
            "chat_id",
            name="uq_mentions",
            postgresql_nulls_not_distinct=True,
        ),
    )
    for col in ("memory", "episode", "file", "chat"):
        op.create_index(f"ix_mentions_{col}", "mentions", [f"{col}_id"])

    for table in ("memories", "episodes", "files"):
        op.add_column(table, sa.Column("graph_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for table in ("memories", "episodes", "files"):
        op.drop_column(table, "graph_at")
    op.drop_table("mentions")
    op.drop_table("relations")
    op.drop_table("entities")
