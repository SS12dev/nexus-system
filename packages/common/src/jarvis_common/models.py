"""Database schema. Alembic migrations in services/api own the DDL; keep the two in step."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
