"""Request and response models for the REST API."""

from pydantic import BaseModel, Field

# The agent configuration is a flat {key: text} object whose keys come from
# config/agent.default.json, so adding a key there adds a field everywhere.
AgentConfig = dict[str, str]


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ReplyOut(BaseModel):
    reply: str


class Message(BaseModel):
    role: str
    content: str
    created_at: str


class ConversationBase(BaseModel):
    id: str
    channel: str
    created_by: str | None = None
    call_uuid: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    started_at: str
    ended_at: str | None = None


class ConversationSummary(ConversationBase):
    message_count: int
    first_user_message: str | None


class ConversationDetail(ConversationBase):
    messages: list[Message]


class ConversationStarted(BaseModel):
    id: str
    greeting: str


class CallIn(BaseModel):
    to: str = Field(min_length=7, max_length=24, examples=["+919876543210"])


class CallOut(BaseModel):
    request_uuid: str | None
    message: str


class TelephonyStatus(BaseModel):
    provider: str
    configured: bool
    phone_number: str | None
    public_base_url: str | None


class Health(BaseModel):
    status: str


class SystemStatus(BaseModel):
    mode: str
    model: str
    telephony_configured: bool


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class User(BaseModel):
    username: str
    role: str
    created_at: str
    created_by: str | None = None
    last_login_at: str | None = None


class LoginOut(BaseModel):
    user: User
    token: str = Field(description="Session token, also set as an HttpOnly cookie. Use as `Authorization: Bearer <token>`.")
    expires_in: int = Field(description="Seconds until the session expires")


class SetupStatus(BaseModel):
    has_users: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)
    role: str = Field(default="user", pattern="^(admin|user)$")


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class Ok(BaseModel):
    ok: bool = True
