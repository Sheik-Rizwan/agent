"""Data access for conversations and their messages."""

from datetime import datetime, timezone

from app.core.database import Database

CONVERSATION_FIELDS = "id, channel, call_uuid, from_number, to_number, started_at, ended_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConversationRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        channel: str = "web",
        call_uuid: str | None = None,
        from_number: str | None = None,
        to_number: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (channel, call_uuid, from_number, to_number, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (channel, call_uuid, from_number, to_number, _now()),
            )
            return cur.lastrowid

    def get(self, conversation_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT {CONVERSATION_FIELDS} FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_call_uuid(self, call_uuid: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT {CONVERSATION_FIELDS} FROM conversations WHERE call_uuid = ? "
                "ORDER BY id DESC LIMIT 1",
                (call_uuid,),
            ).fetchone()
        return dict(row) if row else None

    def mark_ended(self, conversation_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE conversations SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (_now(), conversation_id),
            )

    def delete(self, conversation_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def list(self, limit: int = 50) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.channel, c.call_uuid, c.from_number, c.to_number,
                       c.started_at, c.ended_at,
                       COUNT(m.id) AS message_count,
                       (SELECT content FROM messages
                         WHERE conversation_id = c.id AND role = 'user'
                         ORDER BY id LIMIT 1) AS first_user_message
                  FROM conversations c
                  LEFT JOIN messages m ON m.conversation_id = c.id
                 GROUP BY c.id
                 ORDER BY c.id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, conversation_id: int, role: str, content: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, _now()),
            )

    def list_messages(self, conversation_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]
