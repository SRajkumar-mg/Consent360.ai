"""R1-06: request/response models for the retention and erasure engine.

Kept out of `schemas/schemas.py` for the same reason `schemas/breach.py` and
`schemas/grievance.py` are - so this feature could be built without contending
for the single shared schema file.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.erasure import MIN_PRE_ERASURE_NOTICE_HOURS

RecordClass = Literal[
    "principal_personal_data", "directory_record", "consent_contexts", "notifications"
]
ErasureAction = Literal["ERASE", "ANONYMISE"]


# --------------------------------------------------------------------------- #
# Retention policies
# --------------------------------------------------------------------------- #
class RetentionPolicyIn(BaseModel):
    record_class: RecordClass
    #: A source_app (tenant code), or "*" for every tenant.
    scope: str = "*"
    #: Days after the principal's last interaction at which the data is erased
    #: because the specified purpose is no longer served. Null = event-driven
    #: only (a withdrawal or an approved request), no clock.
    retention_days: Optional[int] = Field(default=None, gt=0)
    #: The separate R.8(1)/Third Schedule clock. Null = this tenant is not in a
    #: Third Schedule class and no inactivity clock runs for it.
    inactivity_days: Optional[int] = Field(default=None, gt=0)
    #: R.8(2) states a minimum, so values below 48 are refused, not clamped.
    pre_erasure_notice_hours: int = Field(default=MIN_PRE_ERASURE_NOTICE_HOURS, ge=MIN_PRE_ERASURE_NOTICE_HOURS)
    action: ErasureAction = "ANONYMISE"
    legal_basis_for_retention: str = Field(min_length=1)
    is_active: bool = True
    notes: str = ""


class RetentionPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    policy_ref: str
    tenant_id: Optional[int]
    record_class: str
    scope: str
    retention_days: Optional[int]
    inactivity_days: Optional[int]
    pre_erasure_notice_hours: int
    action: str
    legal_basis_for_retention: str
    is_active: bool
    notes: str
    updated_at: datetime
    updated_by: str
    #: The R1-10 statutory floor in force for this class, where one exists.
    #: Present so a reader can see for themselves that the ceiling above it is
    #: lawful, rather than taking the API's word that it was checked.
    floor_days: Optional[int] = None
    hard_delete_permitted: bool = False


# --------------------------------------------------------------------------- #
# Legal holds
# --------------------------------------------------------------------------- #
class LegalHoldIn(BaseModel):
    legal_basis: str = Field(min_length=1)
    reason: str = ""
    #: The principal's external id. Omit for a tenant-wide preservation hold.
    customer_external_id: Optional[str] = None
    record_class: Optional[RecordClass] = None
    tenant_code: Optional[str] = None
    expires_at: Optional[datetime] = None


class LegalHoldReleaseIn(BaseModel):
    release_reason: str = Field(min_length=1)


class LegalHoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hold_ref: str
    tenant_id: Optional[int]
    customer_id: Optional[int]
    record_class: Optional[str]
    legal_basis: str
    reason: str
    placed_by: str
    placed_at: datetime
    expires_at: Optional[datetime]
    released_at: Optional[datetime]
    released_by: Optional[str]
    release_reason: str
    is_active: bool


# --------------------------------------------------------------------------- #
# Erasure jobs
# --------------------------------------------------------------------------- #
class ErasureRequestIn(BaseModel):
    """Raise an erasure for one principal.

    `trigger` is restricted to the two a human may raise directly: an approved
    s.12(3) rights request, or a manual erasure with a recorded reason.
    WITHDRAWAL, RETENTION and INACTIVITY jobs are raised by the withdrawal
    path and the scans, never by an API caller asserting them.
    """

    customer_external_id: str
    trigger: Literal["RIGHTS_REQUEST", "MANUAL"] = "RIGHTS_REQUEST"
    #: For RIGHTS_REQUEST: the grievance reference_no of the s.12(3) request.
    request_ref: Optional[str] = None
    reason: str = ""
    #: Why this erasure is lawful and who decided. Required - erasure is
    #: irreversible, so "because the API was called" is not a basis.
    authorisation_basis: str = Field(min_length=1)


class ErasureAuthoriseIn(BaseModel):
    basis: str = Field(min_length=1)


class ErasureCancelIn(BaseModel):
    reason: str = Field(min_length=1)


class ErasureExecuteIn(BaseModel):
    """Deliberately empty of anything that could weaken a guard.

    There is no `force`, no `skip_notice` and no `ignore_hold` parameter, and
    that is a design decision rather than an omission: the R.8(2) notice
    period and a legal hold are the two things standing between a mistake and
    an irreversible one, and an endpoint that can be told to ignore them will
    eventually be told to.
    """


class ErasureJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_ref: str
    tenant_id: Optional[int]
    customer_id: int
    purpose_id: Optional[int]
    policy_id: Optional[int]
    trigger: str
    trigger_ref: str
    action: str
    status: str
    authorised_by: Optional[str]
    authorised_at: Optional[datetime]
    authorisation_basis: str
    notice_required: bool
    notice_hours: int
    notice_sent_at: Optional[datetime]
    notification_ids: list
    execute_after: Optional[datetime]
    executed_at: Optional[datetime]
    executed_by: Optional[str]
    records_erased: dict
    records_retained: dict
    processor_alert_ids: list
    anonymised_ref: Optional[str]
    evidence_hash: Optional[str]
    blocked_reason: str
    hold_id: Optional[int]
    cancelled_at: Optional[datetime]
    cancelled_by: Optional[str]
    cancel_reason: str
    error: Optional[str]
    source_app: str
    created_by: str
    reason: str
    created_at: datetime


class ErasureScanOut(BaseModel):
    policies: int
    candidates: int
    jobs_proposed: int
    inactivity_clock_breaches: Optional[int] = None


class ErasureMetricsOut(BaseModel):
    generated_at: datetime
    erasure_backlog: int
    backlog_by_status: dict
    jobs_executed: int
    median_tat_hours: Optional[float]
    executed_with_compliant_notice: int
    # None, not a fabricated 100.0, when jobs_executed is 0 - see
    # app/services/erasure.py::erasure_metrics.
    notice_compliance_pct: Optional[float] = None
    inactivity_clock_breaches: int
    legal_holds_active: int
