"""Consent360 SDK exceptions."""


class Consent360Error(Exception):
    """Base exception for all Consent360 SDK errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class AuthError(Consent360Error):
    """Raised when authentication fails (401)."""


class NotFoundError(Consent360Error):
    """Raised when a resource is not found (404)."""


class RateLimitError(Consent360Error):
    """Raised when rate limit is exceeded (429)."""


class ValidationError(Consent360Error):
    """Raised when request validation fails (422)."""
