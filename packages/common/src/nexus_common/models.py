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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Width of every embedding column. It matches bge-m3, the default local embedder; another model
# needs either the same width (OpenAI's text-embedding-3 models take a `dimensions` parameter) or
# a migration.
EMBEDDING_DIM = 1024
# What a user's assistant is called until they pick a name of their own.
DEFAULT_ASSISTANT_NAME = "Jarvis"
ASSISTANT_NAME_MAX = 24
# The version of the Terms of Service and Privacy Policy (apps/web/public/terms.html and
# privacy.html, which show the same date). Changing it asks everyone but owners to agree again.
TERMS_VERSION = "2026-10-27"


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
    # Made by Nexus or an agent, for example a report written in the sandbox.
    generated = "generated"


class SkillScope(enum.StrEnum):
    # Shipped with Nexus in library/core; shared by every user.
    core = "core"
    # Playbooks shared by every user once an admin enables them, e.g. imported ones.
    domain = "domain"
    # One user's own skills.
    personal = "personal"
    # Drafted by Nexus after a hard run; the user approves them.
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
    # One user's house style, for their own agents and Nexus.
    user = "user"


class Capability(enum.StrEnum):
    """Tool rights beyond reading and writing in the user's own library. Owners and admins have
    them all; members get them per user on the People page."""

    # Run code in the sandbox.
    exec = "exec"
    # Act outside Nexus, such as sending email. Always asks for approval first.
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


class AccessRequestStatus(enum.StrEnum):
    pending = "pending"
    # An admin approved it: the person now has an invite and joins on their next sign-in.
    approved = "approved"
    denied = "denied"


class AccountRecoveryStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    cancelled = "cancelled"


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
    # IANA time zone ("Asia/Kolkata"), from the browser at onboarding; routines run on it.
    timezone: Mapped[str | None] = mapped_column(String(64))
    # Set when the person finishes (or skips) the setup interview after first sign-in.
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Their setup answers: roles, interests, day_start, work_days, style, packs.
    # JSONB, not JSON: queries that de-duplicate users need a column type with equality.
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    # WhatsApp number in E.164 ("+919812345678"), for routine notices they opted into.
    phone: Mapped[str | None] = mapped_column(String(20))
    # May the self-review count the anonymous topics of their requests? (improve.py)
    share_usage: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # The Terms of Service and Privacy Policy version they agreed to (TERMS_VERSION), and when.
    terms_version: Mapped[str | None] = mapped_column(String(20))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_delete_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_purge_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_reset_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", lazy="selectin")

    __table_args__ = (
        Index(
            "ix_users_account_delete_expiry",
            "account_delete_expires_at",
            postgresql_where=text("account_deleted_at IS NOT NULL"),
        ),
    )

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


class AccessRequest(Base):
    """Someone without an invite asking to join. Only made from an email the identity provider
    verified moments before (see routes/auth.py), so nobody can ask on another person's behalf.
    Rows are kept after a decision, as the history of who asked and who decided."""

    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    # Why they want in, in their own words, for the admin deciding.
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AccessRequestStatus] = mapped_column(
        _enum(AccessRequestStatus, "access_request_status")
    )
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The invited user an approval made.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        # One open request per address; asking again while pending updates it.
        Index(
            "ix_access_requests_pending_email",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_access_requests_email_created", "email", "created_at"),
        Index("ix_access_requests_status_created", "status", "created_at"),
    )


