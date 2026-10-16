"""Shared fixtures. Every test gets its own empty data directory and a clean
environment, so tests never touch your real data/ folder or .env values."""

import httpx
import pytest
from fastapi.testclient import TestClient

# Import the app first: it loads .env, and the autouse fixture below then
# removes those values so a real .env can never leak into a test.
from app.main import app
from app.services.auth_service import rate_limiter

ADMIN = {"username": "admin", "password": "Sup3rSecretPw"}
USER = {"username": "alice", "password": "Wonderland42x"}

ENV_VARS = (
    "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_TEMPERATURE", "GEMINI_MAX_OUTPUT_TOKENS",
    "GEMINI_TIMEOUT_SECONDS", "LLM_MAX_HISTORY_MESSAGES", "AGENT_TIMEZONE", "AGENT_DEFAULTS_PATH",
    "PUBLIC_BASE_URL", "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER", "PLIVO_VOICE",
    "PLIVO_LANGUAGE", "PLIVO_VALIDATE_SIGNATURE", "PLIVO_MAX_SILENT_TURNS", "SESSION_TTL_HOURS",
    "COOKIE_SECURE", "PASSWORD_MIN_LENGTH", "LOGIN_MAX_ATTEMPTS", "LOGIN_LOCKOUT_MINUTES",
    "ADMIN_USERNAME", "ADMIN_PASSWORD",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    rate_limiter._failures.clear()
    yield tmp_path / "data"
    rate_limiter._failures.clear()


@pytest.fixture
def data_dir(isolated_env):
    return isolated_env


@pytest.fixture
def anon(monkeypatch):
    """A client with an admin account bootstrapped but not logged in."""
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN["username"])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN["password"])
    with TestClient(app) as c:
        yield c


def login(client, creds) -> str:
    resp = client.post("/api/v1/auth/login", json=creds)
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture
def admin(anon):
    """Logged-in admin client (cookie session)."""
    login(anon, ADMIN)
    return anon


@pytest.fixture
def user_client(admin):
    """A second client logged in as a regular user. Shares the app with `admin`."""
    resp = admin.post("/api/v1/users", json={**USER, "role": "user"})
    assert resp.status_code == 201, resp.text
    with TestClient(app) as c:
        login(c, USER)
        yield c


def mock_http(monkeypatch, handler):
    """Route every outgoing httpx.AsyncClient request (Gemini, Plivo) to `handler`."""
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
