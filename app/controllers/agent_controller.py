from fastapi import APIRouter, Depends

from app.dependencies import get_agent_service
from app.schemas import AgentConfig
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("", response_model=AgentConfig)
def get_agent(service: AgentService = Depends(get_agent_service)):
    return service.get()


@router.put("", response_model=AgentConfig)
def update_agent(body: AgentConfig, service: AgentService = Depends(get_agent_service)):
    return service.update(body.model_dump())


@router.post("/reset", response_model=AgentConfig)
def reset_agent(service: AgentService = Depends(get_agent_service)):
    return service.reset()
