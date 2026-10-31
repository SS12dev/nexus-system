"""knowledge graph: chat history imports (ChatGPT and Claude exports), as the user's own words
per conversation, and mentions and relation evidence that point at them

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TARGETS = ["memory_id", "episode_id", "file_id", "chat_id"]


def upgrade() -> None:
    op.create_table(
        "chat_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source", sa.String(16)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), server_default="reading", nullable=False),
        sa.Column("conversations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("kept", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_chat_imports_user", "chat_imports", ["user_id"])
    op.create_table(
        "imported_chats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "import_id",
            sa.Uuid(),
            sa.ForeignKey("chat_imports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), server_default="", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("messages", sa.Integer(), server_default="0", nullable=False),
        sa.Column("graph_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("import_id", "external_id", name="uq_imported_chats_external"),
    )
    op.create_index("ix_imported_chats_pending", "imported_chats", ["user_id", "graph_at"])

    for table, unique, first in (
        ("mentions", "uq_mentions", "entity_id"),
        ("relation_evidence", "uq_relation_evidence", "relation_id"),
    ):
        op.add_column(
            table,
            sa.Column(
                "imported_chat_id",
                sa.Uuid(),
                sa.ForeignKey("imported_chats.id", ondelete="CASCADE"),
            ),
        )
        op.drop_constraint(unique, table, type_="unique")
        op.create_unique_constraint(
            unique,
            table,
            [first, *TARGETS, "imported_chat_id"],
            postgresql_nulls_not_distinct=True,
        )
    op.create_index("ix_mentions_imported_chat", "mentions", ["imported_chat_id"])
    op.drop_constraint("ck_mentions_one_target", "mentions", type_="check")
    op.create_check_constraint(
        "ck_mentions_one_target",
        "mentions",
        "num_nonnulls(memory_id, episode_id, file_id, chat_id, imported_chat_id) = 1",
    )


def downgrade() -> None:
    op.execute("delete from mentions where imported_chat_id is not null")
    op.execute("delete from relation_evidence where imported_chat_id is not null")
    op.drop_constraint("ck_mentions_one_target", "mentions", type_="check")
    op.create_check_constraint(
        "ck_mentions_one_target",
        "mentions",
        "num_nonnulls(memory_id, episode_id, file_id, chat_id) = 1",
    )
    op.drop_index("ix_mentions_imported_chat", table_name="mentions")
    for table, unique, first in (
        ("mentions", "uq_mentions", "entity_id"),
        ("relation_evidence", "uq_relation_evidence", "relation_id"),
    ):
        op.drop_constraint(unique, table, type_="unique")
        op.create_unique_constraint(
            unique, table, [first, *TARGETS], postgresql_nulls_not_distinct=True
        )
        op.drop_column(table, "imported_chat_id")
    op.drop_index("ix_imported_chats_pending", table_name="imported_chats")
    op.drop_table("imported_chats")
    op.drop_index("ix_chat_imports_user", table_name="chat_imports")
    op.drop_table("chat_imports")
