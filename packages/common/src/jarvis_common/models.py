"""Database schema. Alembic migrations in services/api own the DDL; keep the two in step."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Width of every embedding column. It matches bge-m3, the default local embedder; another model
# needs either the same width (OpenAI's text-embedding-3 models take a `dimensions` parameter) or
# a migration.
EMBEDDING_DIM = 1024
# What a user's assistant is called until they pick a name of their own.
DEFAULT_ASSISTANT_NAME = "Jarvis"
ASSISTANT_NAME_MAX = 24


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RoleName(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


# Higher rank means more rights; a check for a role passes for every role ranked at or above it.
ROLE_RANK: dict[RoleName, int] = {
    RoleName.viewer: 10,
    RoleName.member: 20,
    RoleName.admin: 30,
    RoleName.owner: 40,
}


class UserStatus(enum.StrEnum):
    invited = "invited"
    active = "active"
    disabled = "disabled"


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class MessageStatus(enum.StrEnum):
    streaming = "streaming"
    complete = "complete"
    failed = "failed"


class FileStatus(enum.StrEnum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class MemoryKind(enum.StrEnum):
    preference = "preference"
    fact = "fact"
    goal = "goal"
    instruction = "instruction"


class FileOrigin(enum.StrEnum):
    upload = "upload"
    # Made by Jarvis or an agent, for example a report written in the sandbox.
    generated = "generated"


class SkillScope(enum.StrEnum):
    # Shipped with Jarvis in library/core; shared by every user.
    core = "core"
    # Playbooks shared by every user once an admin enables them, e.g. imported ones.
    domain = "domain"
    # One user's own skills.
    personal = "personal"
    # Drafted by Jarvis after a hard run; the user approves them.
    learned = "learned"


class SkillStatus(enum.StrEnum):
    # Waiting for review: learned and imported skills start here and can't be used yet.
    draft = "draft"
    active = "active"
    disabled = "disabled"


class InstructionLayer(enum.StrEnum):
    # Added to every agent's prompt, after the shipped safety rules.
    global_ = "global"
    # For agents with a given role, such as researcher or writer.
    role = "role"
    # One user's house style, for their own agents and Jarvis.
    user = "user"


class Capability(enum.StrEnum):
    """Tool rights beyond reading and writing in the user's own library. Owners and admins have
    them all; members get them per user on the People page."""

    # Run code in the sandbox.
    exec = "exec"
    # Act outside Jarvis, such as sending email. Always asks for approval first.
    outbound = "outbound"


class SystemLifetime(enum.StrEnum):
    # Archived once its run finishes.
    ephemeral = "ephemeral"
    # Stays, and can be run again.
    persistent = "persistent"
    # A saved recipe with no live agents.
    template = "template"


class SystemStatus(enum.StrEnum):
    idle = "idle"
    running = "running"
    awaiting_approval = "awaiting_approval"
    paused = "paused"
    archived = "archived"


class RunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    awaiting_approval = "awaiting_approval"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ApprovalStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


def _enum(cls: type[enum.Enum], name: str, length: int = 16) -> Enum:
    return Enum(
        cls,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    # The name the user gave their assistant; None until they choose one after first sign-in.
    assistant_name: Mapped[str | None] = mapped_column(String(ASSISTANT_NAME_MAX))
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[UserStatus] = mapped_column(_enum(UserStatus, "user_status"))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", lazy="selectin")

    @property
    def assistant(self) -> str:
        """What this user's assistant is called."""
        return self.assistant_name or DEFAULT_ASSISTANT_NAME

    @property
    def role(self) -> RoleName:
        """The user's effective role: the highest-ranked one they hold (viewer if none)."""
        if not self.roles:
            return RoleName.viewer
        return max((r.name for r in self.roles), key=lambda n: ROLE_RANK[n])

    def has_role(self, minimum: RoleName) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[RoleName] = mapped_column(_enum(RoleName, "role_name"), unique=True)
    rank: Mapped[int] = mapped_column(Integer)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class Session(Base):
    """A signed-in browser. Only a hash of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(lazy="joined")


class GoogleGrant(Base):
    """Google services a user let Jarvis use at sign-in. Tokens are encrypted with a key derived
    from SECRET_KEY (see jarvis_common.google)."""

    __tablename__ = "google_grants"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Space separated, as Google returns them.
    scopes: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApiKey(Base):
    """A credential Jarvis holds on a user's behalf, such as their LiteLLM virtual key."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    secret: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # At most one live key per user and provider.
    __table_args__ = (
        Index(
            "ix_api_keys_user_provider",
            "user_id",
            "provider",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class Budget(Base):
    """Spend cap per user and period, enforced by the user's LiteLLM virtual key."""

    __tablename__ = "budgets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    max_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    duration: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    __table_args__ = (Index("ix_chats_user_updated", "user_id", "updated_at"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[MessageStatus] = mapped_column(_enum(MessageStatus, "message_status"))
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Langfuse trace id for the turn that produced this message.
    trace_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    # Tools Jarvis used for an assistant reply: id, name, args, status, ms, summary.
    steps: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_messages_chat_created", "chat_id", "created_at"),)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_audit_log_created", "created_at"),)


