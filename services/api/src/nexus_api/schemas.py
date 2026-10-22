import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from nexus_common.models import (
    ASSISTANT_NAME_MAX,
    AccessRequestStatus,
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
    # None until the user names their assistant, which the app asks for after first sign-in.
    assistant_name: str | None
    budget: BudgetOut
    # Set while an admin has maintenance mode on.
    notice: str | None = None
    # Owners and admins: access requests waiting for a decision. None for everyone else.
    pending_access: int | None = None


def _assistant_name(value: str) -> str:
    name = " ".join(value.split())
    if not name or not all(c.isalnum() or c in " .'-" for c in name) or not name[0].isalnum():
        raise ValueError("Use letters, numbers, spaces, dots, hyphens or apostrophes")
    return name


class MeIn(BaseModel):
    assistant_name: Annotated[
        str, Field(min_length=1, max_length=ASSISTANT_NAME_MAX), AfterValidator(_assistant_name)
    ]


class SessionOut(BaseModel):
    """A browser signed in as the user, for "signed-in devices"."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip: str | None
    current: bool = False


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


class GoogleAccessOut(BaseModel):
    # Sign-in is Google and asks for services.
    available: bool
    requested: list[str]
    granted: list[str]
    connected_at: datetime | None


class AuthConfigOut(BaseModel):
    oidc_enabled: bool
    dev_login_enabled: bool
    # Someone refused for lack of an invite may ask for one.
    access_requests: bool


def _note(value: str | None) -> str | None:
    # Keep line breaks; drop other control characters, which have no business in a note.
    if value is None:
        return None
    cleaned = "".join(c for c in value if c in "\n\t" or c.isprintable()).strip()
    return cleaned or None


Note = Annotated[str | None, Field(default=None, max_length=500), AfterValidator(_note)]


class AccessRequestIn(BaseModel):
    note: Note = None


class AccessStateOut(BaseModel):
    """Where the person on the login page stands, for the address they just proved they own.

    can_request: no invite and nothing pending. pending: waiting for an admin. approved: they
    have an invite now, so signing in works. denied: refused recently; may ask again after
    retry_after."""

    email: str
    name: str | None
    avatar_url: str | None
    status: Literal["can_request", "pending", "approved", "denied"]
    requested_at: datetime | None = None
    retry_after: datetime | None = None
    # Whether they'll be emailed when approved (SMTP is set up).
    email_on_approval: bool = False


class AccessRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    avatar_url: str | None
    note: str | None
    status: AccessRequestStatus
    ip: str | None
    user_agent: str | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None
    decided_by_name: str | None = None
    # Earlier requests from the same address, e.g. after a denial.
    previous: int = 0


class AccessRequestListOut(BaseModel):
    requests: list[AccessRequestOut]
    pending: int
    # Whether new requests are also emailed (SMTP is set up); otherwise only shown here.
    email_notifications: bool
    # Whether the login page offers requests at all.
    enabled: bool


class ApproveIn(BaseModel):
    role: RoleName = RoleName.member
    budget_usd: Usd | None = None


class ApproveOut(BaseModel):
    request: AccessRequestOut
    user: AdminUserOut


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


class ProjectOut(BaseModel):
    """A software project in the Library: one card linking to its GitHub repo."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    stack: str | None
    repo_url: str | None
    branch: str | None
    merged: bool
    transferred_to: str | None
    last_commit: str | None
    last_commit_message: str | None
    last_commit_at: datetime | None
    live_url: str | None
    app_state: str | None
    public: bool
    screenshot_file_id: uuid.UUID | None
    chat_id: uuid.UUID | None
    updated_at: datetime


class LinkOut(BaseModel):
    """A link in the library: a pointer to a GitHub repo, a Google Doc, an app or a page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    kind: str
    title: str
    note: str | None
    source: str
    chat_id: uuid.UUID | None
    run_id: uuid.UUID | None
    project_id: uuid.UUID | None
    preview: dict | None
    preview_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=2000)
    chat_id: uuid.UUID | None = None


class LinkUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=2000)


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
    steps: list[dict] | None = None
    created_at: datetime
    attachments: list[FileBrief] = []
    # Links Nexus or a team saved while writing it (a repo, an app, a Google Doc).
    links: list[LinkOut] = []


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


class MaintenanceIn(BaseModel):
    enabled: bool
    message: str | None = Field(default=None, max_length=300)
