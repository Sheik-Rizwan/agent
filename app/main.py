"""Application entry point: builds the FastAPI app, mounts the REST API and the web UI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.controllers import (  # noqa: E402
    agent_controller,
    call_controller,
    conversation_controller,
    health_controller,
    plivo_webhook_controller,
)
from app.core.database import get_database  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_database().init_schema()
    yield


app = FastAPI(
    title="Agent API",
    description="Voice AI receptionist powered by Gemini, with phone calls through Plivo.",
    version="1.1.0",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


api = APIRouter(prefix="/api/v1")
api.include_router(health_controller.router)
api.include_router(agent_controller.router)
api.include_router(conversation_controller.router)
api.include_router(call_controller.router)
api.include_router(plivo_webhook_controller.router)
app.include_router(api)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
