"""Data access for conversations, stored as one JSON file per conversation.

Layout:
    <DATA_DIR>/conversations/<id>.json   the conversation and all its messages
    <DATA_DIR>/call_index.json           Plivo CallUUID -> conversation id

Conversation ids are random 32-character hex strings. Every id is checked
against that pattern before it is turned into a file path, so a request like
`/conversations/..%2F..%2Fusers` can never read or delete other files.
"""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.json_store import JsonFile, ensure_private_dir

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def is_valid_id(conversation_id: str) -> bool:
    return isinstance(conversation_id, str) and bool(ID_PATTERN.match(conversation_id))


class ConversationRepository:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "conversations"
        self.call_index = JsonFile(data_dir / "call_index.json", dict)

    def _file(self, conversation_id: str) -> JsonFile | None:
        if not is_valid_id(conversation_id):
            return None
        return JsonFile(self.dir / f"{conversation_id}.json", dict)

    def create(
        self,
        channel: str,
        created_by: str | None = None,
        call_uuid: str | None = None,
        from_number: str | None = None,
        to_number: str | None = None,
    ) -> dict:
        ensure_private_dir(self.dir)
        conversation = {
            "id": uuid.uuid4().hex,
            "channel": channel,
            "created_by": created_by,
            "call_uuid": call_uuid,
            "from_number": from_number,
            "to_number": to_number,
            "started_at": now_iso(),
            "ended_at": None,
            "messages": [],
        }
        self._file(conversation["id"]).write(conversation)
        if call_uuid:
            with self.call_index.update() as index:
                index[call_uuid] = conversation["id"]
        return conversation

    def get(self, conversation_id: str) -> dict | None:
        file = self._file(conversation_id)
        if file is None:
            return None
        data = file.read()
        return data or None

    def get_by_call_uuid(self, call_uuid: str) -> dict | None:
        conversation_id = self.call_index.read().get(call_uuid)
        return self.get(conversation_id) if conversation_id else None

    def add_message(self, conversation_id: str, role: str, content: str) -> bool:
        file = self._file(conversation_id)
        if file is None:
            return False
        with file.update(create=False) as data:
            if data is None:
                return False  # deleted meanwhile
            data["messages"].append({"role": role, "content": content, "created_at": now_iso()})
        return True

    def mark_ended(self, conversation_id: str) -> None:
        file = self._file(conversation_id)
        if file is None:
            return
        with file.update(create=False) as data:
            if data is not None and data.get("ended_at") is None:
                data["ended_at"] = now_iso()

    def delete(self, conversation_id: str) -> bool:
        file = self._file(conversation_id)
        if file is None:
            return False
        conversation = file.read()
        deleted = file.delete()
        call_uuid = conversation.get("call_uuid") if conversation else None
        if call_uuid:
            with self.call_index.update() as index:
                index.pop(call_uuid, None)
        return deleted

    def list(self, limit: int) -> list[dict]:
        if not self.dir.exists():
            return []
        conversations = []
        for path in self.dir.glob("*.json"):
            data = self.get(path.stem)
            if data:
                conversations.append(data)
        conversations.sort(key=lambda c: c["started_at"], reverse=True)
        return conversations[:limit]
