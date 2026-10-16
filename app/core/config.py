"""Application configuration, read from environment variables (and .env).

Every tunable value lives here so nothing is hardcoded in the services.
Invalid numbers fall back to the default with a warning instead of crashing.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r is not a boolean; using %s", name, raw, default)
    return default


def _env_num(name: str, default, cast, minimum=None, maximum=None):
    raw = _env(name)
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        logger.warning("%s=%r is not a valid number; using %s", name, raw, default)
        return default
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        logger.warning("%s=%s is out of range [%s, %s]; using %s", name, value, minimum, maximum, default)
        return default
    return value


def _path(name: str, default: str) -> Path:
    path = Path(_env(name, default))
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Config:
    # Storage
    data_dir: Path
    agent_defaults_path: Path

    # Gemini
    gemini_api_key: str
    gemini_model: str
    gemini_temperature: float
    gemini_max_output_tokens: int
    gemini_timeout_seconds: float
    llm_max_history_messages: int
    agent_timezone: str

    # Plivo
    public_base_url: str
    plivo_auth_id: str
    plivo_auth_token: str
    plivo_phone_number: str
    plivo_voice: str
    plivo_language: str
    plivo_validate_signature: bool
    plivo_max_silent_turns: int

    # Security
    session_ttl_hours: float
    cookie_secure: bool
    password_min_length: int
    login_max_attempts: int
    login_lockout_minutes: float
    admin_username: str
    admin_password: str
    enable_api_docs: bool

    @property
    def plivo_configured(self) -> bool:
        return bool(self.plivo_auth_id and self.plivo_auth_token and self.plivo_phone_number)


def get_config() -> Config:
    # Read on every call so changes to the environment (and tests) take effect immediately.
    return Config(
        data_dir=_path("DATA_DIR", "data"),
        agent_defaults_path=_path("AGENT_DEFAULTS_PATH", "config/agent.default.json"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_temperature=_env_num("GEMINI_TEMPERATURE", 0.6, float, 0.0, 2.0),
        gemini_max_output_tokens=_env_num("GEMINI_MAX_OUTPUT_TOKENS", 1024, int, 16, 65536),
        gemini_timeout_seconds=_env_num("GEMINI_TIMEOUT_SECONDS", 30.0, float, 1.0, 300.0),
        llm_max_history_messages=_env_num("LLM_MAX_HISTORY_MESSAGES", 40, int, 2, 1000),
        agent_timezone=_env("AGENT_TIMEZONE", "Asia/Kolkata"),
        public_base_url=_env("PUBLIC_BASE_URL").rstrip("/"),
        plivo_auth_id=_env("PLIVO_AUTH_ID"),
        plivo_auth_token=_env("PLIVO_AUTH_TOKEN"),
        plivo_phone_number=_env("PLIVO_PHONE_NUMBER"),
        plivo_voice=_env("PLIVO_VOICE", "WOMAN"),
        plivo_language=_env("PLIVO_LANGUAGE", "en-US"),
        plivo_validate_signature=_env_bool("PLIVO_VALIDATE_SIGNATURE", True),
        plivo_max_silent_turns=_env_num("PLIVO_MAX_SILENT_TURNS", 2, int, 0, 10),
        session_ttl_hours=_env_num("SESSION_TTL_HOURS", 12.0, float, 0.01, 24 * 30),
        cookie_secure=_env_bool("COOKIE_SECURE", False),
        password_min_length=_env_num("PASSWORD_MIN_LENGTH", 10, int, 8, 128),
        login_max_attempts=_env_num("LOGIN_MAX_ATTEMPTS", 5, int, 1, 100),
        login_lockout_minutes=_env_num("LOGIN_LOCKOUT_MINUTES", 15.0, float, 0.1, 24 * 60),
        admin_username=_env("ADMIN_USERNAME"),
        admin_password=os.environ.get("ADMIN_PASSWORD", ""),
        enable_api_docs=_env_bool("ENABLE_API_DOCS", True),
    )