def _tsv(column: str = "content"):
    return mapped_column(
        TSVECTOR, Computed(f"to_tsvector('english', {column})", persisted=True), deferred=True
    )


# --- Library -----------------------------------------------------------------------------------


class File(Base):
    """An upload. The bytes live in object storage; the parsed text lives in file_chunks."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The chat it was uploaded in, if any. Files stay in the library when the chat is deleted.
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    status: Mapped[FileStatus] = mapped_column(_enum(FileStatus, "file_status"))
    error: Mapped[str | None] = mapped_column(Text)
    # Which parser produced the text, how many chunks and characters it has.
    parser: Mapped[str | None] = mapped_column(String(32))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[FileOrigin] = mapped_column(
        _enum(FileOrigin, "file_origin"), default=FileOrigin.upload, server_default="upload"
    )
    # The agent run that generated it, if any.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    # Versions of a generated file share the first version's id here; version counts from 1.
    version_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_files_user_created", "user_id", "created_at"),
        Index("ix_files_user_sha", "user_id", "sha256"),
    )


class FileChunk(Base):
    __tablename__ = "file_chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    # Denormalised so every search can filter on the owner without a join.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    # Heading path or sheet name the chunk sits under.
    heading: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    tsv = _tsv()

    __table_args__ = (
        UniqueConstraint("file_id", "idx"),
        Index("ix_file_chunks_user", "user_id"),
        Index("ix_file_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_file_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Attachment(Base):
    """A file sent with a message."""

    __tablename__ = "attachments"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )


class EmbeddingCache(Base):
    """Vectors keyed by a hash of the text, so identical text is never embedded twice. Holds no
    text, so it reveals nothing about whose content produced a vector."""

    __tablename__ = "embedding_cache"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Memory ------------------------------------------------------------------------------------


class Profile(Base):
    """Jarvis's short impression of a user, rebuilt from their memories, plus their memory
    settings."""

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    # When false, Jarvis neither learns new memories from chats nor reads old ones.
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a memory changes; the next rebuild clears it.
    stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Memory(Base):
    """One fact, preference, goal or standing instruction about a user."""

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[MemoryKind] = mapped_column(_enum(MemoryKind, "memory_kind"))
    content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.6)
    # Pinned memories are always in the profile and Jarvis never changes or drops them itself.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # "jarvis" when learned from a chat, "user" when added or edited on the Memory page.
    source: Mapped[str] = mapped_column(String(16), default="jarvis")
    source_chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL")
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    tsv = _tsv()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Last time a chat confirmed it; stale memories lose confidence in the nightly job.
    reinforced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_memories_user", "user_id", "updated_at"),
        Index("ix_memories_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_memories_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MemoryEvent(Base):
    """History of one memory: who added, changed or reinforced it and from which message.
    Deleting a memory deletes its history too, so deletion is real deletion."""

    __tablename__ = "memory_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    op: Mapped[str] = mapped_column(String(16))  # add, update, reinforce, pin, unpin, decay
    actor: Mapped[str] = mapped_column(String(16))  # jarvis or user
    content: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_memory_events_memory", "memory_id", "created_at"),)


class Episode(Base):
    """Summary and outcome of one chat, written once the chat goes quiet. Also serves as the
    rolling summary of a chat too long to send in full."""

    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), unique=True
    )
    content: Mapped[str] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    tsv = _tsv()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_episodes_user", "user_id"),
        Index("ix_episodes_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_episodes_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --- Skills and instructions -------------------------------------------------------------------


class Skill(Base):
    """One skill in the Agent Skills format: a SKILL.md (frontmatter name and description, then
    instructions) plus optional text files such as scripts/ and references/. Shared skills have
    no owner; personal and learned ones belong to one user."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # "<scope>/<name>", the id agents use, e.g. core/pdf.
    slug: Mapped[str] = mapped_column(String(128))
    scope: Mapped[SkillScope] = mapped_column(_enum(SkillScope, "skill_scope"))
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    # The whole SKILL.md, frontmatter included.
    content: Mapped[str] = mapped_column(Text)
    # Other files of the skill by relative path, e.g. {"scripts/merge.py": "..."}.
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[SkillStatus] = mapped_column(_enum(SkillStatus, "skill_status"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # library (shipped), editor, import, or run:<id> for a skill Jarvis learned from a run.
    source: Mapped[str] = mapped_column(String(64))
    # Hash of the shipped files, so a startup sync only touches skills that changed.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    successes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    tsv = _tsv("name || ' ' || description")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index(
            "ix_skills_shared_slug", "slug", unique=True, postgresql_where=text("owner_id IS NULL")
        ),
        Index(
            "ix_skills_owner_slug",
            "owner_id",
            "slug",
            unique=True,
            postgresql_where=text("owner_id IS NOT NULL"),
        ),
        Index("ix_skills_tsv", "tsv", postgresql_using="gin"),
    )


