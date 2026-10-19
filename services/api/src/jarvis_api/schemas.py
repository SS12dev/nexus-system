import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from jarvis_common.models import MessageRole, MessageStatus, RoleName, UserStatus

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


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class SendMessageOut(BaseModel):
    chat: ChatOut
    user_message: MessageOut
    assistant_message: MessageOut
