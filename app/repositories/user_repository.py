"""Data access for user accounts, stored in <DATA_DIR>/users.json.

Usernames are stored lowercase, so "Admin" and "admin" are the same account.
"""

from pathlib import Path

from app.core.json_store import JsonFile


def _default() -> dict:
    return {"users": {}}


class UserRepository:
    def __init__(self, data_dir: Path):
        self.file = JsonFile(data_dir / "users.json", _default)

    def _users(self, data: dict) -> dict:
        return data.setdefault("users", {})

    def get(self, username: str) -> dict | None:
        return self._users(self.file.read()).get(username.lower())

    def list(self) -> list[dict]:
        users = list(self._users(self.file.read()).values())
        return sorted(users, key=lambda u: u["created_at"])

    def count(self) -> int:
        return len(self._users(self.file.read()))

    def create(self, user: dict) -> bool:
        """Returns False if the username is taken (checked under the file lock)."""
        with self.file.update() as data:
            users = self._users(data)
            if user["username"] in users:
                return False
            users[user["username"]] = user
        return True

    def update(self, username: str, changes: dict) -> dict | None:
        with self.file.update() as data:
            user = self._users(data).get(username.lower())
            if user is None:
                return None
            user.update(changes)
            return dict(user)

    def delete_unless_last_admin(self, username: str) -> str:
        """Atomically delete a user. Returns "deleted", "not_found" or "last_admin"."""
        with self.file.update() as data:
            users = self._users(data)
            user = users.get(username.lower())
            if user is None:
                return "not_found"
            admins = [u for u in users.values() if u["role"] == "admin"]
            if user["role"] == "admin" and len(admins) <= 1:
                return "last_admin"
            del users[username.lower()]
        return "deleted"
