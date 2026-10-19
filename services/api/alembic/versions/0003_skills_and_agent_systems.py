"""skills and agent systems

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instructions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "layer",
            sa.Enum(
                "global", "role", "user", name="instruction_layer", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_instructions_shared",
        "instructions",
        ["layer", "key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "ix_instructions_user",
        "instructions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "core",
                "domain",
                "personal",
                "learned",
                name="skill_scope",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft", "active", "disabled", name="skill_status", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("successes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', name || ' ' || description)", persisted=True),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skills_owner_slug",
        "skills",
        ["owner_id", "slug"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )
    op.create_index(
        "ix_skills_shared_slug",
        "skills",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index("ix_skills_tsv", "skills", ["tsv"], unique=False, postgresql_using="gin")
    op.create_table(
        "tool_grants",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "capability",
            sa.Enum("exec", "outbound", name="capability", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "capability"),
    )
    op.create_table(
        "agent_systems",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("topology", sa.String(length=32), nullable=False),
        sa.Column(
            "lifetime",
            sa.Enum(
                "ephemeral",
                "persistent",
                "template",
                name="system_lifetime",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "idle",
                "running",
                "awaiting_approval",
                "paused",
                "archived",
                name="system_status",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', name || ' ' || goal)", persisted=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["agent_systems.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_systems_tsv", "agent_systems", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_agent_systems_user", "agent_systems", ["user_id", "updated_at"], unique=False
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("file_ids", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "awaiting_approval",
                "paused",
                "completed",
                "failed",
                "cancelled",
                name="run_status",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("budget_usd", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("estimate_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("key_secret", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["system_id"], ["agent_systems.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_system", "agent_runs", ["system_id", "created_at"], unique=False)
    op.create_index("ix_agent_runs_user", "agent_runs", ["user_id", "created_at"], unique=False)
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="approval_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("decided_args", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["system_id"], ["agent_systems.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tool_call_id"),
    )
    op.create_index("ix_approvals_user_status", "approvals", ["user_id", "status"], unique=False)
    op.create_table(
        "run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_events_run", "run_events", ["run_id", "created_at"], unique=False)
    op.create_table(
        "skill_usages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skill_usages_run", "skill_usages", ["run_id"], unique=False)
    op.add_column(
        "files",
        sa.Column(
            "origin",
            sa.Enum("upload", "generated", name="file_origin", native_enum=False, length=16),
            server_default="upload",
            nullable=False,
        ),
    )
    op.add_column("files", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.add_column("files", sa.Column("version_of_id", sa.Uuid(), nullable=True))
    op.add_column("files", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.create_foreign_key(
        "fk_files_version_of", "files", "files", ["version_of_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_files_run", "files", "agent_runs", ["run_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_files_run", "files", type_="foreignkey")
    op.drop_constraint("fk_files_version_of", "files", type_="foreignkey")
    op.drop_column("files", "version")
    op.drop_column("files", "version_of_id")
    op.drop_column("files", "run_id")
    op.drop_column("files", "origin")
    op.drop_index("ix_skill_usages_run", table_name="skill_usages")
    op.drop_table("skill_usages")
    op.drop_index("ix_run_events_run", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_approvals_user_status", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_agent_runs_user", table_name="agent_runs")
    op.drop_index("ix_agent_runs_system", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("skill_versions")
    op.drop_index("ix_agent_systems_user", table_name="agent_systems")
    op.drop_index("ix_agent_systems_tsv", table_name="agent_systems", postgresql_using="gin")
    op.drop_table("agent_systems")
    op.drop_table("tool_grants")
    op.drop_index("ix_skills_tsv", table_name="skills", postgresql_using="gin")
    op.drop_index(
        "ix_skills_shared_slug", table_name="skills", postgresql_where=sa.text("owner_id IS NULL")
    )
    op.drop_index(
        "ix_skills_owner_slug",
        table_name="skills",
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )
    op.drop_table("skills")
    op.drop_index(
        "ix_instructions_user",
        table_name="instructions",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_instructions_shared",
        table_name="instructions",
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.drop_table("instructions")
