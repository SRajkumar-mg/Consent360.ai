from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, field_validator, model_validator

from app.models.entities import LEGAL_BASIS_VALUES, NOTIFICATION_CHANNELS, NOTIFICATION_EVENT_TYPES


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserOut"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    full_name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role_id: int
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    role_id: Optional[int] = None
    is_active: Optional[bool] = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    permissions: list[str]
    is_system: bool


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    email: str
    role_id: int
    role_name: str = ""
    role_permissions: list[str] = []
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


class UserListOut(BaseModel):
    id: int
    username: str
    full_name: str
    email: str
    role_id: int
    role_name: str = ""
    is_active: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
class CustomerContextIn(BaseModel):
    customer_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source_app: Optional[str] = None
    callback_url: Optional[str] = None
    # Opt-in, per-request override of the tenant-level default (see
    # app.core.api_keys.SCOPE_FIDUCIARY_ASSERT): True asserts the caller has
    # already verified this principal's identity itself (requires the API
    # key to carry that scope, or the request is rejected outright); False
    # forces the normal OTP-verification path even for a key that has the
    # scope; None (the default) defers entirely to the key's tenant-level
    # setting. See app/api/routes/integration.py::_resolve_fiduciary_assertion.
    fiduciary_asserted: Optional[bool] = None


class CustomerContextOut(BaseModel):
    context_token: str
    context_id: int
    customer_id: str
    name: str
    expires_in_minutes: int
    source_app: str
    ui_url: str
    request_id: Optional[str] = None


# ---------------------------------------------------------------------------
# CRM portal
# ---------------------------------------------------------------------------
class CrmCustomerLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=20)
    source_app: str = "CRM_PORTAL"


class CrmCustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    age: Optional[int] = None
    # aadhar_number removed (R1-12/H-11) - see the same note on
    # app.models.entities.CrmCustomer.
    address: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime


class CrmLoginOut(BaseModel):
    customer: CrmCustomerOut
    created: bool = False
    context_token: str
    consent_portal_url: str
    expires_in_minutes: int


class CrmDirectoryLoginOut(BaseModel):
    customer: CrmCustomerOut
    created: bool = False


class CustomerCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = "ACTIVE"
    source_app: Optional[str] = "MANUAL"


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
    source_app: Optional[str] = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    external_id: str
    name: str
    email: str
    phone: str
    status: str
    source_app: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Data categories / activities
# ---------------------------------------------------------------------------
class DataCategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str = ""
    is_active: bool = True


class DataCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DataCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    description: str
    is_active: bool


class ProcessingActivityIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str = ""
    is_active: bool = True


class ProcessingActivityUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ProcessingActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    description: str
    is_active: bool


# ---------------------------------------------------------------------------
# Purposes
# ---------------------------------------------------------------------------
class DataItemIn(BaseModel):
    """One itemised data element collected for a purpose (A-01/B-04):
    `necessity=True` means the item is strictly required for the purpose
    (vs. merely useful/optional), which is what a Notice's itemised list and
    the minimisation check both read."""
    data_category_id: int
    necessity: bool = True
    description: str = ""


class DataItemOut(DataItemIn):
    data_category_code: str = ""
    data_category_name: str = ""


def _validate_legal_basis(v: Optional[str]) -> Optional[str]:
    if v is not None and v not in LEGAL_BASIS_VALUES:
        raise ValueError(
            f"legal_basis must be one of {LEGAL_BASIS_VALUES} (CONSENT or a DPDP s.7(a)-(i) clause)"
        )
    return v


