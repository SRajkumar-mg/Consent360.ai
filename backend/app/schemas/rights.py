"""R2-05: request/response models for the data-principal rights module.

Kept in its own module (not `schemas/schemas.py`) for the same reason the ORM
models are - see `app/models/rights.py`'s closing note.

Four conventions worth knowing before adding a field:

* A request is addressed by its `reference_no` everywhere in the API, never by
  its primary key, and a nomination by its `nomination_ref`. Those are the
  handles the principal already holds; exposing the surrogate ids in URLs
  would hand out a sequential enumeration oracle over a register of who has
  asked for their data - which is exactly what the CSPRNG generation exists
  to avoid.
* The narrative fields (`request_detail`, `resolution_summary`,
  `rejection_basis`, and both correction payloads) are encrypted at rest.
  They round-trip through these models in the clear because the ORM decrypts
  on read - which is why every route returning one is permission- or
  verified-context-gated.
* **The principal is never taken from the body.** There is no
  `customer_external_id` on any `*SelfIn` model: a self-service submission is
  attributed to whoever the context token says it is. The staff models do
  carry an external id, because a handler logging a phone call has to say
  whose call it was - and that request then starts UNVERIFIED and cannot be
  fulfilled until an OTP the handler never sees has been confirmed.
* Nothing here accepts a `status`, an `identity_verified` or a
  `verification_method` from a caller. Those are outcomes of service calls,
  not inputs; a schema that accepted them would let a request be created
  pre-verified.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.rights import (
    CORRECTABLE_CUSTOMER_FIELDS,  # noqa: F401  (re-exported: see CorrectionApplyIn)
    NOMINATION_ACTIVATION_GROUNDS,
    RIGHTS_REJECTION_REASONS,
    RIGHTS_REQUEST_CHANNELS,
    RIGHTS_REQUEST_TYPES,
)

_TYPE_PATTERN = "^(" + "|".join(RIGHTS_REQUEST_TYPES) + ")$"
_CHANNEL_PATTERN = "^(" + "|".join(RIGHTS_REQUEST_CHANNELS) + ")$"
_REJECTION_PATTERN = "^(" + "|".join(RIGHTS_REJECTION_REASONS) + ")$"
_GROUND_PATTERN = "^(" + "|".join(NOMINATION_ACTIVATION_GROUNDS) + ")$"

# Generous prose, and still a hard bound on what one submission can push into
# an encrypted column. Same figure the grievance module uses.
_MAX_DETAIL = 20_000

# CORRECTABLE_CUSTOMER_FIELDS is defined in app/models/rights.py, with the
# other vocabularies, and re-exported here so `CorrectionApplyIn`'s docstring
# and the service that enforces it name the same tuple rather than two copies
# that can drift.


# --------------------------------------------------------------------------- #
#  Inputs - the data principal's own surface
# --------------------------------------------------------------------------- #

class RightsRequestSelfIn(BaseModel):
    """The principal's own submission, via the portal (X-Context-Token).

    The requester is taken from the context token, never from the body, and
    the context must already be verified - see
    routes/rights.py::_verified_principal_context.
    """

    request_type: str = Field(pattern=_TYPE_PATTERN)
    request_detail: str = Field(default="", max_length=_MAX_DETAIL)
    #: CORRECTION only. Keys must be in CORRECTABLE_CUSTOMER_FIELDS; validated
    #: in the service rather than here so the one refusal message lives in one
    #: place for both the self and staff paths.
    requested_changes: dict[str, str] = Field(default_factory=dict)


class RightsRequestStaffIn(BaseModel):
    """A handler logging a request that arrived off-platform (email, phone,
    post). It enters the same register, gets the same reference and the same
    clock - and, critically, starts UNVERIFIED."""

    customer_external_id: str = Field(min_length=1, max_length=256)
    request_type: str = Field(pattern=_TYPE_PATTERN)
    request_detail: str = Field(default="", max_length=_MAX_DETAIL)
    requested_changes: dict[str, str] = Field(default_factory=dict)
    channel: str = Field(default="STAFF", pattern=_CHANNEL_PATTERN)
    #: The date the request actually reached the organisation, when it is
    #: being back-entered. The clock runs from THIS, not from the moment a
    #: handler got round to typing it in - a fiduciary cannot extend its own
    #: response period by being slow to log a request.
    received_at: Optional[datetime] = None


class RightsRequestUpdateIn(BaseModel):
    """Triage: assign it, move it into progress, add a note."""

    assigned_to: Optional[str] = Field(default=None, max_length=128)
    status: Optional[str] = Field(default=None, pattern="^(VERIFYING|IN_PROGRESS)$")
    note: str = Field(default="", max_length=4000)
    #: An internal triage note is not shown to the principal unless the
    #: handler says so.
    visible_to_principal: bool = False


class RightsVerificationConfirmIn(BaseModel):
    """The six-digit code the principal received at the address on file, read
    back. Confirmed against `app/services/otp.py`, which owns the comparison,
    the attempt budget and the expiry - this module never compares it."""

    code: str = Field(min_length=4, max_length=12)


class CorrectionApplyIn(BaseModel):
    """s.12(1): apply the correction to the principal's record.

    `changes` may restate (or narrow) what the principal asked for - a handler
    who established that only the phone number was actually wrong applies only
    that - but every key must be in CORRECTABLE_CUSTOMER_FIELDS and every
    value must be non-empty. Omit it to apply exactly what was requested.
    """

    changes: Optional[dict[str, str]] = None
    note: str = Field(default="", max_length=4000)


class RightsFulfilIn(BaseModel):
    resolution_summary: str = Field(min_length=1, max_length=_MAX_DETAIL)


class RightsRejectIn(BaseModel):
    reason: str = Field(pattern=_REJECTION_PATTERN)
    #: Why, in prose, with the provision relied on. s.12(3) permits refusal
    #: only where retention is necessary for the specified purpose or for
    #: compliance with a law, so a refusal that cannot name one is not a
    #: lawful refusal.
    basis: str = Field(min_length=1, max_length=_MAX_DETAIL)


class ErasureApprovalIn(BaseModel):
    """Approve an ERASURE request and hand it to the R1-06 engine.

    There is no "erase now" here and no bypass of the R.8(2) notice: this
    creates an `erasure_jobs` row through
    `app/services/erasure.py::approve_rights_request_erasure` and inherits
    every guarantee that engine carries.
    """

    basis: str = Field(default="", max_length=2000)


# --------------------------------------------------------------------------- #
#  Inputs - nominations
# --------------------------------------------------------------------------- #

class NominationSelfIn(BaseModel):
    """s.14: the principal names an individual to exercise her rights on her
    death or incapacity. Accepted only from a verified context."""

    nominee_name: str = Field(min_length=1, max_length=256)
    nominee_email: str = Field(min_length=3, max_length=256)
    nominee_phone: str = Field(default="", max_length=64)
    nominee_relationship: str = Field(default="", max_length=64)


class NominationConfirmIn(BaseModel):
    """The nominee confirming, with the code sent to their own address."""

    code: str = Field(min_length=4, max_length=12)


class NominationRevokeIn(BaseModel):
    reason: str = Field(default="", max_length=4000)


class NominationActivateIn(BaseModel):
    """Staff activation on death or incapacity.

    `evidence_ref` is mandatory and non-empty at the database level too
    (`ck_nominations_active_is_authorised`): activating a nomination transfers
    the ability to exercise someone's rights at the moment they cannot object,
    and an activation nobody can check afterwards is indistinguishable from an
    account takeover.
    """

    ground: str = Field(pattern=_GROUND_PATTERN)
    evidence_ref: str = Field(min_length=1, max_length=256)
    note: str = Field(default="", max_length=4000)


# --------------------------------------------------------------------------- #
#  Outputs
# --------------------------------------------------------------------------- #

class RightsRequestEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    from_status: str = ""
    to_status: str = ""
    note: str = ""
    actor_username: str = ""
    created_at: datetime


class RightsRequestOut(BaseModel):
    """The full record. Returned to staff, and to the principal for her own
    request (with the timeline filtered to principal-visible entries - see
    routes/rights.py::_out)."""

    model_config = ConfigDict(from_attributes=True)

    reference_no: str
    request_type: str
    status: str
    channel: str
    request_detail: str = ""
    requested_changes: dict[str, Any] = Field(default_factory=dict)
    applied_changes: dict[str, Any] = Field(default_factory=dict)
    customer_id: int
    customer_external_id: str = ""
    source_app: str = ""

    received_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledgement_hours: int
    acknowledgement_due_at: datetime
    #: The tenant's published response period, snapshotted at receipt. Echoed
    #: on every read so the principal (and an auditor) can see the deadline
    #: and the promise it came from without a second lookup.
    response_days: int
    due_at: datetime
    days_remaining: int = 0
    overdue: bool = False

    identity_verified: bool = False
    verification_method: Optional[str] = None
    verified_at: Optional[datetime] = None
    verification_note: str = ""

    assigned_to: str = ""
    fulfilled_at: Optional[datetime] = None
    fulfilled_by: str = ""
    resolution_summary: str = ""
    rejection_reason: Optional[str] = None
    rejection_basis: str = ""
    closed_at: Optional[datetime] = None
    closed_by: str = ""
    closure_hash: Optional[str] = None
    package_hash: Optional[str] = None

    #: ERASURE only - the R1-06 job carrying the erasure out, and its live
    #: status. The rights request is fulfilled only once that job is EXECUTED.
    erasure_job_ref: Optional[str] = None
    erasure_job_status: Optional[str] = None
    nomination_ref: Optional[str] = None

    created_at: datetime
    updated_at: datetime
    events: list[RightsRequestEventOut] = Field(default_factory=list)


class RightsAcknowledgementOut(BaseModel):
    """What a submission returns.

    Deliberately NOT just `RightsRequestOut`: the DoD is that every request
    gets an acknowledgement and a due date at intake, and this is the
    acknowledgement - the reference to quote, the periods we published, the
    dates we owe by, whether we still need to establish identity, and the DPO
    contact R.9/E-08 requires every rights response to carry.
    """

    request: RightsRequestOut
    reference_no: str
    acknowledged: bool
    acknowledgement_message: str
    acknowledgement_hours: int
    acknowledgement_due_at: datetime
    response_days: int
    due_at: datetime
    identity_verified: bool
    #: What the principal must still do, in plain words, when identity has not
    #: been established at intake. Empty when there is nothing outstanding.
    next_step: str = ""
    data_protection_officer: str = ""
    grievance_url: str = ""
    board_complaint_url: str = ""
    #: Ids of the Notification rows queued, so a caller can follow delivery
    #: through GET /notifications.
    notification_ids: list[int] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  The s.11 access fulfilment package
# --------------------------------------------------------------------------- #

class AccessRecipientOut(BaseModel):
    """s.11(1)(b): one Data Fiduciary or Data Processor the personal data was
    shared with, and a description of what was shared.

    Assembled from `data_sharing_events` - the register that records real
    disclosures - never inferred from a processor list or a contract. A
    processor we have a contract with but never actually sent anything to is
    not a recipient, and saying it was would be a false statement about where
    someone's data went.
    """

    processor_name: str
    processor_type: str = ""
    country: str = ""
    purposes: list[str] = Field(default_factory=list)
    data_categories: list[str] = Field(default_factory=list)
    disclosures: int = 0
    first_shared_at: Optional[datetime] = None
    last_shared_at: Optional[datetime] = None
    legal_bases: list[str] = Field(default_factory=list)


class AccessProcessingActivityOut(BaseModel):
    """s.11(1)(a): one processing activity carried out on the principal's
    personal data, and the purpose it serves."""

    code: str
    name: str
    description: str = ""
    purpose_code: str = ""
    purpose_name: str = ""
    data_categories: list[str] = Field(default_factory=list)
    consent_status: str = ""


class AccessConsentSummaryOut(BaseModel):
    purpose_code: str
    purpose_name: str
    data_category: str
    processing_activity: str
    status: str
    consent_version: int = 1
    granted_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class AccessPackageOut(BaseModel):
    """The s.11 fulfilment package.

    One object answering all three limbs the Act asks for: the summary of
    personal data being processed, the processing activities undertaken, and
    the identities of everyone it was shared with plus what was shared.
    `package_hash` is SHA-256 over its canonical JSON, and the same value is
    recorded on the request and in the immutable audit row - so "what exactly
    did you send her?" stays answerable even though the package itself is
    generated on demand and never stored.
    """

    reference_no: str
    generated_at: datetime
    source_app: str
    #: The identifying fields held about the principal. Delivered to the
    #: principal herself, so unmasked - but see routes/rights.py, which only
    #: ever serves this over a verified context or to a permissioned handler.
    personal_data: dict[str, Any] = Field(default_factory=dict)
    consents: list[AccessConsentSummaryOut] = Field(default_factory=list)
    processing_activities: list[AccessProcessingActivityOut] = Field(default_factory=list)
    recipients: list[AccessRecipientOut] = Field(default_factory=list)
    #: Disclosures that were requested or refused rather than made. Reported
    #: separately so a REQUESTED-but-never-SENT event is never presented as a
    #: disclosure that happened.
    non_disclosures: list[dict[str, Any]] = Field(default_factory=list)
    consent_history_entries: int = 0
    receipts_issued: int = 0
    #: R.9 / E-08: every rights response carries the contact.
    data_protection_officer: str = ""
    dpo_contact: str = ""
    rights_url: str = ""
    grievance_url: str = ""
    board_complaint_url: str = ""
    package_hash: str = ""


# --------------------------------------------------------------------------- #
#  Nominations
# --------------------------------------------------------------------------- #

class NominationOut(BaseModel):
    """A nomination as the principal and the queue see it.

    `nominee_email` and `nominee_phone` are masked here even for the principal
    who supplied them: this record is read on a screen months or years later,
    the values add nothing to the decision being made, and they belong to a
    third party who never chose to have them displayed. `mask_identifier` is
    the same helper the customer list uses.
    """

    model_config = ConfigDict(from_attributes=True)

    nomination_ref: str
    status: str
    nominee_name: str
    nominee_email_masked: str = ""
    nominee_phone_masked: str = ""
    nominee_relationship: str = ""
    customer_id: int
    customer_external_id: str = ""
    source_app: str = ""
    nominated_at: datetime
    verification_sent_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    activated_by: Optional[str] = None
    activation_ground: Optional[str] = None
    activation_evidence_ref: str = ""
    revoked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class NominationAcknowledgementOut(BaseModel):
    nomination: NominationOut
    request: Optional[RightsRequestOut] = None
    message: str


# --------------------------------------------------------------------------- #
#  The queue
# --------------------------------------------------------------------------- #

class RightsQueueStatsOut(BaseModel):
    """The admin queue's header numbers and the three KPIs R2-05 owes.

    K-20 is `by_type`; K-21 is `median_acknowledgement_hours`; K-22 is
    `on_time_closure_rate` over `closed_total`. `app/services/kpi_catalogue.py`
    republishes exactly these numbers rather than recomputing them, so the
    dashboard and this screen cannot disagree.
    """

    total: int
    open: int
    overdue: int
    awaiting_verification: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_rejection_reason: dict[str, int]
    acknowledged_total: int
    acknowledged_within_commitment: int
    median_acknowledgement_hours: Optional[float] = None
    closed_total: int
    closed_within_period: int
    #: closed_within_period / closed_total as a percentage. None when nothing
    #: has closed yet - deliberately not 0.0, which would read as "we close
    #: nothing on time" rather than "there is nothing to measure".
    on_time_closure_rate: Optional[float] = None
    average_days_to_closure: Optional[float] = None
    fulfilled_total: int = 0
    rejected_total: int = 0
    nominations_total: int = 0
    nominations_active: int = 0
