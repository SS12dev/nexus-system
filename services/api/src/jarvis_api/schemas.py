import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from jarvis_common.models import (
    FileStatus,
    MemoryKind,
    MessageRole,
    MessageStatus,
    RoleName,
    UserStatus,
)

Usd = Annotated[
    Decimal, Field(ge=0, le=100000), AfterValidator(lambda v: v.quantize(Decimal("0.01")))
]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    avatar_url: str | None
    role: RoleName
    status: UserStatus
    created_at: datetime
    last_login_at: datetime | None


class BudgetOut(BaseModel):
    max_usd: Decimal
    duration: str
    spend_usd: float | None = None


class MeOut(UserOut):
    budget: BudgetOut


class AdminUserOut(UserOut):
    budget: BudgetOut


class InviteIn(BaseModel):
    email: EmailStr
    role: RoleName = RoleName.member
    budget_usd: Usd | None = None


class UserUpdateIn(BaseModel):
    role: RoleName | None = None
    status: UserStatus | None = None
    budget_usd: Usd | None = None


class AuthConfigOut(BaseModel):
    oidc_enabled: bool
    dev_login_enabled: bool


class DevLoginIn(BaseModel):
    email: EmailStr
    token: str


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ChatCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class FileBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    content_type: str
    size: int
    status: FileStatus
    origin: str = "upload"
    version: int = 1


class FileOut(FileBrief):
    error: str | None
    parser: str | None
    chunk_count: int
    char_count: int
    chat_id: uuid.UUID | None
    created_at: datetime


class FileUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class FileTextOut(BaseModel):
    text: str
    offset: int
    total_chars: int


class SearchHitOut(BaseModel):
    file: FileBrief
    heading: str | None
    snippet: str
    score: float


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chat_id: uuid.UUID
    role: MessageRole
    content: str
    status: MessageStatus
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    trace_id: str | None
    error: str | None
    created_at: datetime
    attachments: list[FileBrief] = []


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class SendMessageOut(BaseModel):
    chat: ChatOut
    user_message: MessageOut
    assistant_message: MessageOut


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: MemoryKind
    content: str
    confidence: float
    pinned: bool
    source: str
    source_chat_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    reinforced_at: datetime


class MemoryEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    op: str
    actor: str
    content: str | None
    message_id: uuid.UUID | None
    created_at: datetime


class MemoryCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    kind: MemoryKind = MemoryKind.fact
    pinned: bool = False


class MemoryUpdateIn(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    kind: MemoryKind | None = None
    pinned: bool | None = None


class ProfileOut(BaseModel):
    content: str
    memory_enabled: bool
    built_at: datetime | None
    stale: bool
    memory_count: int


class ProfileUpdateIn(BaseModel):
    memory_enabled: bool


# --- Phase 3: skills, instructions, agent systems, approvals -------------------------------


class SkillCreateIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000, description="The SKILL.md text")
    files: dict[str, str] = Field(default_factory=dict)
    # personal (yours) or, for admins, domain (shared with everyone).
    scope: str = "personal"


class SkillUpdateIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    files: dict[str, str] | None = None
    note: str | None = Field(default=None, max_length=500)


class SkillStatusIn(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class SkillRollbackIn(BaseModel):
    version: int = Field(ge=1)


class InstructionIn(BaseModel):
    content: str = Field(max_length=20_000)


class RunIn(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    budget_usd: float | None = Field(default=None, gt=0, le=1000)
    chat_id: uuid.UUID | None = None


class TemplateIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    args: dict | None = None
    note: str | None = Field(default=None, max_length=1000)


class GrantsIn(BaseModel):
    capabilities: list[str] = Field(default_factory=list)
