from fastapi import APIRouter, Depends

from app.dependencies import get_agent_service, get_current_user, require_admin
from app.schemas import AgentConfig
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("", response_model=AgentConfig, dependencies=[Depends(get_current_user)])
def get_agent(service: AgentService = Depends(get_agent_service)):
    return service.get()


@router.put("", response_model=AgentConfig, dependencies=[Depends(require_admin)])
def update_agent(body: AgentConfig, service: AgentService = Depends(get_agent_service)):
    """Update any subset of the agent fields. Field names come from config/agent.default.json."""
    return service.update(body)


@router.post("/reset", response_model=AgentConfig, dependencies=[Depends(require_admin)])
def reset_agent(service: AgentService = Depends(get_agent_service)):
    return service.reset()
