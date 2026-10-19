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


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=False, length=16, validate_strings=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[UserStatus] = mapped_column(_enum(UserStatus, "user_status"))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", lazy="selectin")

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
