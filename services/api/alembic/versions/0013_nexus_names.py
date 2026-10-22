"""the last pre-Nexus names: "assistant" for what the assistant did, nexus-* model aliases

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Who did it. "jarvis" meant the assistant, whatever the user named it.
ACTOR_COLUMNS = [
    ("memories", "source"),
    ("memory_events", "actor"),
    ("links", "source"),
    ("skill_usages", "agent"),
]
# The LiteLLM aliases were jarvis-nano/mini/pro/max/embed. Recording the new names keeps stored
# vectors from being re-embedded.
MODEL_COLUMNS = [
    ("messages", "model"),
    ("file_chunks", "embedding_model"),
    ("embedding_cache", "model"),
    ("memories", "embedding_model"),
    ("episodes", "embedding_model"),
    ("skills", "embedding_model"),
    ("agent_systems", "embedding_model"),
]


def _rename(actor: tuple[str, str], prefix: tuple[str, str]) -> None:
    for table, column in ACTOR_COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = '{actor[1]}' WHERE {column} = '{actor[0]}'")
    old, new = prefix
    for table, column in MODEL_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = '{new}' || substr({column}, {len(old) + 1}) "
            f"WHERE {column} LIKE '{old}%'"
        )


def upgrade() -> None:
    _rename(("jarvis", "assistant"), ("jarvis-", "nexus-"))


def downgrade() -> None:
    _rename(("assistant", "jarvis"), ("nexus-", "jarvis-"))