def check_data_items_cover_categories(data_category_ids: list[int], data_items: list["DataItemIn"]) -> None:
    """R1-12/B-04: every data_category_id a purpose (version) actually
    collects must carry an explicit necessity marking, and a data_items
    entry must not reference a category the purpose doesn't even list -
    otherwise `necessity` is decorative rather than enforced (a purpose
    could add a whole new data category with no reviewer ever having to say
    whether it's actually necessary). Called from PurposeIn's own validator
    (a full payload, always has both fields) and explicitly from
    app/api/routes/purposes.py::update_purpose after merging a PATCH-style
    PurposeUpdate onto the current version (partial updates cannot be
    validated in isolation - see that call site).
    """
    item_ids = [d.data_category_id if isinstance(d, DataItemIn) else d["data_category_id"] for d in data_items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("data_items must not list the same data_category_id more than once")
    item_id_set = set(item_ids)
    category_id_set = set(data_category_ids)
    missing = category_id_set - item_id_set
    if missing:
        raise ValueError(
            "Every data_category_id in data_category_ids must have a matching, necessity-marked "
            f"data_items entry (DPDP s.6(1) minimisation, gap B-04) - missing for: {sorted(missing)}"
        )
    extra = item_id_set - category_id_set
    if extra:
        raise ValueError(
            f"data_items references data_category_id(s) not listed in data_category_ids: {sorted(extra)}"
        )


class ReviewChecklistIn(BaseModel):
    """R2-09/A-09: the plain-language & dark-pattern review record that
    gates publishing, persisted server-side (PurposeVersion.checklist /
    PolicyVersion.checklist) instead of only in the reviewing browser's
    localStorage - see frontend/src/components/NoticeChecklist.tsx
    (NoticeChecklistRecord), whose completedAt/reviewer/items shape this
    mirrors under snake_case field names to match this backend's own JSON
    field-naming convention."""
    reviewer: str = Field(min_length=1, max_length=256)
    completed_at: datetime
    items: list[str] = []


class ReviewChecklistOut(ReviewChecklistIn):
    pass


class PurposeIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str = ""
    legal_basis: str = "CONSENT"
    requires_consent: bool = True
    retention_period_days: int = Field(default=365, ge=0)
    data_category_ids: list[int] = []
    processing_activity_ids: list[int] = []
    data_items: list[DataItemIn] = []
    services_enabled: str = ""
    child_restricted: bool = False
    retention_policy_id: Optional[str] = None
    consent_text: str = ""
    translations: Optional[dict] = None
    checklist: Optional[ReviewChecklistIn] = None

    @field_validator("legal_basis")
    @classmethod
    def _check_legal_basis(cls, v: str) -> str:
        return _validate_legal_basis(v)

    @model_validator(mode="after")
    def _check_data_items(self) -> "PurposeIn":
        check_data_items_cover_categories(self.data_category_ids, self.data_items)
        return self


class PurposeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    legal_basis: Optional[str] = None
    requires_consent: Optional[bool] = None
    retention_period_days: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    status: Optional[str] = None
    data_category_ids: Optional[list[int]] = None
    processing_activity_ids: Optional[list[int]] = None
    data_items: Optional[list[DataItemIn]] = None
    services_enabled: Optional[str] = None
    child_restricted: Optional[bool] = None
    retention_policy_id: Optional[str] = None
    consent_text: Optional[str] = None
    translations: Optional[dict] = None
    checklist: Optional[ReviewChecklistIn] = None
    # R1-09/P-01: downgrade a free-text field from MATERIAL to COSMETIC, with
    # the publisher's justification recorded on the change log. Only
    # `description`, `services_enabled` and `consent_text` may be downgraded -
    # a machine cannot tell a typo fix from a rewrite of scope, so the default
    # is the safe direction and a human may override it on the record.
    # Anything else (a data category added, a retention period extended, the
    # lawful basis changed) is decided by the direction of the change and is
    # refused with 422 rather than silently ignored. See
    # app/services/material_change.py.
    cosmetic_overrides: Optional[dict[str, str]] = None

    @field_validator("legal_basis")
    @classmethod
    def _check_legal_basis(cls, v: Optional[str]) -> Optional[str]:
        return _validate_legal_basis(v)


class PurposeVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    purpose_id: int
    version_number: int
    name: str
    description: str
    legal_basis: str
    requires_consent: bool
    retention_period_days: int
    data_category_ids: list[int]
    processing_activity_ids: list[int]
    data_items: list[dict] = []
    services_enabled: str = ""
    child_restricted: bool = False
    retention_policy_id: Optional[str] = None
    consent_text: str
    translations: dict = {}
    checklist: Optional[dict] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    is_current: bool
    created_by: str


class PurposeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    description: str
    legal_basis: str
    requires_consent: bool
    retention_period_days: int
    services_enabled: str = ""
    child_restricted: bool = False
    retention_policy_id: Optional[str] = None
    status: str
    current_version: int
    is_active: bool
    created_at: datetime
    versions: list[PurposeVersionOut] = []
    data_categories: list[DataCategoryOut] = []
    processing_activities: list[ProcessingActivityOut] = []
    # R1-09/P-01: present only on the response to a PUT that published a new
    # version - what the change was classified as and, when it was material,
    # how many principals now have to consent again before processing may
    # continue. Null everywhere else.
    change: Optional["ChangeClassificationOut"] = None


class PurposeVersionCreate(BaseModel):
    reason: str = ""
    checklist: Optional[ReviewChecklistIn] = None


class CoverageActivityOut(BaseModel):
    id: int
    code: str
    name: str
    covered: bool
    gateways: list[str] = []
    purpose_codes: list[str] = []


class CoverageReportOut(BaseModel):
    """K-10 lawful-gateway coverage: processing activities with a valid
    gateway (consent or an s.7 clause) via at least one active purpose,
    divided by all activities."""
    total_activities: int
    covered_activities: int
    coverage_pct: Optional[float] = None  # None when the denominator is empty: see d47cf58
    activities: list[CoverageActivityOut] = []


class PurposeTemplateOut(BaseModel):
    """Ready-to-submit `PurposeIn`-shaped scaffolding for a lawful-gateway
    family the workbook calls out by name (L-02 employment, L-03 State
    processing) - a starting point for the admin console's create-purpose
    form, not a persisted record."""
    key: str
    label: str
    guidance: str
    purpose: PurposeIn


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------
class PolicyRuleIn(BaseModel):
    purpose_code: str
    data_category_code: str
    processing_activity_code: str
    decision: str = Field(pattern=r"^(ALLOW|DENY)$")
    requires_active_consent: bool = True
    priority: int = 10


class PolicyRuleOut(BaseModel):
    purpose_code: str
    purpose_name: str = ""
    data_category_code: str
    data_category_name: str = ""
    processing_activity_code: str
    processing_activity_name: str = ""
    decision: str
    requires_active_consent: bool
    priority: int


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str = ""
    default_decision: str = Field(default="REQUIRE_CONSENT", pattern=r"^(ALLOW|DENY|REQUIRE_CONSENT)$")
    rules: list[PolicyRuleIn] = []
    checklist: Optional[ReviewChecklistIn] = None


class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_decision: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None
    rules: Optional[list[PolicyRuleIn]] = None
    checklist: Optional[ReviewChecklistIn] = None
    #: P-02: downgrade a free-text field from MATERIAL to COSMETIC, mirroring
    #: PurposeUpdate.cosmetic_overrides. Nothing on a Policy is downgradeable
    #: (POLICY_OVERRIDABLE_FIELDS is empty - see app/services/
    #: material_change.py), so any key here is refused with 422; the field
    #: still exists so that refusal is explicit rather than a 422 for an
    #: "unrecognised field".
    cosmetic_overrides: Optional[dict[str, str]] = None


class PolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    policy_id: int
    version_number: int
    rules: list[Any]
    default_decision: str
    checklist: Optional[dict] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    is_current: bool
    created_by: str


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    description: str
    status: str
    current_version: int
    is_active: bool
    created_at: datetime
    versions: list[PolicyVersionOut] = []
    #: P-02, mirrors PurposeOut.change: what publishing this update actually
    #: did, echoed back to the publisher on the response to their own edit.
    change: Optional["ChangeClassificationOut"] = None


class PolicyVersionCreate(BaseModel):
    reason: str = ""
    checklist: Optional[ReviewChecklistIn] = None


# ---------------------------------------------------------------------------
# Notices (R1-04: A-01, A-02, A-07, D-04, A-12)
# ---------------------------------------------------------------------------
class NoticeLangContent(BaseModel):
    title: str = ""
    body: str = ""


class NoticeIn(BaseModel):
    purpose_id: int
    language_default: str = "en"
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1)
    translations: dict[str, NoticeLangContent] = {}
    data_items: list[DataItemIn] = []
    services_enabled: str = ""
    retention_period_days: Optional[int] = Field(default=None, ge=0)
    retention_note: str = ""
    child_restricted: bool = False
    # A-09: the plain-language & dark-pattern review record for this notice's
    # first draft. Not required to create a draft - only to publish it (see
    # POST /notices/{id}/publish) - but a caller may attach one immediately.
    checklist: Optional[ReviewChecklistIn] = None


