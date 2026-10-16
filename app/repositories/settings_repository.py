"""Data access for the key/value agent settings table."""

from app.core.database import Database


class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_all(self) -> dict[str, str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def upsert_many(self, values: dict[str, str]) -> None:
        with self.db.connect() as conn:
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(values.items()),
            )

    def clear(self) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM settings")
