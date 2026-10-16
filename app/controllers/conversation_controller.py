from fastapi import APIRouter, Depends, Query

from app.dependencies import get_conversation_service
from app.schemas import (
    ConversationDetail,
    ConversationStarted,
    ConversationSummary,
    MessageIn,
    Ok,
    ReplyOut,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationStarted, status_code=201)
def start_conversation(service: ConversationService = Depends(get_conversation_service)):
    conversation, greeting = service.start(channel="web")
    return ConversationStarted(id=conversation["id"], greeting=greeting)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    service: ConversationService = Depends(get_conversation_service),
):
    return service.list(limit)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int, service: ConversationService = Depends(get_conversation_service)):
    return service.get_with_messages(conversation_id)


@router.delete("/{conversation_id}", response_model=Ok)
def delete_conversation(conversation_id: int, service: ConversationService = Depends(get_conversation_service)):
    service.delete(conversation_id)
    return Ok()


@router.post("/{conversation_id}/messages", response_model=ReplyOut)
async def send_message(
    conversation_id: int,
    body: MessageIn,
    service: ConversationService = Depends(get_conversation_service),
):
    return ReplyOut(reply=await service.reply(conversation_id, body.text))


@router.post("/{conversation_id}/end", response_model=Ok)
def end_conversation(conversation_id: int, service: ConversationService = Depends(get_conversation_service)):
    service.end(conversation_id)
    return Ok()
