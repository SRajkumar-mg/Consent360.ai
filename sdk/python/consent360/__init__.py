"""Consent360 Python SDK — integration client for the Consent Management Platform."""

from consent360.client import Consent360
from consent360.exceptions import (
    Consent360Error,
    AuthError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from consent360.models import (
    CustomerContext,
    ContextStatus,
    PortalOverview,
    PortalPurpose,
    PortalAction,
    PurgeResult,
)

__version__ = "1.0.0"

__all__ = [
    "Consent360",
    "Consent360Error",
    "AuthError",
    "NotFoundError",
    "RateLimitError",
    "ValidationError",
    "CustomerContext",
    "ContextStatus",
    "PortalOverview",
    "PortalPurpose",
    "PortalAction",
    "PurgeResult",
]
