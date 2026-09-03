"""Consent360 SDK data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CustomerContext:
    """Result of creating a customer context."""

    context_token: str
    context_id: int
    customer_id: str
    name: str
    expires_in_minutes: int
    source_app: str
    ui_url: str
    request_id: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CustomerContext:
        return cls(
            context_token=d["context_token"],
            context_id=d["context_id"],
            customer_id=d["customer_id"],
            name=d["name"],
            expires_in_minutes=d["expires_in_minutes"],
            source_app=d["source_app"],
            ui_url=d["ui_url"],
            request_id=d.get("request_id"),
        )


@dataclass
class ContextStatus:
    """Status of a context token."""

    status: str  # "VALID" | "CONSUMED" | "EXPIRED"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContextStatus:
        return cls(status=d["message"])


@dataclass
class PortalPurpose:
    """A purpose in the portal overview."""

    code: str
    name: str
    description: str
    legal_basis: str
    requires_consent: bool
    retention_period_days: int
    consent_text: str
    translations: dict[str, str] = field(default_factory=dict)
    status: str = "NOT_GRANTED"
    granted_count: int = 0
    total_count: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortalPurpose:
        return cls(
            code=d["code"],
            name=d["name"],
            description=d.get("description", ""),
            legal_basis=d.get("legal_basis", "CONSENT"),
            requires_consent=d.get("requires_consent", True),
            retention_period_days=d.get("retention_period_days", 365),
            consent_text=d.get("consent_text", ""),
            translations=d.get("translations", {}),
            status=d.get("status", "NOT_GRANTED"),
            granted_count=d.get("granted_count", 0),
            total_count=d.get("total_count", 0),
        )


@dataclass
class PortalOverview:
    """Customer's consent portal overview."""

    customer_id: int
    customer_external_id: str
    customer_name: str
    source_app: str
    purposes: list[PortalPurpose] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortalOverview:
        return cls(
            customer_id=d["customer"]["id"],
            customer_external_id=d["customer"]["external_id"],
            customer_name=d["customer"]["name"],
            source_app=d.get("source_app", ""),
            purposes=[PortalPurpose.from_dict(p) for p in d.get("purposes", [])],
        )


@dataclass
class PortalAction:
    """Result of a grant/withdraw action."""

    purpose_code: str
    action: str
    affected: int
    message: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PortalAction:
        return cls(
            purpose_code=d["purpose_code"],
            action=d["action"],
            affected=d["affected"],
            message=d["message"],
        )


@dataclass
class PurgeResult:
    """Result of purging a customer's consent data."""

    deleted: bool
    external_id: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PurgeResult:
        return cls(deleted=d["deleted"], external_id=d["external_id"])
