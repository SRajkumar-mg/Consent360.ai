"""R3-08: request/response models for the breach register.

Kept out of `schemas/schemas.py` for the same reason the models are kept out
of `entities.py` - this module is self-contained and imports nothing from
either. Field names mirror `app/models/breach.py`, whose docstring carries the
Rule 7 mapping for every narrative field.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class BreachCreateIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=256)
    source_app: str = Field(..., min_length=1, max_length=128)
    # Optional because a breach is often registered before anyone knows when
    # it happened; `detected_at` defaults to now, `aware_at` stays NULL until
    # triage confirms it is a personal data breach and starts the clocks.
    occurred_at: Optional[datetime] = None
    detected_at: Optional[datetime] = None
    aware_at: Optional[datetime] = None
    severity: str = "MEDIUM"
    nature: str = ""
    extent: str = ""
    location: str = ""
    likely_impact: str = ""
    likely_consequences: str = ""
    mitigation_measures: str = ""
    safety_measures: str = ""
    cause: str = ""
    cert_in_reportable: bool = True


class BreachUpdateIn(BaseModel):
    title: Optional[str] = None
    severity: Optional[str] = None
    occurred_at: Optional[datetime] = None
    nature: Optional[str] = None
    extent: Optional[str] = None
    location: Optional[str] = None
    likely_impact: Optional[str] = None
    likely_consequences: Optional[str] = None
    mitigation_measures: Optional[str] = None
    safety_measures: Optional[str] = None
    cause: Optional[str] = None
    findings_on_actor: Optional[str] = None
    remedial_measures: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    cert_in_reportable: Optional[bool] = None
    cert_in_not_reportable_reason: Optional[str] = None


class BreachAwareIn(BaseModel):
    aware_at: Optional[datetime] = None


class BreachStatusIn(BaseModel):
    status: str
    note: str = ""


class BreachAffectedIn(BaseModel):
    external_ids: list[str] = Field(..., min_length=1)
    data_involved: str = ""


class BreachAffectedOut(BaseModel):
    added: int
    already_present: int
    unresolved: list[str]
    affected_count: int


class BreachOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    breach_ref: str
    tenant_id: Optional[int]
    source_app: str
    title: str
    status: str
    severity: str
    occurred_at: Optional[datetime]
    detected_at: datetime
    aware_at: Optional[datetime]
    nature: str
    extent: str
    location: str
    likely_impact: str
    likely_consequences: str
    mitigation_measures: str
    safety_measures: str
    cause: str
    findings_on_actor: str
    remedial_measures: str
    contact_name: str
    contact_email: str
    contact_phone: str
    cert_in_reportable: bool
    cert_in_not_reportable_reason: str
    affected_count: int
    scope_finalised_at: Optional[datetime]
    closed_at: Optional[datetime]
    closure_note: str
    created_by: str
    created_at: datetime


class BreachDetailOut(BreachOut):
    clocks: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    outstanding_obligations: list[str]


class BreachNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    breach_id: int
    recipient_type: str
    stage: str
    customer_id: Optional[int]
    notification_id: Optional[int]
    channel: str
    language: str
    subject: str
    clock_start_at: Optional[datetime]
    deadline_at: Optional[datetime]
    target_at: Optional[datetime]
    deadline_basis: str
    content_hash: str
    status: str
    sent_at: Optional[datetime]
    delivered_at: Optional[datetime]
    filing_reference: str
    last_error: str
    generated_by: str
    created_at: datetime


class BreachNotificationContentOut(BreachNotificationOut):
    """The filing including its rendered content and structured payload -
    what a regulator or an auditor needs to check the hash against."""
    content: str
    payload: dict[str, Any]


class BreachNoticeIssueIn(BaseModel):
    language: Optional[str] = None


class BreachNoticeIssueOut(BaseModel):
    generated: int
    already_issued: int
    breach_notification_ids: list[int]


class BreachFilingRecordIn(BaseModel):
    filing_reference: str = Field(..., min_length=1, max_length=256)
    filed_at: Optional[datetime] = None
    delivered: bool = True


class BreachHashVerifyOut(BaseModel):
    breach_notification_id: int
    recorded_hash: str
    recomputed_hash: str
    matches: bool
    algorithm: str


class BreachExtensionRequestIn(BaseModel):
    requested_until: datetime
    reason: str = Field(..., min_length=1)
    written_request_ref: str = ""


class BreachExtensionDecisionIn(BaseModel):
    status: str = Field(..., pattern="^(GRANTED|REFUSED|WITHDRAWN)$")
    granted_until: Optional[datetime] = None
    board_reference: str = ""
    decision_note: str = ""


class BreachExtensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    breach_id: int
    requested_at: datetime
    requested_by: str
    requested_until: datetime
    reason: str
    written_request_ref: str
    status: str
    decided_at: Optional[datetime]
    decided_by: str
    granted_until: Optional[datetime]
    board_reference: str
    decision_note: str


class BreachReportPreviewOut(BaseModel):
    """A generated report before it is filed: the structured payload, the
    prose, the hash it would carry, and anything mandated that is missing."""
    breach_ref: str
    report_type: str
    provision: str
    payload: dict[str, Any]
    content: str
    content_hash: str
    missing_mandated_sections: list[str]
    deadline_at: Optional[datetime]
    deadline_basis: str


class BreachClocksOut(BaseModel):
    breach_ref: str
    aware_at: Optional[datetime]
    detected_at: datetime
    clocks: list[dict[str, Any]]
    timeline: list[dict[str, Any]]


class BreachMetricsOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    generated_at: str
    scope: str
    breaches: int