class NoticeUpdate(BaseModel):
    language_default: Optional[str] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=512)
    body: Optional[str] = Field(default=None, min_length=1)
    translations: Optional[dict[str, NoticeLangContent]] = None
    data_items: Optional[list[DataItemIn]] = None
    services_enabled: Optional[str] = None
    retention_period_days: Optional[int] = Field(default=None, ge=0)
    retention_note: Optional[str] = None
    child_restricted: Optional[bool] = None
    is_active: Optional[bool] = None
    # A-09: attaches/replaces the plain-language & dark-pattern review record
    # on the draft version this update targets - see ReviewChecklistIn and
    # update_notice()'s reset-on-content-change handling of this field.
    checklist: Optional[ReviewChecklistIn] = None


class NoticeVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    notice_id: int
    version_number: int
    language_default: str
    title: str
    body: str
    translations: dict = {}
    data_items: list[dict] = []
    purposes: list[dict] = []
    services_enabled: str = ""
    retention_period_days: Optional[int] = None
    retention_note: str = ""
    child_restricted: bool = False
    links: dict = {}
    contact_snapshot: dict = {}
    # A-09: the stored review record - the evidence that this version's
    # plain-language & dark-pattern checklist was completed before it was
    # published. Null on a version published before this gate existed
    # (grandfathered - see Notice.checklist's docstring) or on a draft that
    # has not been reviewed yet.
    checklist: Optional[dict] = None
    content_hash: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    is_current: bool
    published_by: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    created_by: str


class NoticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    purpose_id: int
    purpose_code: str = ""
    purpose_name: str = ""
    status: str
    current_version: int
    is_active: bool
    created_at: datetime
    versions: list[NoticeVersionOut] = []


class PublicNoticeOut(BaseModel):
    """The public, unauthenticated render of a published notice - the exact
    payload a cookie banner/CMP shows and that a consent event records
    against (A-07)."""
    tenant_code: str
    purpose_code: str
    purpose_name: str
    notice_version_id: int
    version_number: int
    language_requested: str
    language_served: str
    title: str
    body: str
    data_items: list[dict] = []
    services_enabled: str
    legal_basis: str
    requires_consent: bool
    retention_period_days: Optional[int] = None
    retention_note: str = ""
    child_restricted: bool
    links: dict = {}
    contact: dict = {}
    content_hash: Optional[str] = None
    effective_from: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------
class ClientContext(BaseModel):
    language: str = "en"
    notice_version: Optional[int] = None
    banner_version: Optional[str] = None
    ui_control_id: Optional[str] = None
    screen_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    affirmative_action: str = Field(default="CLICK", pattern=r"^(CLICK|TOGGLE|OTP|DOCUMENT|CALL)$")
    affirmative_reference: Optional[str] = None
    interaction_step: Optional[int] = None
    # Global Privacy Control, as the calling client claims it (kept
    # separately from the server-observed Sec-GPC header - see
    # ConsentEvidence.gpc_signal's docstring and
    # app/api/routes/portal.py::portal_grant/portal_withdraw, which read the
    # actual request header rather than trusting this field, exactly like
    # ip_address/user_agent above). None means the client didn't report one.
    gpc_signal: Optional[bool] = None


