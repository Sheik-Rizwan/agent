"""Application entry point: builds the FastAPI app, security middleware,
the versioned REST API and the web UI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.controllers import (  # noqa: E402
    agent_controller,
    auth_controller,
    call_controller,
    conversation_controller,
    health_controller,
    plivo_webhook_controller,
    user_controller,
)
from app.core.config import get_config  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402
from app.core.json_store import ensure_private_dir  # noqa: E402
from app.repositories.agent_repository import AgentRepository  # noqa: E402
from app.repositories.session_repository import SessionRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.services.user_service import UserService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

STATIC_DIR = Path(__file__).parent / "static"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
NO_CSP_PATHS = ("/docs", "/redoc")  # Swagger/ReDoc load scripts from a CDN

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def bootstrap() -> None:
    """Startup checks: data dir, agent defaults file, first admin account."""
    config = get_config()
    ensure_private_dir(config.data_dir)
    try:
        AgentRepository(config.data_dir, config.agent_defaults_path).load_defaults()
    except AppError as exc:
        logger.error("%s", exc.message)

    users = UserService(UserRepository(config.data_dir), SessionRepository(config.data_dir), config)
    if users.has_users():
        return
    if config.admin_username and config.admin_password:
        try:
            users.create(config.admin_username, config.admin_password, "admin", created_by="bootstrap")
            logger.info("Created admin user '%s' from ADMIN_USERNAME/ADMIN_PASSWORD", config.admin_username)
        except AppError as exc:
            logger.error("Could not create the admin from ADMIN_USERNAME/ADMIN_PASSWORD: %s", exc.message)
    else:
        logger.warning(
            "No users exist yet. Create an admin with:  python -m app.cli create-user <name> --role admin"
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    yield


_docs = get_config().enable_api_docs
app = FastAPI(
    title="Agent API",
    description=(
        "Voice AI receptionist powered by Gemini, with phone calls through Plivo.\n\n"
        "Log in with `POST /api/v1/auth/login`, then send `Authorization: Bearer <token>`."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF defence in depth (the session cookie is already SameSite=Strict):
    # a browser always sends Origin on cross-site POST/PUT/DELETE, so reject
    # any state-changing request whose Origin isn't this server.
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked"})

    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if not request.url.path.startswith(NO_CSP_PATHS):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message}, headers=exc.headers)


api = APIRouter(prefix="/api/v1")
api.include_router(health_controller.router)
api.include_router(auth_controller.router)
api.include_router(user_controller.router)
api.include_router(agent_controller.router)
api.include_router(conversation_controller.router)
api.include_router(call_controller.router)
api.include_router(plivo_webhook_controller.router)
app.include_router(api)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