class AccountRecoveryRequest(Base):
    """A verified account owner asking an admin to restore a soft-deleted account."""

    __tablename__ = "account_recovery_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    role: Mapped[RoleName] = mapped_column(_enum(RoleName, "account_recovery_role_name"))
    status: Mapped[AccountRecoveryStatus] = mapped_column(
        _enum(AccountRecoveryStatus, "account_recovery_status")
    )
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    email_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_email_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_account_recovery_pending_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_account_recovery_status_created", "status", "created_at"),
    )


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
    """Google services a user let Nexus use at sign-in. Tokens are encrypted with a key derived
    from SECRET_KEY (see nexus_common.google)."""

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
    """A credential Nexus holds on a user's behalf, such as their LiteLLM virtual key."""

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
    # "chat" for a conversation in the chat list; "routine" for the chat a routine runs in,
    # which stays out of the list (its runs show on the Routines page instead).
    kind: Mapped[str] = mapped_column(String(20), default="chat", server_default="chat")
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
    # Tools Nexus used for an assistant reply: id, name, args, status, ms, summary.
    steps: Mapped[list | None] = mapped_column(JSON)
    # What Nexus remembered for an assistant reply: memory ids, and the entities as
    # {id, name, type}. Shown as chips under the reply and lit up in the orb.
    recalled: Mapped[dict | None] = mapped_column(JSON)
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


class TrashItem(Base):
    """A restorable user-owned item. The original row stays intact during its retention window."""

    __tablename__ = "trash_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("kind", "entity_id", name="uq_trash_items_kind_entity"),
        Index("ix_trash_items_user_expiry", "user_id", "expires_at"),
    )


def _tsv(column: str = "content"):
    return mapped_column(
        TSVECTOR, Computed(f"to_tsvector('english', {column})", persisted=True), deferred=True
    )


# --- Library -----------------------------------------------------------------------------------


class Folder(Base):
    """A project's folder in the library: the files made for one piece of work, such as a
    rocket model's .3mf, .step and .stl. Files Nexus and its teams make are filed into one on
    their own (nexus_common.folders); the user can rename it and move files in or out. Outside
    the orb each folder is one moon instead of a crystal per file."""

    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    # The software project its files belong to (a dev team's screenshots), if any.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    # The team that started it: a kept team's later runs file into the same folder.
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_systems.id", ondelete="SET NULL")
    )
    # The chat it started in.
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Last time a file went in.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_folders_user", "user_id", "updated_at"),)


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
    # The project folder it is filed in; uploads and loose files have none.
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL")
    )
    graph_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_files_user_created", "user_id", "created_at"),
        Index("ix_files_user_sha", "user_id", "sha256"),
        Index("ix_files_folder", "folder_id"),
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
    """Nexus's short impression of a user, rebuilt from their memories, plus their memory
    settings."""

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    # When false, Nexus neither learns new memories from chats nor reads old ones.
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
    # Pinned memories are always in the profile and Nexus never changes or drops them itself.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # "assistant" when learned from a chat, "user" when added or edited on the Memory page.
    source: Mapped[str] = mapped_column(String(16), default="assistant")
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
    # When its entities were last linked in the knowledge graph; null means still to do.
    graph_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    actor: Mapped[str] = mapped_column(String(16))  # assistant or user
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
    graph_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


# --- Knowledge graph ---------------------------------------------------------------------------


class Entity(Base):
    """Someone or something a user's memories are about: a person, project, organisation,
    place, topic or tool. Each user has one entity for themselves (is_self), which every
    relation about "the user" starts from."""

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # person, project, organisation, place, topic, tool or other (nexus_common.graph.TYPES)
    type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    # Other names it goes by, lower-cased, for matching ("ust global", "the habit app").
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    is_self: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', name || ' ' || summary)", persisted=True),
        deferred=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Last time a chat, memory or file mentioned it.
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_entities_user", "user_id"),
        Index("ix_entities_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_entities_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Relation(Base):
    """A fact linking two entities ("Shivam works at UST"). A fact that stops being true gets
    valid_to instead of being deleted, so the graph keeps what used to be true."""

    __tablename__ = "relations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    src_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    dst_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    # A short verb phrase, lower case: "works at", "uses", "is planning".
    predicate: Mapped[str] = mapped_column(String(80))
    # The same fact as a sentence, for the prompt and the Memory page.
    fact: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source_chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL")
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_relations_src", "src_id"),
        Index("ix_relations_dst", "dst_id"),
        Index("ix_relations_user", "user_id"),
    )