class SkillVersion(Base):
    """Every saved version of a skill, so an edit can be rolled back."""

    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("skill_id", "version"),)


class SkillUsage(Base):
    """An agent loaded a skill during a run; the outcome is set when the run ends."""

    __tablename__ = "skill_usages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    agent: Mapped[str] = mapped_column(String(64))
    # success or failure, once the run has ended.
    outcome: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_skill_usages_run", "run_id"),)


class Instruction(Base):
    """A layer of standing instructions for agents: global (admins), per agent role (admins), or
    one user's own. Shipped defaults live in library/instructions; these rows add to them."""

    __tablename__ = "instructions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    layer: Mapped[InstructionLayer] = mapped_column(_enum(InstructionLayer, "instruction_layer"))
    # The agent role for the role layer; empty otherwise.
    key: Mapped[str] = mapped_column(String(64), default="")
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index(
            "ix_instructions_shared",
            "layer",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "ix_instructions_user",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )


class ToolGrant(Base):
    __tablename__ = "tool_grants"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[Capability] = mapped_column(
        _enum(Capability, "capability"), primary_key=True
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Agent systems -----------------------------------------------------------------------------


class AgentSystem(Base):
    """A team of sub-agents Jarvis designed. The spec (see jarvis_common.spec) is the whole
    definition: agents, their tools, skills and model tiers, and the topology joining them."""

    __tablename__ = "agent_systems"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict] = mapped_column(JSON)
    topology: Mapped[str] = mapped_column(String(32))
    lifetime: Mapped[SystemLifetime] = mapped_column(_enum(SystemLifetime, "system_lifetime"))
    status: Mapped[SystemStatus] = mapped_column(_enum(SystemStatus, "system_status", 24))
    # The template it was made from, or the system a template was saved from.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_systems.id", ondelete="SET NULL")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    tsv = _tsv("name || ' ' || goal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_agent_systems_user", "user_id", "updated_at"),
        Index("ix_agent_systems_tsv", "tsv", postgresql_using="gin"),
    )


