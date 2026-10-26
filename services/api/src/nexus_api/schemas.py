import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, model_validator

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
    # None until they finish or skip the setup interview, which the app shows until then.
    onboarded_at: datetime | None = None
    timezone: str | None = None
    profile: dict = {}
    phone: str | None = None
    share_usage: bool = True
    # Which routine notice channels this Nexus can send on.
    channels: list[str] = []
    # The terms version they agreed to, and the current one; the app asks again when they
    # differ (never for owners, whose own terms they are).
    terms_version: str | None = None
    terms_current: str = ""
    terms_needed: bool = False


def _assistant_name(value: str) -> str:
    name = " ".join(value.split())
    if not name or not all(c.isalnum() or c in " .'-" for c in name) or not name[0].isalnum():
        raise ValueError("Use letters, numbers, spaces, dots, hyphens or apostrophes")
    return name


AssistantName = Annotated[
    str, Field(min_length=1, max_length=ASSISTANT_NAME_MAX), AfterValidator(_assistant_name)
]


def _phone(value: str) -> str:
    digits = "".join(c for c in value if c.isdigit() or c == "+")
    if digits and not re.fullmatch(r"\+[1-9]\d{7,14}", digits):
        raise ValueError("Use the international format with the country code, like +919812345678")
    return digits


Phone = Annotated[str, Field(max_length=32), AfterValidator(_phone)]
Time = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class MeIn(BaseModel):
    """Any of these; fields left out stay as they are. An empty phone removes it."""

    assistant_name: AssistantName | None = None
    timezone: str | None = Field(default=None, max_length=64)
    phone: Phone | None = None
    share_usage: bool | None = None
    # The first-run tour was finished or skipped; kept in profile so it shows once per person.
    tour_done: bool | None = None


class StyleIn(BaseModel):
    length: Literal["short", "balanced", "detailed"] = "balanced"
    tone: Literal["casual", "professional"] = "casual"
    language: str = Field(default="", max_length=40)


class OnboardingRoutineIn(BaseModel):
    # "<pack id>/<routine id>"
    key: str = Field(max_length=100)
    time: Time | None = None


class OnboardingIn(BaseModel):
    """The setup interview. Every step can be skipped, except agreeing to the terms."""

    # The Terms of Service and Privacy Policy version they agreed to on the first screen.
    accept_terms: str | None = Field(default=None, max_length=20)

    assistant_name: AssistantName | None = None
    roles: list[str] = Field(default=[], max_length=5)
    # Their work in their own words, e.g. "PhD student in marine biology".
    role_note: str = Field(default="", max_length=300)
    interests: list[str] = Field(default=[], max_length=20)
    # Anything else: topics they follow, their city, their stack.
    about: str = Field(default="", max_length=1000)
    timezone: str | None = Field(default=None, max_length=64)
    day_start: Time | None = None
    style: StyleIn | None = None
    routines: list[OnboardingRoutineIn] = Field(default=[], max_length=20)
    delivery: list[Literal["push", "email", "whatsapp"]] = []
    phone: Phone | None = None


class OnboardingOut(BaseModel):
    me: "MeOut"
    # The welcome chat that opens when they're done.
    chat_id: uuid.UUID | None = None
    routines: int = 0


class PackRoutineOut(BaseModel):
    key: str
    name: str
    prompt: str
    # The RRULE at the person's day start, and in words.
    schedule: str
    schedule_text: str
    time: str
    # As the pack wrote it: "HH:MM", "day_start" or "day_start+30" (minutes after day start).
    at: str
    needs: list[str]
    only_if_new: bool
    default_on: bool


class ChoiceOut(BaseModel):
    id: str
    label: str
    blurb: str = ""


class PackOut(BaseModel):
    id: str
    label: str
    kind: str
    blurb: str
    # Role or interest ids that select it besides its own id.
    match: list[str]
    prompts: list[str]
    routines: list[PackRoutineOut]


class ProfilesOut(BaseModel):
    roles: list[ChoiceOut]
    interests: list[ChoiceOut]
    packs: list[PackOut]


class RoutineIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=4000)
    schedule: str = Field(min_length=1, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    delivery: list[Literal["push", "email", "whatsapp"]] = []
    only_if_new: bool = False
    enabled: bool = True


class RoutineUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    schedule: str | None = Field(default=None, min_length=1, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    delivery: list[Literal["push", "email", "whatsapp"]] | None = None
    only_if_new: bool | None = None
    enabled: bool | None = None


class RoutineOut(BaseModel):
    id: uuid.UUID
    name: str
    prompt: str
    schedule: str
    schedule_text: str
    timezone: str
    delivery: list[str]
    only_if_new: bool
    enabled: bool
    source: str
    chat_id: uuid.UUID | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    failures: int
    created_at: datetime | None
    # The latest run's status, if it has run.
    last_status: str | None = None


class RoutineRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scheduled_for: datetime
    status: str
    message_id: uuid.UUID | None
    input_tokens: int | None
    output_tokens: int | None
    delivered: list[str]
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class SchedulePreviewIn(BaseModel):
    schedule: str = Field(min_length=1, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)


class SchedulePreviewOut(BaseModel):
    ok: bool
    text: str = ""
    next: list[datetime] = []
    error: str | None = None


class TermsIn(BaseModel):
    version: str = Field(max_length=20)


class DeleteAccountIn(BaseModel):
    # Their own email address, typed again, so it can't happen by accident.
    confirm: str = Field(max_length=320)


class ClientErrorIn(BaseModel):
    """A page that crashed in the browser (the web app's ErrorBoundary)."""

    message: str = Field(max_length=1000)
    stack: str = Field(default="", max_length=4000)
    page: str = Field(default="", max_length=300)


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
    recalled: dict | None = None
    created_at: datetime
    attachments: list[FileBrief] = []
    # Links Nexus or a team saved while writing it (a repo, an app, a Google Doc).
    links: list[LinkOut] = []


class SendMessageIn(BaseModel):
    # May be empty when files are attached (a screenshot on its own).
    content: str = Field(default="", max_length=20000)
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _something_to_send(self) -> "SendMessageIn":
        if not self.content.strip() and not self.file_ids:
            raise ValueError("Write a message or attach a file")
        return self


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


class ProposalDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class MaintenanceIn(BaseModel):
    enabled: bool
    message: str | None = Field(default=None, max_length=300)


class PushKeysIn(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=2000, pattern=r"^https://")
    keys: PushKeysIn


class PushEndpointIn(BaseModel):
    endpoint: str = Field(max_length=2000)


class PushStateOut(BaseModel):
    enabled: bool
    public_key: str | None = None
    # Devices this person gets notifications on.
    devices: int = 0


class FeedbackIn(BaseModel):
    kind: Literal["problem", "idea", "praise"] = "idea"
    message: str = Field(min_length=3, max_length=4000)
    page: str = Field(default="", max_length=300)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    message: str
    page: str | None
    done_at: datetime | None
    created_at: datetime
    # Who sent it (owners only see this on the Admin page).
    email: str | None = None
    name: str | None = None
