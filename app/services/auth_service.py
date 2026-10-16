"""Login, logout and session validation.

Security measures:
- Passwords are checked with scrypt (see core/security.py). Unknown usernames
  are checked against a dummy hash so timing doesn't reveal valid usernames.
- Failed logins are rate limited per (username, client IP). After
  LOGIN_MAX_ATTEMPTS failures inside LOGIN_LOCKOUT_MINUTES, further attempts
  get HTTP 429 until the window passes.
- Sessions are random 256-bit tokens; only their SHA-256 hash is stored, and
  they expire after SESSION_TTL_HOURS.
"""

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from app.core.config import Config
from app.core.exceptions import TooManyRequestsError, UnauthorizedError
from app.core.security import DUMMY_HASH, hash_token, new_session_token, verify_password
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository


class LoginRateLimiter:
    """In-memory sliding window of failed attempts. Shared across requests."""

    def __init__(self):
        self._failures: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key, window: float, now: float) -> deque:
        failures = self._failures[key]
        while failures and failures[0] <= now - window:
            failures.popleft()
        return failures

    def check(self, key: tuple[str, str], max_attempts: int, window: float) -> None:
        now = time.monotonic()
        with self._lock:
            failures = self._prune(key, window, now)
            if not failures:
                self._failures.pop(key, None)  # keep memory bounded
            elif len(failures) >= max_attempts:
                retry_after = max(1, int(failures[0] + window - now) + 1)
                raise TooManyRequestsError(
                    "Too many failed login attempts. Try again later.", retry_after
                )

    def record_failure(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures[key].append(time.monotonic())

    def reset(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.pop(key, None)


rate_limiter = LoginRateLimiter()


def public_user(user: dict) -> dict:
    """A user record without secrets, safe to return from the API."""
    return {k: v for k, v in user.items() if k != "password_hash"}


class AuthService:
    def __init__(self, users: UserRepository, sessions: SessionRepository, config: Config):
        self.users = users
        self.sessions = sessions
        self.config = config

    def login(self, username: str, password: str, client_ip: str) -> tuple[str, dict]:
        """Returns (session token, user). Raises 401 on bad credentials, 429 when locked out."""
        key = (username.strip().lower(), client_ip)
        window = self.config.login_lockout_minutes * 60
        rate_limiter.check(key, self.config.login_max_attempts, window)

        user = self.users.get(username.strip())
        password_ok = verify_password(password, user["password_hash"] if user else DUMMY_HASH)
        if not (user and password_ok):
            rate_limiter.record_failure(key)
            raise UnauthorizedError("Invalid username or password")

        rate_limiter.reset(key)
        token = new_session_token()
        now = datetime.now(timezone.utc)
        self.sessions.create(
            hash_token(token),
            {
                "username": user["username"],
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=self.config.session_ttl_hours)).isoformat(),
            },
        )
        updated = self.users.update(user["username"], {"last_login_at": now.isoformat()}) or user
        return token, public_user(updated)

    def authenticate(self, token: str | None) -> dict:
        """Returns the logged-in user for a session token, or raises 401."""
        if not token:
            raise UnauthorizedError("Not logged in")
        token_hash = hash_token(token)
        session = self.sessions.get(token_hash)
        if session is None:
            raise UnauthorizedError("Session is invalid or has been logged out")
        if datetime.fromisoformat(session["expires_at"]) <= datetime.now(timezone.utc):
            self.sessions.delete(token_hash)
            raise UnauthorizedError("Session has expired. Please log in again.")
        user = self.users.get(session["username"])
        if user is None:
            self.sessions.delete(token_hash)
            raise UnauthorizedError("This account no longer exists")
        return {**public_user(user), "session_hash": token_hash}

    def logout(self, token: str | None) -> None:
        if token:
            self.sessions.delete(hash_token(token))