class Mention(Base):
    """Ties an entity to one memory, past-chat summary, file or chat that is about it. Exactly
    one of the four is set; deleting that row deletes the mention, and an entity nothing
    mentions any more is pruned (nexus_common.graph.prune)."""

    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE")
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE")
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "memory_id",
            "episode_id",
            "file_id",
            "chat_id",
            name="uq_mentions",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_mentions_memory", "memory_id"),
        Index("ix_mentions_episode", "episode_id"),
        Index("ix_mentions_file", "file_id"),
        Index("ix_mentions_chat", "chat_id"),
    )


class RecallQuestion(Base):
    """A question whose answer is one memory or past-chat summary, written by the memory model
    for the recall check on the Admin page. It goes when its target goes."""

    __tablename__ = "recall_questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    # Exactly one of these is set.
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE")
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_recall_questions_user", "user_id"),)


class RecallEval(Base):
    """One run of the recall check: how often each way of recalling finds the memory or past
    chat a question is about. `scores` maps a mode to {n, hits, hit_rate, mrr}; `misses` lists
    the questions the standard mode missed, for the owner to read."""

    __tablename__ = "recall_evals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # running, done or failed
    status: Mapped[str] = mapped_column(String(16))
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    misses: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_recall_evals_user", "user_id", "created_at"),)


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
    # library (shipped), editor, import, or run:<id> for a skill Nexus learned from a run.
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
    """A team of sub-agents Nexus designed. The spec (see nexus_common.spec) is the whole
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
    # the user's account and Nexus no longer pushes to it.
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
    # New apps are public: anyone with the link opens them (the code stays in a private repo).
    # A private one opens only for its owner and Nexus's owner, after signing in to Nexus.
    public: Mapped[bool] = mapped_column(Boolean, default=True)
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


class Link(Base):
    """A pointer in the library to something that lives elsewhere: a project's GitHub repo, a
    file, folder or pull request in it, a Google Doc, a running app, a web page. Only the address
    and a snapshot for the preview are kept, never the thing itself. One row per address: saving
    the same address again updates it."""

    __tablename__ = "links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2048))
    # What it points to (nexus_common.links.LINK_KINDS), worked out from the address.
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(300))
    # Why it's in the library, in a sentence: shown under the title and searched.
    note: Mapped[str | None] = mapped_column(Text)
    # Who added it: the user, Nexus in a chat, or a team's run.
    source: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    # Where it came from, if anywhere. Links stay in the library when these are deleted.
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    # What the preview shows (a repo's description and README, a file's text, a pull request's
    # state), fetched when the link was saved or last refreshed.
    preview: Mapped[dict | None] = mapped_column(JSON)
    preview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "url"),
        Index("ix_links_user_updated", "user_id", "updated_at"),
    )


class MessageLink(Base):
    """A link Nexus or a team saved while writing a message, shown on that message."""

    __tablename__ = "message_links"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    link_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("links.id", ondelete="CASCADE"), primary_key=True
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


# --- Self-review -------------------------------------------------------------------------------


class ReviewStatus(enum.StrEnum):
    running = "running"
    done = "done"
    failed = "failed"


class ProposalKind(enum.StrEnum):
    # A shared skill, active for everyone once approved.
    skill = "skill"
    # Text added to the global or a role's standing instructions.
    instruction = "instruction"
    # A change to one runtime setting on the Configuration tab.
    setting = "setting"
    # A new tool for agents: a spec, opened as a pull request on the Nexus repository.
    tool = "tool"
    # Any other change to Nexus's code, also a spec in a pull request.
    code = "code"


class ProposalStatus(enum.StrEnum):
    pending = "pending"
    # Approved; a pull request is being opened.
    applying = "applying"
    applied = "applied"
    denied = "denied"
    # Approved, but applying it failed; `error` says why and it can be tried again.
    failed = "failed"
    # Applied, then undone by the owner.
    reverted = "reverted"


class ImprovementReview(Base):
    """One self-review: what Nexus looked at (as counts and anonymous topics only) and how many
    proposals came of it. `findings` is shown to the owner as is, so it never holds chat text,
    names or user ids."""

    __tablename__ = "improvement_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[ReviewStatus] = mapped_column(_enum(ReviewStatus, "review_status"))
    # schedule or manual
    trigger: Mapped[str] = mapped_column(String(16))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    findings: Mapped[dict] = mapped_column(JSON, default=dict)
    proposals: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_improvement_reviews_created", "created_at"),)


class Proposal(Base):
    """An improvement Nexus drafted for the owner to approve or deny. Nothing in it applies
    until they approve; then `result` records what changed, so it can be undone."""

    __tablename__ = "improvement_proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("improvement_reviews.id", ondelete="SET NULL")
    )
    kind: Mapped[ProposalKind] = mapped_column(_enum(ProposalKind, "proposal_kind"))
    status: Mapped[ProposalStatus] = mapped_column(_enum(ProposalStatus, "proposal_status"))
    title: Mapped[str] = mapped_column(String(200))
    # Why, in a few sentences.
    summary: Mapped[str] = mapped_column(Text, default="")
    # The figures behind it, one line each ("web_fetch failed 14 of 60 calls").
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    # low, medium or high
    impact: Mapped[str] = mapped_column(String(16), default="medium")
    effort: Mapped[str] = mapped_column(String(16), default="medium")
    # What to apply: the skill's SKILL.md, the instruction text, the setting, or the spec.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # kind and title, normalised: the same idea isn't proposed twice.
    fingerprint: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The owner's reason for a denial, which later reviews read.
    note: Mapped[str | None] = mapped_column(Text)
    # skill_id, the instruction or setting before the change, pr_url.
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_improvement_proposals_status_created", "status", "created_at"),
        Index("ix_improvement_proposals_fingerprint", "fingerprint"),
    )


class RoutineRunStatus(enum.StrEnum):
    running = "running"
    ok = "ok"
    # Ran, found nothing new, and told nobody (routine.only_if_new).
    quiet = "quiet"
    failed = "failed"
    # Not run: the person's budget couldn't cover it.
    skipped = "skipped"


class Routine(Base):
    """A saved prompt that runs on a schedule as its owner, in a hidden chat of its own (kind
    "routine", kept out of the chat list). nexus_common/routines.py has the schedule rules, the
    orchestrator's routines.py runs them."""

    __tablename__ = "routines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text)
    # An RFC 5545 RRULE without DTSTART, e.g. "FREQ=WEEKLY;BYDAY=SU;BYHOUR=18;BYMINUTE=0".
    schedule: Mapped[str] = mapped_column(String(300))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # Where a finished run is announced besides its chat: any of "email", "whatsapp".
    delivery: Mapped[list] = mapped_column(JSON, default=list)
    # When true, a run with nothing new to say is stored as quiet and announced nowhere.
    only_if_new: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # onboarding, pack:<id>, chat or user.
    source: Mapped[str] = mapped_column(String(40), default="user")
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failed runs in a row; at ROUTINE_MAX_FAILURES the routine pauses itself.
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_routines_due", "enabled", "next_run_at"),
        Index("ix_routines_user", "user_id"),
    )


