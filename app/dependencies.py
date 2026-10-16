"""Wires repositories into services for FastAPI's dependency injection."""

from fastapi import Depends

from app.core.config import Config, get_config
from app.core.database import Database, get_database
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.settings_repository import SettingsRepository
from app.services.agent_service import AgentService
from app.services.conversation_service import ConversationService
from app.services.llm_service import LLMService
from app.services.plivo_service import PlivoService


def get_agent_service(db: Database = Depends(get_database)) -> AgentService:
    return AgentService(SettingsRepository(db))


def get_llm_service(config: Config = Depends(get_config)) -> LLMService:
    return LLMService(config)


def get_conversation_service(
    db: Database = Depends(get_database),
    agent: AgentService = Depends(get_agent_service),
    llm: LLMService = Depends(get_llm_service),
) -> ConversationService:
    return ConversationService(ConversationRepository(db), agent, llm)


def get_plivo_service(config: Config = Depends(get_config)) -> PlivoService:
    return PlivoService(config)
