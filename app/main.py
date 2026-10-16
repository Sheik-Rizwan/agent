"""FastAPI app: REST API for the voice agent plus the static web UI."""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app import llm, store  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"
MAX_HISTORY_MESSAGES = 40


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="Agent", lifespan=lifespan)


class SettingsIn(BaseModel):
    agent_name: str = Field(min_length=1, max_length=60)
    greeting: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=20000)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _require_conversation(conversation_id: int) -> None:
    if not store.conversation_exists(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": llm.mode(), "model": llm.model_name()}


@app.get("/api/settings")
def read_settings():
    return store.get_settings()


@app.put("/api/settings")
def update_settings(body: SettingsIn):
    return store.save_settings(body.model_dump())


@app.post("/api/settings/reset")
def reset_settings():
    return store.reset_settings()


@app.post("/api/conversations", status_code=201)
def start_conversation():
    conversation_id = store.create_conversation()
    greeting = store.get_settings()["greeting"]
    store.add_message(conversation_id, "assistant", greeting)
    return {"id": conversation_id, "greeting": greeting}


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: int, body: MessageIn):
    _require_conversation(conversation_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message is empty")

    store.add_message(conversation_id, "user", text)
    history = store.get_messages(conversation_id)[-MAX_HISTORY_MESSAGES:]
    try:
        reply = await llm.generate_reply(store.get_settings()["system_prompt"], history)
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    store.add_message(conversation_id, "assistant", reply)
    return {"reply": reply}


@app.post("/api/conversations/{conversation_id}/end")
def finish_conversation(conversation_id: int):
    _require_conversation(conversation_id)
    store.end_conversation(conversation_id)
    return {"ok": True}


@app.get("/api/conversations")
def conversations():
    return store.list_conversations()


@app.get("/api/conversations/{conversation_id}")
def conversation_detail(conversation_id: int):
    _require_conversation(conversation_id)
    return {"id": conversation_id, "messages": store.get_messages(conversation_id)}


@app.delete("/api/conversations/{conversation_id}")
def remove_conversation(conversation_id: int):
    _require_conversation(conversation_id)
    store.delete_conversation(conversation_id)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
