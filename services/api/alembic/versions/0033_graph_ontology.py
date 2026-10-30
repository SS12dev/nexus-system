"""knowledge graph ontology: shells, domains, typed and weighted relations with evidence

Entities get a shell (0 the user .. 4 knowledge), a life domain, salience, a layout position,
an origin and, for the app's own records, the record they stand for. Relations get a family,
the model's own wording as a label, a weight, an evidence count, an origin and when they were
last seen; relation_evidence holds every item that supports one. ontology_types and
ontology_predicates hold the vocabulary (seeded by nexus_common.ontology.seed).

Existing rows: shells follow the old types, the user's own entity is shell 0, each relation's
source chat becomes its first evidence. Families are filled in from the predicates by the
graph sync the worker runs every ten minutes, so the mapping lives in one place.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("entities", "type", type_=sa.String(32))
    op.add_column("entities", sa.Column("shell", sa.Integer(), server_default="4", nullable=False))
    op.add_column("entities", sa.Column("domain", sa.String(16)))
    op.add_column("entities", sa.Column("salience", sa.Float(), server_default="0", nullable=False))
    op.add_column("entities", sa.Column("pos", sa.JSON()))
    op.add_column(
        "entities", sa.Column("origin", sa.String(16), server_default="model", nullable=False)
    )
    op.add_column("entities", sa.Column("source_kind", sa.String(16)))
    op.add_column("entities", sa.Column("source_id", sa.Uuid()))
    op.create_index(
        "uq_entities_source",
        "entities",
        ["user_id", "source_kind", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_kind is not null"),
    )
    op.execute(
        """
        update entities set shell = case
            when is_self then 0
            when type in ('person', 'organisation', 'place') then 2
            when type = 'project' then 3
            else 4 end
        """
    )

    op.add_column("relations", sa.Column("family", sa.String(16)))
    op.add_column("relations", sa.Column("label", sa.String(80), server_default="", nullable=False))
    op.add_column(
        "relations", sa.Column("weight", sa.Float(), server_default="0.5", nullable=False)
    )
    op.add_column(
        "relations", sa.Column("evidence_count", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "relations", sa.Column("origin", sa.String(16), server_default="model", nullable=False)
    )
    op.add_column("relations", sa.Column("last_seen", sa.DateTime(timezone=True)))
    op.execute(
        "update relations set label = predicate, last_seen = updated_at, weight = confidence"
    )

    op.create_table(
        "relation_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "relation_id",
            sa.Uuid(),
            sa.ForeignKey("relations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memory_id", sa.Uuid(), sa.ForeignKey("memories.id", ondelete="CASCADE")),
        sa.Column("episode_id", sa.Uuid(), sa.ForeignKey("episodes.id", ondelete="CASCADE")),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("files.id", ondelete="CASCADE")),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "relation_id",
            "memory_id",
            "episode_id",
            "file_id",
            "chat_id",
            name="uq_relation_evidence",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_relation_evidence_relation", "relation_evidence", ["relation_id"])
    op.execute(
        """
        insert into relation_evidence (id, user_id, relation_id, chat_id, created_at)
        select gen_random_uuid(), user_id, id, source_chat_id, valid_from
        from relations where source_chat_id is not null
        """
    )

    op.create_table(
        "ontology_types",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("shell", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "ontology_predicates",
        sa.Column("name", sa.String(48), primary_key=True),
        sa.Column("family", sa.String(16), nullable=False),
        sa.Column("symmetric", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("ontology_predicates")
    op.drop_table("ontology_types")
    op.drop_index("ix_relation_evidence_relation", table_name="relation_evidence")
    op.drop_table("relation_evidence")
    for col in ("last_seen", "origin", "evidence_count", "weight", "label", "family"):
        op.drop_column("relations", col)
    op.drop_index("uq_entities_source", table_name="entities")
    for col in ("source_id", "source_kind", "origin", "pos", "salience", "domain", "shell"):
        op.drop_column("entities", col)
    op.alter_column("entities", "type", type_=sa.String(16))
