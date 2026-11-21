"""search across everything: full-text index on chat messages

Revision ID: 0039
Revises: 0037
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Generated, so every message (old and new) is searchable without a backfill job.
    op.execute(
        "alter table messages add column tsv tsvector "
        "generated always as (to_tsvector('english', content)) stored"
    )
    op.create_index("ix_messages_tsv", "messages", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_messages_tsv", table_name="messages")
    op.drop_column("messages", "tsv")