class ConsentAction(BaseModel):
    reason: str = ""
    collection_method: str = "UI"
    expires_in_days: Optional[int] = Field(default=None, ge=1)
    consent_text: Optional[str] = None
    context: ClientContext = ClientContext()


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    customer_external_id: str = ""
    purpose_id: int
    purpose_name: str = ""
    purpose_code: str = ""
    purpose_version: int = 1
    data_category_id: int
    data_category_name: str = ""
    processing_activity_id: int
    processing_activity_name: str = ""
    consent_version: int
    status: str
    granted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    denied_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    renewed_at: Optional[datetime] = None
    requested_at: Optional[datetime] = None
    collection_method: str
    source_app: str
    policy_code: str = ""
    policy_version: Optional[int] = None
    notice_version_id: Optional[int] = None
    consent_text: str = ""
    created_at: datetime


class ConsentHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    action: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    reason: str
    consent_version: int
    actor_username: str
    source_app: str
    request_id: Optional[str] = None
    details: dict
    created_at: datetime


class ConsentEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    evidence_ref: str
    collected_at: datetime
    collected_by: str
    collection_method: str
    source_app: str
    consent_version: int
    purpose_version: int
    policy_version: Optional[int] = None
    notice_version_id: Optional[int] = None
    notice_hash: Optional[str] = None
    language: str = "en"
    content_hash: Optional[str] = None
    request_id: Optional[str] = None
    gpc_signal: Optional[bool] = None
    # None = the row carries no signature, so there is nothing to verify (a
    # legacy row); True/False = the stored signature does / does not still
    # match this row's hashes. See ConsentEvidence.signature_valid.
    signature_valid: Optional[bool] = None
    details: dict


class ConsentDetailOut(BaseModel):
    consent: ConsentOut
    history: list[ConsentHistoryOut]
    evidence: list[ConsentEvidenceOut]


class CustomerConsentSummary(BaseModel):
    customer: CustomerOut
    total_purposes: int
    status_counts: dict[str, int]
    expiring_soon: list[ConsentOut]
    consents: list[ConsentOut]


# ---------------------------------------------------------------------------
# Consent receipts, sharing events, objections (R1-08: B-09, D-05, M-02, C-06)
# ---------------------------------------------------------------------------
class ConsentReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_ref: str
    consent_id: int
    evidence_id: int
    customer_id: int
    source_app: str
    action: str
    consent_version: int
    notice_version_id: Optional[int] = None
    payload: dict
    payload_hash: str
    signature: str
    issued_at: datetime
    valid: bool = True


class DataSharingEventIn(BaseModel):
    customer_external_id: str = Field(min_length=1, max_length=64)
    processor_id: int
    purpose_code: str = Field(min_length=1, max_length=64)
    data_category_codes: list[str] = Field(min_length=1)
    event_type: str = Field(pattern=r"^(REQUESTED|SENT|DENIED)$")
    consent_id: Optional[int] = None
    reason: str = ""


class DataSharingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    processor_id: int
    processor_name: str = ""
    purpose_id: int
    purpose_code: str = ""
    consent_id: Optional[int] = None
    data_category_ids: list[int] = []
    data_category_codes: list[str] = []
    event_type: str
    legal_basis: str
    reason: str
    actor_username: str
    source_app: str
    occurred_at: datetime
    signature_valid: bool = True


class ObjectionIn(BaseModel):
    customer_external_id: str = Field(min_length=1, max_length=64)
    purpose_code: str = Field(min_length=1, max_length=64)
    reason: str = ""


class ObjectionSelfIn(BaseModel):
    purpose_code: str = Field(min_length=1, max_length=64)
    reason: str = ""


class ObjectionResolveIn(BaseModel):
    resolution_note: str = ""


class ObjectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    purpose_id: int
    purpose_code: str = ""
    consent_id: Optional[int] = None
    reason: str
    status: str
    source_app: str
    objected_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_note: str = ""


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event: str
    actor_username: str
    actor_role: str
    source_app: str
    customer_id: Optional[int] = None
    customer_external_id: Optional[str] = None
    consent_id: Optional[int] = None
    purpose_id: Optional[int] = None
    purpose_code: Optional[str] = None
    policy_id: Optional[int] = None
    policy_code: Optional[str] = None
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    consent_version: Optional[int] = None
    policy_version: Optional[int] = None
    decision: Optional[str] = None
    reason: str
    request_id: Optional[str] = None
    details: dict
    created_at: datetime


class AuditExportEventOut(AuditEventOut):
    """AuditEventOut plus the fields a recipient needs to independently
    re-verify the hash chain from the export alone (tenant_id groups rows
    into chains; prev_hash/entry_hash are the links; actor_type/actor_id are
    part of the hashed payload - see app.core.audit_chain._canonical)."""
    tenant_id: Optional[int] = None
    actor_id: Optional[str] = None
    actor_type: str
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None


class CustomerPortalOut(BaseModel):
    """Data-subject self-service view. Strictly scoped to ONE customer AND ONE source."""
    customer: CustomerOut
    source_app: str
    token_type: str = "consent-context"
    status_counts: dict[str, int] = {}
    consents: list[ConsentOut] = []
    history: list[ConsentHistoryOut] = []
    audit: list[AuditEventOut] = []


class AuditFilters(BaseModel):
    customer_id: Optional[str] = None
    purpose_code: Optional[str] = None
    event: Optional[str] = None
    actor: Optional[str] = None
    decision: Optional[str] = None
    source_app: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class DashboardMetrics(BaseModel):
    total_customers: int
    total_consents: int
    active_consents: int
    pending_consents: int
    withdrawn_consents: int
    denied_consents: int
    expired_consents: int
    expiring_soon: int
    granted_consents: int
    total_purposes: int
    total_policies: int


