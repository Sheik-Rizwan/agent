"""Business logic for conversations, shared by the web UI and phone calls."""

from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.conversation_repository import ConversationRepository
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService

MAX_HISTORY_MESSAGES = 40


class ConversationService:
    def __init__(self, repo: ConversationRepository, agent: AgentService, llm: LLMService):
        self.repo = repo
        self.agent = agent
        self.llm = llm

    def start(
        self,
        channel: str = "web",
        call_uuid: str | None = None,
        from_number: str | None = None,
        to_number: str | None = None,
    ) -> tuple[dict, str]:
        """Create a conversation and record the agent's greeting. Returns (conversation, greeting)."""
        conversation_id = self.repo.create(channel, call_uuid, from_number, to_number)
        greeting = self.greeting()
        self.repo.add_message(conversation_id, "assistant", greeting)
        return self.repo.get(conversation_id), greeting

    def get(self, conversation_id: int) -> dict:
        conversation = self.repo.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    def exists(self, conversation_id: int) -> bool:
        return self.repo.get(conversation_id) is not None

    def greeting(self) -> str:
        return self.agent.get()["greeting"]

    def get_by_call_uuid(self, call_uuid: str) -> dict | None:
        return self.repo.get_by_call_uuid(call_uuid)

    def get_with_messages(self, conversation_id: int) -> dict:
        conversation = self.get(conversation_id)
        return {**conversation, "messages": self.repo.list_messages(conversation_id)}

    def list(self, limit: int = 50) -> list[dict]:
        return self.repo.list(limit)

    async def reply(self, conversation_id: int, text: str) -> str:
        """Store the caller's message, ask the LLM for a reply, store and return it."""
        self.get(conversation_id)
        text = text.strip()
        if not text:
            raise ValidationError("Message is empty")

        self.repo.add_message(conversation_id, "user", text)
        history = self.repo.list_messages(conversation_id)[-MAX_HISTORY_MESSAGES:]
        reply = await self.llm.generate_reply(self.agent.get()["system_prompt"], history)
        self.repo.add_message(conversation_id, "assistant", reply)
        return reply

    def end(self, conversation_id: int) -> None:
        self.get(conversation_id)
        self.repo.mark_ended(conversation_id)

    def end_by_call_uuid(self, call_uuid: str) -> None:
        conversation = self.repo.get_by_call_uuid(call_uuid)
        if conversation is not None:
            self.repo.mark_ended(conversation["id"])

    def delete(self, conversation_id: int) -> None:
        self.get(conversation_id)
        self.repo.delete(conversation_id)
