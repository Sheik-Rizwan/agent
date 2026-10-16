from fastapi import APIRouter, Depends

from app.core.config import Config, get_config
from app.dependencies import get_llm_service
from app.schemas import Health
from app.services.llm_service import LLMService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health(config: Config = Depends(get_config), llm: LLMService = Depends(get_llm_service)):
    return Health(
        status="ok",
        mode=llm.mode,
        model=config.gemini_model,
        telephony_configured=config.plivo_configured,
    )