class RoutineRun(Base):
    __tablename__ = "routine_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    routine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routines.id", ondelete="CASCADE"))
    # The time it was due, which with the routine id makes each run unique.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[RoutineRunStatus] = mapped_column(_enum(RoutineRunStatus, "routine_run_status"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Channels it was announced on, e.g. ["email"].
    delivered: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("routine_id", "scheduled_for", name="uq_routine_runs_slot"),
        Index("ix_routine_runs_routine_started", "routine_id", "started_at"),
    )


class PushSubscription(Base):
    """One browser or phone that allowed notifications (nexus_common/push.py)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_push_subscriptions_user", "user_id"),)


class FeedbackKind(enum.StrEnum):
    problem = "problem"
    idea = "idea"
    praise = "praise"


class Feedback(Base):
    """What someone told the owner from the app's Send feedback form. Listed on the Admin page,
    emailed to owners, and read by the self-review (without the sender) as evidence."""

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[FeedbackKind] = mapped_column(_enum(FeedbackKind, "feedback_kind"))
    message: Mapped[str] = mapped_column(Text)
    # The page they were on, e.g. /routines.
    page: Mapped[str | None] = mapped_column(String(300))
    # Screenshots sent with it: [{"key", "name", "content_type", "size"}], bytes in storage
    # under feedback_shot_key().
    screenshots: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Set when the owner marks it handled.
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_feedback_created", "created_at"),)


class NotificationKind(enum.StrEnum):
    # A team finished; featured_* points at the one result worth opening.
    result = "result"
    # A team stopped or failed.
    failed = "failed"
    # A step is waiting for the person's approval.
    approval = "approval"
    # A timer or alarm widget is ringing.
    timer = "timer"
    # A reminder widget is due.
    reminder = "reminder"
    # Something the assistant put on the main screen with its screen tool.
    info = "info"
    access_request = "access_request"
    account_recovery = "account_recovery"
    invite_sent = "invite_sent"


class Notification(Base):
    """A notice on the main screen (toast, bell tray) and, when no tab is open, on the phone
    (nexus_common/notices.py). Kept so the tray survives a reload and another device."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(_enum(NotificationKind, "notification_kind"))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    # The result to open: a file (shown in the viewer) or a link (a live app, a repo).
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("links.id", ondelete="SET NULL"))
    # Where Open goes in the app, e.g. /c/<chat id> or /agents/<system id>.
    url: Mapped[str] = mapped_column(String(300), default="/")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)