class AgentRun(Base):
    """One run of a system. Its LangGraph checkpoints use the run id as the thread id, so a run
    paused for approval resumes where it stopped."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_systems.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    input: Mapped[str] = mapped_column(Text, default="")
    # Library files the run starts with.
    file_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status", 24))
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(0))
    estimate_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    # Live state per agent for the UI: {agent_id: {"status", "tier", "steps"}}.
    agents: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(64))
    # The run's own LiteLLM key, capped at budget_usd; revoked and cleared when the run ends.
    key_secret: Mapped[str | None] = mapped_column(Text)
    # The chat message that carries the result, once posted.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_agent_runs_system", "system_id", "created_at"),
        Index("ix_agent_runs_user", "user_id", "created_at"),
    )


class Project(Base):
    """A software project a dev team built: its workbench folder, and its GitHub repo once it
    has been pushed. The Library shows it as one card, never as loose files."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The workbench project id (the spec's project): a follow-up run works in the same one.
    workbench_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    stack: Mapped[str | None] = mapped_column(String(32))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    # The system and run that last worked on it.
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_systems.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    # GitHub: owner/name, set once the repo exists. After "move it to my GitHub" the owner is
    # the user's account and Jarvis no longer pushes to it.
    repo_owner: Mapped[str | None] = mapped_column(String(100))
    repo_name: Mapped[str | None] = mapped_column(String(100))
    repo_url: Mapped[str | None] = mapped_column(String(300))
    # The branch of the last push and whether it was merged to main.
    branch: Mapped[str | None] = mapped_column(String(200))
    merged: Mapped[bool] = mapped_column(Boolean, default=False)
    transferred_to: Mapped[str | None] = mapped_column(String(100))
    last_commit: Mapped[str | None] = mapped_column(String(64))
    last_commit_message: Mapped[str | None] = mapped_column(Text)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The running app (the deployer): its name in URLs and containers, where it answers, and
    # how its last deploy went. app_state: building, running, stopped or failed.
    app_slug: Mapped[str | None] = mapped_column(String(64))
    app_state: Mapped[str | None] = mapped_column(String(24))
    live_url: Mapped[str | None] = mapped_column(String(300))
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deploy_error: Mapped[str | None] = mapped_column(Text)
    # Apps have no internet unless their project opts in.
    internet: Mapped[bool] = mapped_column(Boolean, default=False)
    # Private apps open only for their owner and Jarvis's owner, after signing in to Jarvis.
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    # The last deploy's screenshot, a file in the owner's library.
    screenshot_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "workbench_id"),
        Index("ix_projects_user", "user_id", "updated_at"),
    )


class RunEvent(Base):
    """What happened in a run, for the Agents page timeline."""

    __tablename__ = "run_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    agent: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_run_events_run", "run_id", "created_at"),)


class Approval(Base):
    """A risky tool call waiting for the user. The run pauses until every one is decided."""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_systems.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    agent: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(64))
    tool_call_id: Mapped[str] = mapped_column(String(128))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    # Why it needs approval, in words for the card.
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ApprovalStatus] = mapped_column(_enum(ApprovalStatus, "approval_status"))
    # The arguments the user approved, if they edited them.
    decided_args: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id"),
        Index("ix_approvals_user_status", "user_id", "status"),
    )


# --- Operations --------------------------------------------------------------------------------


class BackupStatus(enum.StrEnum):
    # Asked for on the Admin page; the backup service picks it up within a minute.
    requested = "requested"
    running = "running"
    ok = "ok"
    failed = "failed"


class Backup(Base):
    """One run of the backup service (infra/backup), which writes these rows itself with psql."""

    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # scheduled or manual
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[BackupStatus] = mapped_column(_enum(BackupStatus, "backup_status"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Folder name under BACKUP_DIR, e.g. 2026-10-20T2130Z.
    name: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_backups_created", "created_at"),)


class AppSetting(Base):
    """Switches admins flip at runtime, such as maintenance mode. One JSON value per key."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
