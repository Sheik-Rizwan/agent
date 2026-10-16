"""Wires repositories -> services -> controllers via FastAPI's dependency injection,
and provides the authentication guards used by the controllers.

    get_current_user  any logged-in user (cookie session or Bearer token)
    require_admin     logged-in user with role "admin"
"""

from fastapi import Depends, Request

from app.core.config import Config, get_config
from app.core.exceptions import ForbiddenError
from app.repositories.agent_repository import AgentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.services.agent_service import AgentService
from app.services.auth_service import AuthService
from app.services.conversation_service import ConversationService
from app.services.llm_service import LLMService
from app.services.plivo_service import PlivoService
from app.services.user_service import UserService

SESSION_COOKIE = "agent_session"


# ---------- services ----------

def get_agent_service(config: Config = Depends(get_config)) -> AgentService:
    return AgentService(AgentRepository(config.data_dir, config.agent_defaults_path))


def get_llm_service(
    config: Config = Depends(get_config), agent: AgentService = Depends(get_agent_service)
) -> LLMService:
    return LLMService(config, agent.demo_config())


def get_conversation_service(
    config: Config = Depends(get_config),
    agent: AgentService = Depends(get_agent_service),
    llm: LLMService = Depends(get_llm_service),
) -> ConversationService:
    return ConversationService(ConversationRepository(config.data_dir), agent, llm, config)


def get_plivo_service(config: Config = Depends(get_config)) -> PlivoService:
    return PlivoService(config)


def get_auth_service(config: Config = Depends(get_config)) -> AuthService:
    return AuthService(UserRepository(config.data_dir), SessionRepository(config.data_dir), config)


def get_user_service(config: Config = Depends(get_config)) -> UserService:
    return UserService(UserRepository(config.data_dir), SessionRepository(config.data_dir), config)


# ---------- authentication ----------

def get_session_token(request: Request) -> str | None:
    """API clients send `Authorization: Bearer <token>`; the web UI uses an HttpOnly cookie."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE)


def get_current_user(
    request: Request, auth: AuthService = Depends(get_auth_service)
) -> dict:
    return auth.authenticate(get_session_token(request))


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise ForbiddenError("Admin access required")
    return user