class Widget(Base):
    """A widget on the main screen: a timer, alarm, stopwatch, reminder, clock or note
    (nexus_common/widgets.py). The server keeps the time, so a timer keeps running with the tab
    closed and rings on every device; fires_at is when the worker next has to act."""

    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(120), default="")
    # Kind-specific: a timer's ends_at and status, an alarm's time and repeat, a note's text.
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Order on the dock, lowest first.
    slot: Mapped[int] = mapped_column(Integer, default=0)
    # Where it was dragged to on a laptop's main screen: {"x", "y"}, each 0..1 across the room
    # left beside the card, so 1 is flush right or bottom on any screen. None = in the dock.
    pos: Mapped[dict | None] = mapped_column(JSONB)
    # The chat that asked for it, if any.
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    fires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_widgets_user", "user_id", "slot"),
        Index("ix_widgets_fires_at", "fires_at"),
    )


class CustomWidget(Base):
    """One version of a custom widget: a small HTML app a widget-builder team wrote, shown in a
    locked-down frame on the main screen (nexus_common/custom_widgets.py). Each edit is a new
    row with the same root_id, so a change can be rolled back; the Widget on the dock points at
    the version it shows (state.custom_id)."""

    __tablename__ = "custom_widgets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The first version's id; every version of one widget shares it.
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(120))
    html: Mapped[str] = mapped_column(Text)
    # {"size": "s" | "m" | "l", "hosts": [...], "data": [...], "about": "..."}
    manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_custom_widgets_root", "root_id", "version"),)


class ScreenState(Base):
    """How a user's main screen is laid out (nexus_common/screen.py): the orb, plain lists or a
    board of widgets, and what's open over it. One row per user, so a reload or a second device
    shows the same screen. overrides are the person's own recent changes, which the assistant's
    screen tool leaves alone unless asked."""

    __tablename__ = "screen_states"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # orb, list or dashboard
    mode: Mapped[str] = mapped_column(String(16), default="orb")
    # What's open over the main area: {"kind": "file" | "widget", "id", "chat_id", "at"}.
    focus: Mapped[dict | None] = mapped_column(JSONB)
    # [{"key": "mode" | "move:<widget>" | "focus:file:<id>", "at": iso}], newest last.
    overrides: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
