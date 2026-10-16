"""Webhooks Plivo calls during a phone call. Each one returns Plivo XML.

Flow: answer -> greet and listen -> speech -> reply and listen -> ... -> hangup.
Plivo transcribes the caller and sends the text in the `Speech` parameter.

These endpoints are not behind login (Plivo can't log in); instead every request
must carry a valid Plivo signature (see verify_plivo_signature).

All spoken phone messages come from the agent configuration (phone_* fields),
and the silence limit from PLIVO_MAX_SILENT_TURNS.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import Config, get_config
from app.core.exceptions import ForbiddenError, NotConfiguredError, UpstreamError
from app.dependencies import get_agent_service, get_conversation_service, get_plivo_service
from app.services.agent_service import AgentService
from app.services.conversation_service import ConversationService
from app.services.plivo_service import PlivoService

logger = logging.getLogger(__name__)


def _public_url(request: Request, config: Config) -> str:
    """The URL Plivo called. Behind a tunnel the server sees a different host,
    so PUBLIC_BASE_URL is used when set."""
    if config.public_base_url:
        return f"{config.public_base_url}{request.url.path}"
    return str(request.url)


async def verify_plivo_signature(
    request: Request,
    config: Config = Depends(get_config),
    plivo: PlivoService = Depends(get_plivo_service),
) -> None:
    if not config.plivo_validate_signature:
        return
    if not config.plivo_auth_token:
        raise NotConfiguredError("Plivo is not configured")
    signature = request.headers.get("X-Plivo-Signature-V2", "")
    nonce = request.headers.get("X-Plivo-Signature-V2-Nonce", "")
    if not plivo.is_valid_signature(_public_url(request, config), nonce, signature):
        logger.warning("Rejected Plivo webhook with invalid signature: %s", request.url.path)
        raise ForbiddenError("Invalid Plivo signature")


router = APIRouter(
    prefix="/webhooks/plivo",
    tags=["plivo webhooks"],
    dependencies=[Depends(verify_plivo_signature)],
)


async def _params(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        params.update({k: str(v) for k, v in form.items()})
    return params


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def _speech_url(request: Request, config: Config, conversation_id: str, silent_turns: int) -> str:
    base = config.public_base_url or str(request.base_url).rstrip("/")
    return f"{base}/api/v1/webhooks/plivo/speech?conversation_id={conversation_id}&silent={silent_turns}"


@router.api_route("/answer", methods=["GET", "POST"])
async def answer(
    request: Request,
    config: Config = Depends(get_config),
    agent: AgentService = Depends(get_agent_service),
    conversations: ConversationService = Depends(get_conversation_service),
    plivo: PlivoService = Depends(get_plivo_service),
):
    params = await _params(request)
    call_uuid = params.get("CallUUID") or None
    existing = conversations.get_by_call_uuid(call_uuid) if call_uuid else None
    if existing:
        # Plivo retried the answer webhook; continue the same conversation.
        conversation = existing
        greeting = agent.get()["greeting"]
    else:
        conversation, greeting = conversations.start(
            channel="phone",
            call_uuid=call_uuid,
            from_number=params.get("From"),
            to_number=params.get("To"),
        )
    return _xml(plivo.listen_xml(greeting, _speech_url(request, config, conversation["id"], 1)))


@router.post("/speech")
async def speech(
    request: Request,
    conversation_id: str,
    silent: int = 1,
    config: Config = Depends(get_config),
    agent: AgentService = Depends(get_agent_service),
    conversations: ConversationService = Depends(get_conversation_service),
    plivo: PlivoService = Depends(get_plivo_service),
):
    """`silent` = how many silent turns in a row this request represents if no speech arrived."""
    messages = agent.get()
    if not conversations.exists(conversation_id):
        return _xml(plivo.hangup_xml(messages["phone_unknown_call"]))

    params = await _params(request)
    text = (params.get("Speech") or "").strip()

    if not text:
        if silent > config.plivo_max_silent_turns:
            conversations.end(conversation_id)
            return _xml(plivo.hangup_xml(messages["phone_goodbye_silent"]))
        next_url = _speech_url(request, config, conversation_id, silent + 1)
        return _xml(plivo.listen_xml(messages["phone_not_heard"], next_url))

    try:
        reply = await conversations.reply(conversation_id, text)
    except UpstreamError as exc:
        logger.error("LLM failed during call %s: %s", conversation_id, exc.message)
        reply = messages["phone_technical_issue"]
    return _xml(plivo.listen_xml(reply, _speech_url(request, config, conversation_id, 1)))


@router.post("/hangup")
async def hangup(
    request: Request,
    conversations: ConversationService = Depends(get_conversation_service),
):
    params = await _params(request)
    call_uuid = params.get("CallUUID")
    if call_uuid:
        conversations.end_by_call_uuid(call_uuid)
    return Response(status_code=204)
