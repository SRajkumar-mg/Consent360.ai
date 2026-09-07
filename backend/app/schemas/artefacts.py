"""R3-10: request/response models for the decision-validation endpoint and
the Consent Manager artefact API.

Separate module, not `schemas/schemas.py`, for the same parallel-lane reason
as `app/models/artefacts.py` - see that module's docstring.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
#  POST /decisions/evaluate - consent validation before processing
# --------------------------------------------------------------------------- #

class DecisionEvaluateIn(BaseModel):
    """A fiduciary system asking "may I process this, right now?".

    Identifiers are codes and external ids, never internal primary keys: the
    caller is an external system that has no visibility of this platform's
    row ids, and echoing an internal id back at us is not an authorisation.
    """

    customer_id: str = Field(..., min_length=1, max_length=128, description="The principal's external id at the fiduciary")
    purpose_code: str = Field(..., min_length=1, max_length=64)
    data_category_code: str = Field(..., min_length=1, max_length=64)
    processing_activity_code: str = Field(..., min_length=1, max_length=64)
    # Optional and, when present, must equal the API key's own tenant. Kept so
    # a caller can be explicit (and so a mismatch is a loud 403 rather than a
    # silently re-scoped request), exactly like /consent/customer-context.
    source_app: Optional[str] = Field(default=None, max_length=128)
    requested_by: str = Field(default="integration", max_length=64)


class DecisionEvaluateOut(BaseModel):
    decision: str
    allowed: bool
    reason: str
    customer_id: str
    purpose_code: str
    data_category_code: str
    processing_activity_code: str
    source_app: str
    consent_status: Optional[str] = None
    consent_version: Optional[int] = None
    consent_expires_at: Optional[datetime] = None
    policy_code: Optional[str] = None
    policy_version: Optional[int] = None
    decision_log_id: Optional[int] = None
    evaluated_at: datetime
    duration_ms: int
    request_id: Optional[str] = None


# --------------------------------------------------------------------------- #
#  Consent Manager registry and fiduciary onboarding (staff-managed)
# --------------------------------------------------------------------------- #

class ConsentManagerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    tenant_code: str = Field(..., min_length=1, max_length=64, description="Organization code whose API keys authenticate this CM")
    board_registration_number: str = Field(default="", max_length=128)
    registration_status: str = Field(default="PENDING", pattern=r"^(PENDING|REGISTERED|SUSPENDED|DEREGISTERED)$")
    contact_email: str = Field(default="", max_length=256)
    website_url: str = Field(default="", max_length=512)
    disclosures: dict[str, Any] = Field(default_factory=dict)


class ConsentManagerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=256)
    board_registration_number: Optional[str] = Field(default=None, max_length=128)
    registration_status: Optional[str] = Field(default=None, pattern=r"^(PENDING|REGISTERED|SUSPENDED|DEREGISTERED)$")
    contact_email: Optional[str] = Field(default=None, max_length=256)
    website_url: Optional[str] = Field(default=None, max_length=512)
    disclosures: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class ConsentManagerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    cm_ref: str
    name: str
    tenant_id: int
    tenant_code: str = ""
    board_registration_number: str = ""
    registration_status: str
    registered_at: Optional[datetime] = None
    website_url: str = ""
    is_active: bool
    onboarded_fiduciary_count: int = 0
    created_at: datetime


class FiduciaryOnboardingIn(BaseModel):
    source_app: str = Field(..., min_length=1, max_length=128, description="Code of the fiduciary tenant being onboarded")
    status: str = Field(default="ACTIVE", pattern=r"^(PENDING|ACTIVE|SUSPENDED|TERMINATED)$")
    allowed_purpose_codes: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)


class FiduciaryOnboardingUpdate(BaseModel):
    status: Optional[str] = Field(default=None, pattern=r"^(PENDING|ACTIVE|SUSPENDED|TERMINATED)$")
    allowed_purpose_codes: Optional[list[str]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class FiduciaryOnboardingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    consent_manager_id: int
    tenant_id: int
    source_app: str
    status: str
    allowed_purpose_codes: list[str] = Field(default_factory=list)
    onboarded_at: Optional[datetime] = None
    terminated_at: Optional[datetime] = None
    notes: str = ""


# --------------------------------------------------------------------------- #
#  The artefact API itself
# --------------------------------------------------------------------------- #

class ArtefactCreateIn(BaseModel):
    """Give consent, through a Consent Manager or directly.

    ``principal_action_reference`` is the CM's own evidence reference for the
    affirmative action the principal took in the CM's interface. It is
    mandatory for a CM caller: the CM is *attesting* that a real person acted,
    and an attestation with nothing behind it is not evidence. It is recorded
    on every ConsentEvidence row this call produces.
    """

    source_app: Optional[str] = Field(default=None, max_length=128, description="The fiduciary this consent is for; required for a Consent Manager caller")
    customer_id: Optional[str] = Field(default=None, max_length=128)
    email: Optional[str] = Field(default=None, max_length=256)
    purpose_codes: list[str] = Field(..., min_length=1, max_length=50)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)
    language: str = Field(default="en", max_length=8)
    principal_action_reference: Optional[str] = Field(default=None, max_length=256)
    reason: str = Field(default="", max_length=1000)


class ArtefactUpdateIn(BaseModel):
    """Manage an existing artefact: revise the purpose set it covers.

    The list is the *desired end state*, not a delta - purposes present here
    and not in the artefact are granted, purposes in the artefact and not here
    are withdrawn. An empty list is rejected; withdrawing everything is what
    the withdraw endpoint is for, and it must be an explicit act.
    """

    purpose_codes: list[str] = Field(..., min_length=1, max_length=50)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)
    principal_action_reference: Optional[str] = Field(default=None, max_length=256)
    reason: str = Field(default="", max_length=1000)


class ArtefactWithdrawIn(BaseModel):
    """Withdraw an artefact, wholly or for named purposes only."""

    purpose_codes: Optional[list[str]] = Field(default=None, max_length=50)
    principal_action_reference: Optional[str] = Field(default=None, max_length=256)
    reason: str = Field(default="", max_length=1000)


class ArtefactEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_ref: str
    artefact_version: int
    event_type: str
    payload_hash: str
    signature: str
    signature_alg: str
    occurred_at: datetime
    #: Recomputed from the stored payload at read time - False means the
    #: stored payload or signature no longer matches what was signed.
    signature_valid: bool = True


class ArtefactPurposeOut(BaseModel):
    purpose_code: str
    purpose_name: str = ""
    status: str
    consent_ids: list[int] = Field(default_factory=list)


class ArtefactOut(BaseModel):
    artefact_ref: str
    schema_version: str
    status: str
    artefact_version: int
    source_app: str
    consent_manager_ref: Optional[str] = None
    principal_ref: str
    #: The fiduciary's own external id for the principal. Present only for a
    #: fiduciary acting on its own tenant; always omitted for a Consent
    #: Manager caller - see the data-blind decision record.
    principal_external_id: Optional[str] = None
    purposes: list[ArtefactPurposeOut] = Field(default_factory=list)
    expires_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    #: The ISO/IEC TS 27560-modelled record for the current version.
    payload: dict[str, Any] = Field(default_factory=dict)
    events: list[ArtefactEventOut] = Field(default_factory=list)


class ArtefactListOut(BaseModel):
    total: int
    artefacts: list[ArtefactOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Disclosures, schema descriptor and metrics
# --------------------------------------------------------------------------- #

class ConsentManagerDisclosureOut(BaseModel):
    """First Schedule Part B 11 (gap J-04) transparency disclosures."""

    cm_ref: str
    name: str
    board_registration_number: str = ""
    registration_status: str
    registered_at: Optional[datetime] = None
    website_url: str = ""
    contact_email: str = ""
    promoters: list[dict[str, Any]] = Field(default_factory=list)
    directors: list[dict[str, Any]] = Field(default_factory=list)
    key_managerial_personnel: list[dict[str, Any]] = Field(default_factory=list)
    shareholders_above_two_percent: list[dict[str, Any]] = Field(default_factory=list)
    data_blind: bool = True
    onboarded_fiduciaries: list[str] = Field(default_factory=list)
    published_at: datetime


class ArtefactSchemaOut(BaseModel):
    """Machine-readable descriptor of the artefact payload this API emits.

    ISO/IEC TS 27560:2023 clause 6.3.2.1 is a `shall`: an organization that
    creates its own consent-record schema "shall publish or reference the
    schema(s) being used and maintain documentation necessary for its correct
    technical implementation". This response, served without a credential, is
    how that obligation is discharged; `schema_version` inside every artefact
    payload is the identifier it describes.
    """

    schema_version: str
    schema_reference: str
    modelled_on: str
    standard_status: str
    conformance_statement: str
    #: False, always. TS 27560's JSON examples are informative; there is no
    #: normative encoding and no validator. Stated as a field rather than as
    #: prose so an integrator's own tooling can read it.
    normative_json_encoding: bool = False
    signature_alg: str
    top_level_fields: list[str] = Field(default_factory=list)
    #: Which fields came from the ISO text, which from the DPV implementation
    #: guide, and which are ours.
    field_provenance: dict[str, str] = Field(default_factory=dict)
    field_notes: dict[str, str] = Field(default_factory=dict)


class ConsentManagerMetricsOut(BaseModel):
    """CM-06 / K-42 / K-43."""

    window_hours: int
    total_calls: int
    # None, not a fabricated 100.0/0.0, when total_calls is 0 - see
    # app/api/routes/consent_manager.py::metrics.
    availability_pct: Optional[float] = None
    error_rate_pct: Optional[float] = None
    latency_ms_p50: Optional[int] = None
    latency_ms_p95: Optional[int] = None
    latency_ms_max: Optional[int] = None
    record_retrieval_ms_p95: Optional[int] = None
    onboarded_fiduciary_count: int = 0
    registered_consent_manager_count: int = 0
    artefacts_total: int = 0
    artefacts_active: int = 0
    artefacts_withdrawn: int = 0
    per_endpoint: list[dict[str, Any]] = Field(default_factory=list)
