"""Creating, listing, deleting users and changing passwords."""

import re
from datetime import datetime, timezone

from app.core.config import Config
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError, ValidationError
from app.core.security import hash_password, verify_password
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import public_user

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
ROLES = ("admin", "user")
PASSWORD_MAX_LENGTH = 128


class UserService:
    def __init__(self, users: UserRepository, sessions: SessionRepository, config: Config):
        self.users = users
        self.sessions = sessions
        self.config = config

    def validate_password(self, password: str, username: str) -> None:
        if len(password) < self.config.password_min_length:
            raise ValidationError(f"Password must be at least {self.config.password_min_length} characters")
        if len(password) > PASSWORD_MAX_LENGTH:
            raise ValidationError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")
        if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
            raise ValidationError("Password must contain at least one letter and one number")
        if username.lower() in password.lower():
            raise ValidationError("Password must not contain the username")

    def list(self) -> list[dict]:
        return [public_user(u) for u in self.users.list()]

    def has_users(self) -> bool:
        return self.users.count() > 0

    def create(self, username: str, password: str, role: str, created_by: str | None) -> dict:
        username = username.strip().lower()
        if not USERNAME_PATTERN.match(username):
            raise ValidationError(
                "Username must be 3-32 characters: lowercase letters, numbers, '.', '_' or '-', "
                "starting with a letter or number"
            )
        if role not in ROLES:
            raise ValidationError(f"Role must be one of: {', '.join(ROLES)}")
        self.validate_password(password, username)

        user = {
            "username": username,
            "role": role,
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": created_by,
            "last_login_at": None,
        }
        if not self.users.create(user):
            raise ConflictError(f"User '{username}' already exists")
        return public_user(user)

    def delete(self, username: str, acting_user: str) -> None:
        if username.lower() == acting_user.lower():
            raise ForbiddenError("You can't delete your own account")
        result = self.users.delete_unless_last_admin(username)
        if result == "not_found":
            raise NotFoundError(f"User '{username}' not found")
        if result == "last_admin":
            raise ForbiddenError("Can't delete the last admin")
        self.sessions.delete_for_user(username.lower())

    def set_password(self, username: str, new_password: str) -> None:
        """Admin reset: sets the password and logs the user out everywhere."""
        user = self.users.get(username)
        if user is None:
            raise NotFoundError(f"User '{username}' not found")
        self.validate_password(new_password, user["username"])
        self.users.update(user["username"], {"password_hash": hash_password(new_password)})
        self.sessions.delete_for_user(user["username"])

    def change_own_password(self, username: str, current: str, new: str, keep_session_hash: str) -> None:
        """Self-service: requires the current password; other sessions are logged out."""
        user = self.users.get(username)
        if user is None or not verify_password(current, user["password_hash"]):
            raise UnauthorizedError("Current password is incorrect")
        if current == new:
            raise ValidationError("New password must be different from the current one")
        self.validate_password(new, username)
        self.users.update(username, {"password_hash": hash_password(new)})
        self.sessions.delete_for_user(username, except_hash=keep_session_hash)
