"""Request and response models for the REST API."""

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    agent_name: str = Field(min_length=1, max_length=60)
    greeting: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=20000)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ReplyOut(BaseModel):
    reply: str


class Message(BaseModel):
    role: str
    content: str
    created_at: str


class ConversationSummary(BaseModel):
    id: int
    channel: str
    call_uuid: str | None
    from_number: str | None
    to_number: str | None
    started_at: str
    ended_at: str | None
    message_count: int
    first_user_message: str | None


class ConversationDetail(BaseModel):
    id: int
    channel: str
    call_uuid: str | None
    from_number: str | None
    to_number: str | None
    started_at: str
    ended_at: str | None
    messages: list[Message]


class ConversationStarted(BaseModel):
    id: int
    greeting: str


class CallIn(BaseModel):
    to: str = Field(min_length=7, max_length=20, examples=["+919876543210"])


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
    mode: str
    model: str
    telephony_configured: bool


class Ok(BaseModel):
    ok: bool = True