class StatusBucket(BaseModel):
    status: str
    count: int


class PurposeBucket(BaseModel):
    purpose_code: str
    purpose_name: str
    active: int
    total: int


class DashboardResponse(BaseModel):
    metrics: DashboardMetrics
    status_distribution: list[StatusBucket]
    purpose_distribution: list[PurposeBucket]
    recent_consent_activity: list[AuditEventOut]
    recent_audit_events: list[AuditEventOut]
    expiring_consents: list[ConsentOut]


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------
class MessageOut(BaseModel):
    message: str


class ContextResolveOut(BaseModel):
    customer: CustomerOut
    token_type: str = "consent-context"


class OptionOut(BaseModel):
    value: str
    label: str


# ---------------------------------------------------------------------------
# Customer portal (self-service)
# ---------------------------------------------------------------------------
class PortalPurposeOut(BaseModel):
    code: str
    name: str
    description: str = ""
    legal_basis: str = ""
    requires_consent: bool = True
    retention_period_days: int = 365
    consent_text: str = ""
    translations: dict = {}
    status: str  # GRANTED | PARTIAL | NOT_GRANTED
    granted_count: int = 0
    total_count: int = 0
    # R2-11/R1-09 (P-01): the re-consent flag, surfaced to the principal.
    #
    # Without these the portal shows a purpose whose consent was flagged by a
    # material change as plainly "GRANTED" - because the re-pointed consent
    # sits in UPDATED, which the overview counts as active - while the
    # decision engine is simultaneously refusing to process under it. The
    # principal is then the only party who has not been told, which is
    # precisely backwards: she is the one whose agreement is missing.
    re_consent_required: bool = False
    #: How many of this purpose's consent rows are flagged, out of
    #: `total_count`. A purpose can be partially flagged when only some of its
    #: category x activity rows were caught by the change.
    re_consent_count: int = 0
    re_consent_requested_at: Optional[datetime] = None
    re_consent_campaign_ref: Optional[str] = None
    #: The classifier's own prose reason for calling the change material -
    #: what she is being asked to agree to that she did not agree to before.
    #: This is the substantive notice the prompt has to carry; a prompt that
    #: says only "something changed" is not an informed consent moment.
    re_consent_reason: str = ""
    #: The purpose version now in force. Shown next to the reason so "v2 -> v3"
    #: is checkable against the published change log.
    purpose_version_number: Optional[int] = None


class PortalOverview(BaseModel):
    customer: CustomerOut
    purposes: list[PortalPurposeOut]


class PortalActionIn(BaseModel):
    purpose_code: str = Field(min_length=1, max_length=64)
    context: ClientContext = ClientContext()


class PortalVerifyConfirmIn(BaseModel):
    code: str = Field(min_length=4, max_length=8)


class PortalActionOut(BaseModel):
    purpose_code: str
    action: str  # granted | withdrawn
    affected: int
    message: str


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    domain: str = ""
    description: str = ""
    logo_url: str = ""


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: Optional[bool] = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    domain: str
    description: str
    logo_url: str
    is_active: bool
    dpo_name: str = ""
    dpo_email: str = ""
    dpo_phone: str = ""
    withdraw_url: str = ""
    rights_url: str = ""
    grievance_url: str = ""
    board_complaint_url: str = ""
    grievance_response_days: int = 90
    default_language: str = "en"
    environment: str = "DEV"
    settings: dict = {}
    created_at: datetime
    updated_at: datetime


class TenantSettingsUpdate(BaseModel):
    dpo_name: Optional[str] = None
    # Plain str (not EmailStr) so that "" round-trips as "clear the DPO email"
    # instead of a 422 — the admin console's settings form is a single
    # uncontrolled text input the admin can blank out and save, and
    # Organization.dpo_email is a NOT NULL column defaulting to "", so the
    # sane behaviour for a blank field is to store "" like every other
    # optional text column here, not reject the request. Non-empty values
    # are still validated as real email addresses below.
    dpo_email: Optional[str] = None
    dpo_phone: Optional[str] = None
    withdraw_url: Optional[str] = None
    rights_url: Optional[str] = None
    grievance_url: Optional[str] = None
    board_complaint_url: Optional[str] = None
    grievance_response_days: Optional[int] = Field(default=None, ge=1, le=90)
    default_language: Optional[str] = None
    environment: Optional[str] = Field(default=None, pattern=r"^(DEV|PROD)$")
    settings: Optional[dict] = None

    @field_validator("dpo_email")
    @classmethod
    def _validate_dpo_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        return str(TypeAdapter(EmailStr).validate_python(v))


class PublicPrivacyContactOut(BaseModel):
    tenant_code: str
    tenant_name: str
    dpo_name: str
    dpo_email: str
    dpo_phone: str


class PublicRightsOut(BaseModel):
    tenant_code: str
    tenant_name: str
    rights_url: str
    withdraw_url: str
    grievance_url: str
    board_complaint_url: str
    grievance_response_days: int


class PublicPurposeOut(BaseModel):
    purpose_version_id: int
    code: str
    name: str
    description: str
    legal_basis: str
    requires_consent: bool
    retention_period_days: int
    consent_text: str
    data_categories: list[str] = []
    processing_activities: list[str] = []
    translations: dict = {}


class OrganizationUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    full_name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="org_viewer", pattern=r"^(org_admin|org_viewer|jobhub_admin|jobhub_viewer|codex_admin|codex_viewer|skilllearn_admin|skilllearn_viewer)$")


class OrganizationUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    organization_id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


class OrganizationLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class OrganizationLoginResponse(BaseModel):
    access_token: str
    # Organization access tokens expire in 60 minutes and there is no
    # /organizations/auth/refresh route yet, so an org user is currently
    # forced through a full re-login every hour. Optional here so the route
    # (app/api/routes/organizations.py, another workstream's file - see the
    # lane report) can start returning one without a second schema change,
    # and so today's responses, which omit it, stay valid.
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: OrganizationUserOut
    organization: OrganizationOut


class OrganizationRefreshRequest(BaseModel):
    """Body for the /organizations/auth/refresh route that still needs
    building. Org tokens already carry iss/aud/jti, so that route should
    validate and re-mint on the same scheme (ctx="org-auth",
    sub_type="org_user") rather than introduce a parallel one."""
    refresh_token: str


class OrganizationDashboardOut(BaseModel):
    organization: OrganizationOut
    users: list[OrganizationUserOut]
    portal_users: list[CustomerOut] = []
    total_customers: int = 0
    total_consents: int = 0
    active_consents: int = 0
    consent_summary: list[dict] = []
    recent_activity: list[AuditEventOut] = []


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = ["integration.write"]
    expires_at: Optional[datetime] = None
    # R3 "squat-then-claim": issuing the first key for an Organization that
    # already has Customer rows attached is refused unless explicitly
    # acknowledged - see app/api/routes/organizations.py::create_api_key.
    # Normal onboarding (seed_orgs.py then seed_api_keys.py, or an admin
    # creating an org via POST /organizations and issuing its first key
    # immediately) never has pre-existing customers at this point, so this
    # is never needed for a legitimately fresh tenant.
    confirm_preexisting_data: bool = False


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    rotated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None


class ApiKeyCreateOut(ApiKeyOut):
    plaintext_key: str


# ---------------------------------------------------------------------------
# Notifications (R3-06: O-01, O-02, O-04, C-04)
# ---------------------------------------------------------------------------
class NotificationTemplateIn(BaseModel):
    event_type: str = Field(pattern="^(" + "|".join(NOTIFICATION_EVENT_TYPES) + ")$")
    channel: str = Field(pattern="^(" + "|".join(NOTIFICATION_CHANNELS) + ")$")
    language: str = Field(default="en", min_length=2, max_length=8)
    subject: str = ""
    body_template: str = Field(min_length=1)
    is_active: bool = True


class NotificationTemplateUpdate(BaseModel):
    subject: Optional[str] = None
    body_template: Optional[str] = Field(default=None, min_length=1)
    is_active: Optional[bool] = None


class NotificationTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: Optional[int] = None
    event_type: str
    channel: str
    language: str
    subject: str
    body_template: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: str


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: Optional[int] = None
    event_type: str
    channel: str
    language: str
    subject: str
    # R2-11/A-08: the message itself, not only its subject line.
    #
    # A legacy notice under s.5(2) IS its body - the itemised particulars, the
    # rights, the withdrawal path. A principal shown only "Notice about data
    # collected before ..." in her portal has not been given the notice, and a
    # compliance screen that can show what was sent is the difference between
    # evidence and an assertion. Both audiences for this schema
    # (GET /portal/notifications, which is the principal's own record, and
    # GET /notifications, which needs `audit.view`) are entitled to it.
    body: str = ""
    status: str
    provider_ref: Optional[str] = None
    retry_count: int
    #: Why the last attempt failed, when it did. A FAILED row with no reason is
    #: not delivery evidence, it is a shrug.
    last_error: Optional[str] = None
    source_app: str
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    created_at: datetime


class NotificationTriggerIn(BaseModel):
    """Staff-invoked manual trigger for an event type with no automated
    upstream workflow yet - see NOTIFICATION_EVENT_TYPES's docstring."""
    customer_external_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(pattern="^(" + "|".join(NOTIFICATION_EVENT_TYPES) + ")$")
    channels: Optional[list[str]] = None
    language: Optional[str] = None
    context: dict = {}


class NotificationDeliveryMetricsOut(BaseModel):
    notifications_total_attempted: int
    notifications_delivered: int
    notifications_failed: int
    notifications_acknowledged: int
    notification_delivery_rate_pct: Optional[float] = None  # None when the denominator is empty: see d47cf58
    notification_ack_rate_pct: Optional[float] = None  # None when the denominator is empty: see d47cf58
    by_channel: dict[str, dict[str, int]] = {}


# ---------------------------------------------------------------------------
# R1-11: principal record access and reporting (D-06, CM-04, D-09, S-03, T-04)
# ---------------------------------------------------------------------------
class PrincipalRecordOut(BaseModel):
    """The "complete consent record" a verified principal can download -
    everything recorded about them for the source_app their context token
    belongs to: consents, the lifecycle history behind them, the evidence
    proving how each grant/withdrawal was collected, and every receipt
    issued to them. See app/services/principal_records.py::build_principal_record."""
    customer: CustomerOut
    source_app: str
    generated_at: datetime
    consents: list[ConsentOut] = []
    history: list[ConsentHistoryOut] = []
    evidence: list[ConsentEvidenceOut] = []
    receipts: list[ConsentReceiptOut] = []


