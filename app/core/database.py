"""SQLite connection handling and schema setup."""

import os
import sqlite3
from contextlib import contextmanager

from app.core.config import get_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT 'web',
    call_uuid TEXT,
    from_number TEXT,
    to_number TEXT,
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

# Columns added after the first release; created on older databases at startup.
CONVERSATION_COLUMNS = {
    "channel": "TEXT NOT NULL DEFAULT 'web'",
    "call_uuid": "TEXT",
    "from_number": "TEXT",
    "to_number": "TEXT",
}


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
            for column, definition in CONVERSATION_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE conversations ADD COLUMN {column} {definition}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_call_uuid ON conversations(call_uuid)"
            )


def get_database() -> Database:
    return Database(get_config().database_path)
