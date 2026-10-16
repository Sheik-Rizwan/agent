"""Business logic for conversations, shared by the web UI and phone calls."""

from app.core.config import Config
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.repositories.conversation_repository import ConversationRepository
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService


def summarize(conversation: dict) -> dict:
    """List view of a conversation: everything except the message bodies."""
    messages = conversation.get("messages", [])
    first_user = next((m["content"] for m in messages if m["role"] == "user"), None)
    summary = {k: v for k, v in conversation.items() if k != "messages"}
    return {**summary, "message_count": len(messages), "first_user_message": first_user}


class ConversationService:
    def __init__(self, repo: ConversationRepository, agent: AgentService, llm: LLMService, config: Config):
        self.repo = repo
        self.agent = agent
        self.llm = llm
        self.config = config

    def start(
        self,
        channel: str,
        created_by: str | None = None,
        call_uuid: str | None = None,
        from_number: str | None = None,
        to_number: str | None = None,
    ) -> tuple[dict, str]:
        """Create a conversation and record the agent's greeting. Returns (conversation, greeting)."""
        conversation = self.repo.create(channel, created_by, call_uuid, from_number, to_number)
        greeting = self.agent.get()["greeting"]
        self.repo.add_message(conversation["id"], "assistant", greeting)
        return conversation, greeting

    def get(self, conversation_id: str) -> dict:
        conversation = self.repo.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    def exists(self, conversation_id: str) -> bool:
        return self.repo.get(conversation_id) is not None

    def get_by_call_uuid(self, call_uuid: str) -> dict | None:
        return self.repo.get_by_call_uuid(call_uuid)

    def list(self, limit: int = 50) -> list[dict]:
        return [summarize(c) for c in self.repo.list(limit)]

    async def reply(self, conversation_id: str, text: str) -> str:
        """Store the caller's message, ask the LLM for a reply, store and return it."""
        conversation = self.get(conversation_id)
        if conversation.get("ended_at"):
            raise ConflictError("This conversation has ended")
        text = text.strip()
        if not text:
            raise ValidationError("Message is empty")

        if not self.repo.add_message(conversation_id, "user", text):
            raise NotFoundError("Conversation not found")
        history = self.get(conversation_id)["messages"][-self.config.llm_max_history_messages:]
        reply = await self.llm.generate_reply(self.agent.get()["system_prompt"], history)
        if not self.repo.add_message(conversation_id, "assistant", reply):
            raise NotFoundError("Conversation was deleted")
        return reply

    def end(self, conversation_id: str) -> None:
        self.get(conversation_id)
        self.repo.mark_ended(conversation_id)

    def end_by_call_uuid(self, call_uuid: str) -> None:
        conversation = self.repo.get_by_call_uuid(call_uuid)
        if conversation is not None:
            self.repo.mark_ended(conversation["id"])

    def delete(self, conversation_id: str) -> None:
        self.get(conversation_id)
        self.repo.delete(conversation_id)