class DecisionPeriodBucket(BaseModel):
    period: str
    ALLOW: int = 0
    DENY: int = 0
    REQUIRE_CONSENT: int = 0
    WITHDRAWN: int = 0
    EXPIRED: int = 0
    total: int = 0


class DecisionReportOut(BaseModel):
    """D-09/S-03/K-12: decision-engine outcome counts per period, backed by
    consent_decision_logs (app/services/decision_engine.py::_finish)."""
    group_by: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    buckets: list[DecisionPeriodBucket] = []
    totals: DecisionPeriodBucket


# ---------------------------------------------------------------------------
# R3-07: processor register and cease-processing propagation (C-02, M-02, M-05, K-08)
# ---------------------------------------------------------------------------
class ProcessorIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    type: str = Field(default="OTHER", max_length=64)
    country: str = Field(default="", max_length=8)
    tenant_code: Optional[str] = Field(default=None, max_length=64)

    contact_name: str = Field(default="", max_length=256)
    contact_email: str = Field(default="", max_length=320)
    contact_phone: str = Field(default="", max_length=64)
    escalation_email: str = Field(default="", max_length=320)

    contract_ref: str = Field(default="", max_length=128)
    contract_signed_on: Optional[date] = None
    contract_valid_from: Optional[date] = None
    contract_valid_until: Optional[date] = None
    security_clause_ref: str = Field(default="", max_length=128)
    security_measures: str = ""
    erasure_clause_ref: str = Field(default="", max_length=128)
    erasure_sla_days: Optional[int] = Field(default=None, ge=0, le=3650)

    webhook_url: str = Field(default="", max_length=512)
    ack_sla_hours: int = Field(default=24, ge=1, le=720)
    notes: str = ""
    is_active: bool = True

    @model_validator(mode="after")
    def _contract_dates_ordered(self) -> "ProcessorIn":
        if (self.contract_valid_from and self.contract_valid_until
                and self.contract_valid_from > self.contract_valid_until):
            raise ValueError("contract_valid_from must not be after contract_valid_until")
        return self


class ProcessorUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    type: Optional[str] = Field(default=None, max_length=64)
    country: Optional[str] = Field(default=None, max_length=8)
    contact_name: Optional[str] = Field(default=None, max_length=256)
    contact_email: Optional[str] = Field(default=None, max_length=320)
    contact_phone: Optional[str] = Field(default=None, max_length=64)
    escalation_email: Optional[str] = Field(default=None, max_length=320)
    contract_ref: Optional[str] = Field(default=None, max_length=128)
    contract_signed_on: Optional[date] = None
    contract_valid_from: Optional[date] = None
    contract_valid_until: Optional[date] = None
    security_clause_ref: Optional[str] = Field(default=None, max_length=128)
    security_measures: Optional[str] = None
    erasure_clause_ref: Optional[str] = Field(default=None, max_length=128)
    erasure_sla_days: Optional[int] = Field(default=None, ge=0, le=3650)
    webhook_url: Optional[str] = Field(default=None, max_length=512)
    ack_sla_hours: Optional[int] = Field(default=None, ge=1, le=720)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class ProcessorOut(BaseModel):
    """Never carries `webhook_secret`. The plaintext is returned exactly once,
    by ProcessorCreateOut/ProcessorSecretOut, at creation and rotation;
    afterwards only `webhook_secret_fingerprint` identifies which secret is in
    force. Adding the secret to this model would leak it on every list call."""
    id: int
    tenant_id: Optional[int] = None
    name: str
    type: str
    country: str
    contact_name: str
    contact_email_masked: Optional[str] = None
    contact_phone_masked: Optional[str] = None
    escalation_email_masked: Optional[str] = None
    contract_ref: str
    contract_signed_on: Optional[date] = None
    contract_valid_from: Optional[date] = None
    contract_valid_until: Optional[date] = None
    contract_in_force: bool
    security_clause_ref: str
    security_measures: str
    erasure_clause_ref: str
    erasure_sla_days: Optional[int] = None
    webhook_url: str
    webhook_configured: bool
    webhook_secret_fingerprint: str
    webhook_secret_set_at: Optional[datetime] = None
    ack_sla_hours: int
    notes: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProcessorCreateOut(ProcessorOut):
    webhook_secret: str = Field(
        description="Shown once, at creation. Store it now; it is never returned again."
    )


class ProcessorSecretOut(BaseModel):
    processor_id: int
    webhook_secret: str
    webhook_secret_fingerprint: str
    webhook_secret_set_at: datetime
    warning: str = "Shown once. Rotating again invalidates this value."


class ProcessorAlertOut(BaseModel):
    id: int
    alert_ref: str
    trigger_ref: str
    tenant_id: Optional[int] = None
    processor_id: int
    processor_name: str = ""
    customer_id: Optional[int] = None
    consent_id: Optional[int] = None
    purpose_id: Optional[int] = None
    alert_type: str
    status: str
    payload_hash: str
    attempts: int
    max_attempts: int
    http_status: Optional[int] = None
    last_error: Optional[str] = None
    ack_sla_hours: int
    due_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: str = ""
    ack_reference: str = ""
    ack_method: str = ""
    escalated_at: Optional[datetime] = None
    within_sla: Optional[bool] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    reason: str = ""


