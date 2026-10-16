"""Data access for login sessions, stored in <DATA_DIR>/sessions.json.

Keys are SHA-256 hashes of the session tokens; the raw tokens only ever exist
in the user's cookie (or Authorization header).
"""

from datetime import datetime, timezone
from pathlib import Path

from app.core.json_store import JsonFile


def _default() -> dict:
    return {"sessions": {}}


class SessionRepository:
    def __init__(self, data_dir: Path):
        self.file = JsonFile(data_dir / "sessions.json", _default)

    def create(self, token_hash: str, session: dict) -> None:
        with self.file.update() as data:
            sessions = data.setdefault("sessions", {})
            now = datetime.now(timezone.utc)
            # Housekeeping: drop expired sessions whenever a new one is created.
            for key in [k for k, s in sessions.items() if datetime.fromisoformat(s["expires_at"]) <= now]:
                del sessions[key]
            sessions[token_hash] = session

    def get(self, token_hash: str) -> dict | None:
        return self.file.read().get("sessions", {}).get(token_hash)

    def delete(self, token_hash: str) -> None:
        with self.file.update() as data:
            data.setdefault("sessions", {}).pop(token_hash, None)

    def delete_for_user(self, username: str, except_hash: str | None = None) -> int:
        with self.file.update() as data:
            sessions = data.setdefault("sessions", {})
            doomed = [k for k, s in sessions.items() if s["username"] == username and k != except_hash]
            for key in doomed:
                del sessions[key]
        return len(doomed)
