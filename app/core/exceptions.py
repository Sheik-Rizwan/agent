"""Domain errors raised by services and translated to HTTP responses in main.py."""


class AppError(Exception):
    status_code = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    @property
    def headers(self) -> dict[str, str] | None:
        return None


class NotFoundError(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 422


class UnauthorizedError(AppError):
    status_code = 401


class ForbiddenError(AppError):
    status_code = 403


class ConflictError(AppError):
    status_code = 409


class TooManyRequestsError(AppError):
    status_code = 429

    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after_seconds)}


class UpstreamError(AppError):
    """An external API (Gemini, Plivo) failed."""

    status_code = 502


class NotConfiguredError(AppError):
    """A feature needs credentials or settings that are missing."""

    status_code = 503
