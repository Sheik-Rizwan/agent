"""SQLite persistence for agent settings and conversation transcripts."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_SETTINGS = {
    "agent_name": "Ava",
    "greeting": "Hi, this is Ava from Sunrise Dental. How can I help you today?",
    "system_prompt": (
        "You are Ava, a warm and efficient voice receptionist for Sunrise Dental, "
        "a family dental clinic.\n\n"
        "Clinic facts:\n"
        "- Hours: Monday to Friday 9am to 6pm, Saturday 10am to 2pm, closed Sunday.\n"
        "- Services: check-ups and cleaning, fillings, root canals, whitening, braces consultations.\n"
        "- A check-up and cleaning costs 1,500 rupees. Other prices are given after an exam.\n"
        "- Address: 12 MG Road, Bengaluru.\n\n"
        "How to talk:\n"
        "- You are speaking out loud, so keep replies to one to three short sentences.\n"
        "- Never use markdown, bullet points, emojis, or lists.\n"
        "- Ask one question at a time.\n"
        "- To book an appointment, collect the caller's name, preferred day and time, "
        "and reason for the visit, then read the details back to confirm.\n"
        "- If you don't know something, say so and offer to have the clinic call back.\n"
        "- Never give medical diagnoses. For severe pain, bleeding, or swelling, "
        "advise the caller to visit urgently or call emergency services."
    ),
}


def _db_path() -> str:
    return os.environ.get("DATABASE_PATH", "data/agent.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect():
    path = _db_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def get_settings() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    return {key: stored.get(key, default) for key, default in DEFAULT_SETTINGS.items()}


def save_settings(values: dict) -> dict:
    with _connect() as conn:
        for key, value in values.items():
            if key in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
    return get_settings()


def reset_settings() -> dict:
    with _connect() as conn:
        conn.execute("DELETE FROM settings")
    return get_settings()


def create_conversation() -> int:
    with _connect() as conn:
        cur = conn.execute("INSERT INTO conversations (started_at) VALUES (?)", (_now(),))
        return cur.lastrowid


def conversation_exists(conversation_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return row is not None


def end_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (_now(), conversation_id),
        )


def add_message(conversation_id: int, role: str, content: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, _now()),
        )


def get_messages(conversation_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_conversations(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.started_at, c.ended_at,
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


def delete_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
