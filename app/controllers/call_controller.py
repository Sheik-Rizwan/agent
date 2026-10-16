"""REST endpoints for phone calls made through Plivo."""

from fastapi import APIRouter, Depends

from app.core.config import Config, get_config
from app.core.exceptions import NotConfiguredError
from app.dependencies import get_plivo_service
from app.schemas import CallIn, CallOut, TelephonyStatus
from app.services.plivo_service import PlivoService

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("/status", response_model=TelephonyStatus)
def telephony_status(config: Config = Depends(get_config)):
    return TelephonyStatus(
        provider="plivo",
        configured=config.plivo_configured,
        phone_number=config.plivo_phone_number or None,
        public_base_url=config.public_base_url or None,
    )


@router.post("", response_model=CallOut, status_code=201)
async def place_call(
    body: CallIn,
    config: Config = Depends(get_config),
    plivo: PlivoService = Depends(get_plivo_service),
):
    if not config.public_base_url:
        raise NotConfiguredError(
            "PUBLIC_BASE_URL is not set. Plivo needs a public HTTPS URL (e.g. an ngrok tunnel) "
            "to reach this server during the call."
        )
    webhooks = f"{config.public_base_url}/api/v1/webhooks/plivo"
    result = await plivo.place_call(body.to, f"{webhooks}/answer", f"{webhooks}/hangup")
    return CallOut(**result)