class ProcessorAlertAckIn(BaseModel):
    """Body a processor POSTs to /processors/alerts/{alert_ref}/ack, signed
    with its own webhook secret."""
    acknowledged_by: str = Field(min_length=1, max_length=128)
    reference: str = Field(default="", max_length=128)
    note: str = ""


class ProcessorAlertManualAckIn(BaseModel):
    """Staff-recorded, out-of-band acknowledgement - a processor that
    confirmed by email or phone rather than by calling back. Recorded with
    ack_method=MANUAL so K-08 can be read with or without them."""
    acknowledged_by: str = Field(min_length=1, max_length=128)
    reference: str = Field(default="", max_length=128)
    note: str = ""


class ProcessorContractCoverageRow(BaseModel):
    processor_id: int
    name: str
    type: str
    country: str
    contract_ref: str
    contract_valid_from: Optional[date] = None
    contract_valid_until: Optional[date] = None
    ack_sla_hours: int
    has_contract_ref: bool
    contract_in_force: bool
    has_security_clause: bool
    has_erasure_clause: bool
    reachable_for_instructions: bool
    covered: bool
    gaps: list[str] = []


class ProcessorContractCoverageOut(BaseModel):
    generated_at: datetime
    processors_total: int
    processors_covered: int
    coverage_pct: Optional[float] = None  # None when the denominator is empty: see d47cf58
    processors_with_gaps: int
    contracts_expiring_within_90_days: list[int] = []
    processors: list[ProcessorContractCoverageRow] = []


class PropagationSlaOut(BaseModel):
    """K-08: % of withdrawals acknowledged by every processor holding that
    data within the configured SLA."""
    generated_at: datetime
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    withdrawals_total: int
    withdrawals_with_processor_alerts: int
    withdrawals_without_processors: int
    fully_acknowledged_within_sla: int
    sla_breached: int
    awaiting_acknowledgement_within_sla: int
    k08_propagation_sla_pct: Optional[float] = None  # None when the denominator is empty: see d47cf58
    alerts_total: int
    alerts_acknowledged: int
    alerts_escalated: int
    mean_hours_to_acknowledge: Optional[float] = None
    breached_trigger_refs: list[str] = []


class ProcessorDispatchOut(BaseModel):
    attempted: int
    sent: int
    failed_or_retrying: int
    overdue: int = 0
    escalated: int = 0
    dpo_notifications_queued: int = 0


# ---------------------------------------------------------------------------
# R1-10: retention floors and the regulator evidence pack (S-05, D-08)
# ---------------------------------------------------------------------------
class RetentionScheduleRow(BaseModel):
    record_class: str
    label: str
    storage: str
    table: Optional[str] = None
    floor_days: int
    effective_floor_days: int
    configured_retention_days: int
    is_active: bool
    schedule_meets_floor: bool
    deletable: bool
    undeletable_reason: str = ""
    basis: str = ""
    notes: str = ""
    covers: list[str] = []


class RetentionScheduleUpdateIn(BaseModel):
    retention_days: int = Field(ge=1, le=36500)


class RetentionScanClass(BaseModel):
    record_class: str
    label: str
    storage: str
    table: Optional[str] = None
    floor_days: int
    effective_floor_days: int
    configured_retention_days: int
    schedule_meets_floor: bool
    deletable: bool
    rows_total: Optional[int] = None
    rows_within_floor: Optional[int] = None
    rows_past_floor: Optional[int] = None
    oldest_record_at: Optional[datetime] = None
    newest_record_at: Optional[datetime] = None
    id_sequence_gaps: Optional[int] = None
    retention_actions: int = 0
    rows_deleted_before_floor: int = 0
    enforcement: str = ""
    notes: str = ""


class RetentionScanOut(BaseModel):
    generated_at: datetime
    deleted_before_floor: int
    violations: list[dict] = []
    compliant: bool
    classes_scanned: int
    classes: list[RetentionScanClass] = []


class RetentionEnforceIn(BaseModel):
    record_class: str = Field(min_length=1, max_length=64)
    cutoff: Optional[datetime] = None
    dry_run: bool = True
    reason: str = ""


class RetentionEnforceOut(BaseModel):
    record_class: str
    cutoff: datetime
    floor_days: int
    retention_days: int
    matched: int
    rows_deleted: int
    dry_run: bool
    retention_action_id: int


class EvidencePackOut(BaseModel):
    """The pack is deliberately a loose `sections` dict: its content is
    evidence for a regulator, and pinning every section to a Pydantic shape
    would mean a section could be silently dropped by a model change. The
    top-level fields that a reader must not miss - which sections were
    unavailable or partial - are typed."""
    pack_type: str
    pack_version: str
    generated_at: datetime
    generated_by: str
    period_start: datetime
    period_end: datetime
    tenant: dict
    unavailable_sections: list[str] = []
    partial_sections: list[str] = []
    completeness_note: str
    sections: dict
    integrity_proofs: dict


# R1-09: `PurposeOut.change` is declared as a forward reference above and
# resolved here, at the end of the module, rather than by importing
# app/schemas/reconsent.py at the top. That module is a sibling feature schema
# and importing it early would couple this shared file's import order to it;
# resolving the reference at the bottom keeps the dependency one-directional
# and lets reconsent.py stay independently editable by its own lane.
from app.schemas.reconsent import ChangeClassificationOut  # noqa: E402,F401

PurposeOut.model_rebuild()
PolicyOut.model_rebuild()
