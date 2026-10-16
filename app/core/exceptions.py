"""Domain errors raised by services and translated to HTTP responses in main.py."""


class AppError(Exception):
    status_code = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 422


class ForbiddenError(AppError):
    status_code = 403


class UpstreamError(AppError):
    """An external API (Gemini, Plivo) failed."""

    status_code = 502


class NotConfiguredError(AppError):
    """A feature needs credentials or settings that are missing."""

    status_code = 503
