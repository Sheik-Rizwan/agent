"""Application configuration, read from environment variables (and .env)."""

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    agent_timezone: str
    database_path: str
    public_base_url: str
    plivo_auth_id: str
    plivo_auth_token: str
    plivo_phone_number: str
    plivo_voice: str
    plivo_language: str
    plivo_validate_signature: bool

    @property
    def plivo_configured(self) -> bool:
        return bool(self.plivo_auth_id and self.plivo_auth_token and self.plivo_phone_number)


def get_config() -> Config:
    # Read on every call so changes to the environment (and tests) take effect immediately.
    return Config(
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
        agent_timezone=_env("AGENT_TIMEZONE", "Asia/Kolkata"),
        database_path=_env("DATABASE_PATH", "data/agent.db"),
        public_base_url=_env("PUBLIC_BASE_URL").rstrip("/"),
        plivo_auth_id=_env("PLIVO_AUTH_ID"),
        plivo_auth_token=_env("PLIVO_AUTH_TOKEN"),
        plivo_phone_number=_env("PLIVO_PHONE_NUMBER"),
        plivo_voice=_env("PLIVO_VOICE", "WOMAN"),
        plivo_language=_env("PLIVO_LANGUAGE", "en-US"),
        plivo_validate_signature=_env("PLIVO_VALIDATE_SIGNATURE", "true").lower() not in ("0", "false", "no"),
    )
