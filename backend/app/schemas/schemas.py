from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    otp_code: Optional[str] = Field(default=None, description="TOTP MFA code if user has MFA enrolled")


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
    source_app: Optional[str] = "EXTERNAL_APP"
    callback_url: Optional[str] = None


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
    aadhar_number: Optional[str] = None
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
class PurposeIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str = ""
    legal_basis: str = "CONSENT"
    requires_consent: bool = True
    retention_period_days: int = Field(default=365, ge=0)
    data_category_ids: list[int] = []
    processing_activity_ids: list[int] = []
    consent_text: str = ""
    translations: Optional[dict] = None


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
    consent_text: Optional[str] = None
    translations: Optional[dict] = None


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
    consent_text: str
    translations: dict = {}
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
    status: str
    current_version: int
    is_active: bool
    created_at: datetime
    versions: list[PurposeVersionOut] = []
    data_categories: list[DataCategoryOut] = []
    processing_activities: list[ProcessingActivityOut] = []


class PurposeVersionCreate(BaseModel):
    reason: str = ""


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


class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_decision: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None
    rules: Optional[list[PolicyRuleIn]] = None


class PolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    policy_id: int
    version_number: int
    rules: list[Any]
    default_decision: str
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


class PolicyVersionCreate(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------
class ConsentAction(BaseModel):
    reason: str = ""
    collection_method: str = "UI"
    expires_in_days: Optional[int] = Field(default=None, ge=1)
    consent_text: Optional[str] = None


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
    request_id: Optional[str] = None
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


class PortalOverview(BaseModel):
    customer: CustomerOut
    purposes: list[PortalPurposeOut]


class PortalActionIn(BaseModel):
    purpose_code: str = Field(min_length=1, max_length=64)


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
    created_at: datetime
    updated_at: datetime


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
    token_type: str = "bearer"
    user: OrganizationUserOut
    organization: OrganizationOut


class OrganizationDashboardOut(BaseModel):
    organization: OrganizationOut
    users: list[OrganizationUserOut]
    portal_users: list[CustomerOut] = []
    total_customers: int = 0
    total_consents: int = 0
    active_consents: int = 0
    consent_summary: list[dict] = []
    recent_activity: list[AuditEventOut] = []
