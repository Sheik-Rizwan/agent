from fastapi import APIRouter, Depends

from app.core.config import Config, get_config
from app.dependencies import get_current_user, get_llm_service
from app.schemas import Health, SystemStatus
from app.services.llm_service import LLMService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health():
    """Public liveness probe. Reveals nothing about the configuration."""
    return Health(status="ok")


@router.get("/status", response_model=SystemStatus, dependencies=[Depends(get_current_user)])
def system_status(config: Config = Depends(get_config), llm: LLMService = Depends(get_llm_service)):
    return SystemStatus(mode=llm.mode, model=config.gemini_model, telephony_configured=config.plivo_configured)
