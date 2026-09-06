from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.encryption import EncryptedJSON, EncryptedString, EncryptedText, hmac_digest


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations (kept as strings for configurable + traceable values)
# ---------------------------------------------------------------------------

CONSENT_STATUSES = [
    "NOT_REQUESTED",
    "REQUESTED",
    "PENDING",
    "GRANTED",
    "ACTIVE",
    "DENIED",
    "WITHDRAWN",
    "EXPIRED",
    "RENEWED",
    "UPDATED",
    "SUPERSEDED",
]

# Valid lifecycle transitions
CONSENT_TRANSITIONS = {
    "NOT_REQUESTED": ["REQUESTED"],
    "REQUESTED": ["PENDING", "DENIED"],
    "PENDING": ["GRANTED", "DENIED"],
    "GRANTED": ["ACTIVE", "WITHDRAWN", "RENEWED", "EXPIRED"],
    "ACTIVE": ["WITHDRAWN", "EXPIRED", "RENEWED", "UPDATED"],
    "RENEWED": ["ACTIVE", "WITHDRAWN", "EXPIRED"],
    "UPDATED": ["ACTIVE", "WITHDRAWN", "EXPIRED"],
    "DENIED": ["REQUESTED", "GRANTED"],
    "WITHDRAWN": ["GRANTED", "REQUESTED"],
    "EXPIRED": ["RENEWED", "GRANTED", "REQUESTED"],
    "SUPERSEDED": [],
}

DECISION_OUTCOMES = ["ALLOW", "DENY", "REQUIRE_CONSENT", "EXPIRED", "WITHDRAWN"]

AUDIT_EVENTS = [
    "CONSENT_CREATED",
    "CONSENT_GRANTED",
    "CONSENT_DENIED",
    "CONSENT_WITHDRAWN",
    "CONSENT_RENEWED",
    "CONSENT_EXPIRED",
    "CONSENT_UPDATED",
    "CONSENT_REQUESTED",
    "CONSENT_VIEWED",
    "PURPOSE_CREATED",
    "PURPOSE_UPDATED",
    "DATA_CATEGORY_CREATED",
    "DATA_CATEGORY_UPDATED",
    "ACTIVITY_CREATED",
    "ACTIVITY_UPDATED",
    "POLICY_CREATED",
    "POLICY_UPDATED",
    "POLICY_VERSIONED",
    "POLICY_EVALUATED",
    "DECISION_EVALUATED",
    "CONTEXT_CREATED",
    "CONTEXT_CONSUMED",
    "CUSTOMER_CREATED",
    "CUSTOMER_UPDATED",
    "USER_CREATED",
    "USER_UPDATED",
    "ROLE_CHANGED",
    "LOGIN",
    "LOGOUT",
    "LOGIN_FAILED",
    "CONSENT_TEXT_VIEWED",
    "CONSENT_EVIDENCE_VIEWED",
    "API_KEY_CREATED",
    "API_KEY_ROTATED",
    "API_KEY_REVOKED",
    "TENANT_SCOPE_VIOLATION",
    "VERIFY_OTP_SENT",
    "VERIFY_OTP_CONFIRMED",
    "VERIFY_OTP_FAILED",
    "BREACH_CREATED",
    "BREACH_NOTIFICATION_SENT",
    "PROCESSOR_ALERT_SENT",
    "PROCESSOR_ALERT_ESCALATED",
    "ACCESS_LOG",
    "BULK_EXPORT",
    "OFF_HOURS_ADMIN",
    "FAILED_LOGIN_REPEATED",
]


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list["User"]] = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(EncryptedString(512), unique=True, nullable=False, index=True)
    email_search: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # R3-02: Auth hardening
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_recovery_codes: Mapped[list["MFARecoveryCode"]] = relationship(
        "MFARecoveryCode", cascade="all, delete-orphan"
    )

    role: Mapped["Role"] = relationship("Role", back_populates="users")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("external_id", name="uq_customers_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(EncryptedString(256), unique=True, nullable=False, index=True)
    external_id_search: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(EncryptedString(512), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    email_search: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    phone: Mapped[str] = mapped_column(EncryptedString(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # R3-05: Identity verification
    identity_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)

    consents: Mapped[list["Consent"]] = relationship(
        "Consent", back_populates="customer", cascade="all, delete-orphan"
    )
    tenant: Mapped["Tenant | None"] = relationship("Tenant")

    data_sharing_events: Mapped[list["DataSharingEvent"]] = relationship(
        "DataSharingEvent", order_by="DataSharingEvent.created_at", cascade="all, delete-orphan"
    )


class CrmCustomer(Base):
    """Customer records managed by the CRM portal (local user directory).

    Separate from the consent platform's Customer references; a CRM login
    creates/updates a record here and a matching consent context is minted
    for the linked Customer reference on demand.
    """

    __tablename__ = "crm_customers"
    __table_args__ = (UniqueConstraint("email", name="uq_crm_customers_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(EncryptedString(512), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString(512), unique=True, nullable=False, index=True)
    email_search: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aadhar_number: Mapped[str | None] = mapped_column(EncryptedString(256), nullable=True)
    address: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    phone: Mapped[str | None] = mapped_column(EncryptedString(256), nullable=True)
    consent_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tenant: Mapped["Tenant | None"] = relationship("Tenant")


class DataCategory(Base):
    __tablename__ = "data_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    data_residency: Mapped[str] = mapped_column(String(32), default="INDIAN", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProcessingActivity(Base):
    __tablename__ = "processing_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Purpose(Base):
    __tablename__ = "purposes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    legal_basis: Mapped[str] = mapped_column(String(64), default="CONSENT")
    lawful_basis: Mapped[str] = mapped_column(String(32), default="CONSENT")
    requires_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_period_days: Mapped[int] = mapped_column(Integer, default=365)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["PurposeVersion"]] = relationship(
        "PurposeVersion", back_populates="purpose", order_by="PurposeVersion.version_number.desc()"
    )
    tenant: Mapped["Tenant | None"] = relationship("Tenant")


class PurposeVersion(Base):
    __tablename__ = "purpose_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    legal_basis: Mapped[str] = mapped_column(String(64), default="CONSENT")
    lawful_basis: Mapped[str] = mapped_column(String(32), default="CONSENT")
    clause_reference: Mapped[str] = mapped_column(Text, default="")
    requires_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_period_days: Mapped[int] = mapped_column(Integer, default=365)
    data_category_ids: Mapped[list] = mapped_column(JSON, default=list)
    data_items: Mapped[dict] = mapped_column(JSON, default=list)
    services_enabled: Mapped[str] = mapped_column(Text, default="")
    child_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_activity_ids: Mapped[list] = mapped_column(JSON, default=list)
    consent_text: Mapped[str] = mapped_column(EncryptedText, default="")
    translations: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    purpose: Mapped["Purpose"] = relationship("Purpose", back_populates="versions")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["PolicyVersion"]] = relationship(
        "PolicyVersion", back_populates="policy", order_by="PolicyVersion.version_number.desc()"
    )
    tenant: Mapped["Tenant | None"] = relationship("Tenant")


class PolicyVersion(Base):
    __tablename__ = "policy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    default_decision: Mapped[str] = mapped_column(String(32), default="REQUIRE_CONSENT")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    policy: Mapped["Policy"] = relationship("Policy", back_populates="versions")


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("customer_id", "purpose_id", "data_category_id",
                                        "processing_activity_id", "consent_version", "source_app",
                                        name="uq_consent_identity_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    purpose_version_id: Mapped[int] = mapped_column(ForeignKey("purpose_versions.id"), nullable=False)
    data_category_id: Mapped[int] = mapped_column(ForeignKey("data_categories.id"), nullable=False)
    processing_activity_id: Mapped[int] = mapped_column(
        ForeignKey("processing_activities.id"), nullable=False
    )
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    policy_version_id: Mapped[int | None] = mapped_column(ForeignKey("policy_versions.id"), nullable=True)
    notice_version_id: Mapped[int | None] = mapped_column(ForeignKey("notice_versions.id"), nullable=True)
    notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id"), nullable=True)
    terms_document_id: Mapped[int | None] = mapped_column(ForeignKey("legal_documents.id"), nullable=True)
    privacy_document_id: Mapped[int | None] = mapped_column(ForeignKey("legal_documents.id"), nullable=True)
    terms_hash: Mapped[str] = mapped_column(String(64), default="")
    privacy_hash: Mapped[str] = mapped_column(String(64), default="")

    consent_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="NOT_REQUESTED", index=True)

    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    consent_text: Mapped[str] = mapped_column(EncryptedText, default="")
    collection_method: Mapped[str] = mapped_column(String(64), default="UI")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)

    re_consent_required: Mapped[bool] = mapped_column(Boolean, default=False)
    re_consent_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship("Customer", back_populates="consents")
    purpose: Mapped["Purpose"] = relationship("Purpose")
    purpose_version: Mapped["PurposeVersion"] = relationship("PurposeVersion")
    data_category: Mapped["DataCategory"] = relationship("DataCategory")
    processing_activity: Mapped["ProcessingActivity"] = relationship("ProcessingActivity")
    policy: Mapped["Policy"] = relationship("Policy")
    policy_version: Mapped["PolicyVersion"] = relationship("PolicyVersion")
    notice_version: Mapped["NoticeVersion | None"] = relationship("NoticeVersion")
    tenant: Mapped["Tenant | None"] = relationship("Tenant")

    history: Mapped[list["ConsentHistory"]] = relationship(
        "ConsentHistory", back_populates="consent", order_by="ConsentHistory.created_at"
    )
    evidence: Mapped[list["ConsentEvidence"]] = relationship("ConsentEvidence", back_populates="consent")
    receipts: Mapped[list["ConsentReceipt"]] = relationship("ConsentReceipt", back_populates="consent")


class ConsentHistory(Base):
    __tablename__ = "consent_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(EncryptedText, default="")
    consent_version: Mapped[int] = mapped_column(Integer, default=1)
    policy_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped["Consent"] = relationship("Consent", back_populates="history")


class ConsentEvidence(Base):
    __tablename__ = "consent_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), nullable=False, index=True)
    evidence_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    collected_by: Mapped[str] = mapped_column(String(64), default="system")
    collection_method: Mapped[str] = mapped_column(String(64), default="UI")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    consent_text: Mapped[str] = mapped_column(EncryptedText, default="")
    consent_version: Mapped[int] = mapped_column(Integer, default=1)
    purpose_version: Mapped[int] = mapped_column(Integer, default=1)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    notice_version_id: Mapped[int | None] = mapped_column(ForeignKey("notice_versions.id"), nullable=True)
    notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id"), nullable=True)
    notice_hash: Mapped[str] = mapped_column(String(64), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    ip_address: Mapped[str] = mapped_column(EncryptedString(256), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    session_id: Mapped[str] = mapped_column(String(64), default="")
    ui_control_id: Mapped[str] = mapped_column(String(128), default="")
    banner_version: Mapped[str] = mapped_column(String(64), default="")
    screen_id: Mapped[str] = mapped_column(String(128), default="")
    affirmative_action: Mapped[str] = mapped_column(String(32), default="CLICK")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    terms_document_id: Mapped[int | None] = mapped_column(ForeignKey("legal_documents.id"), nullable=True)
    privacy_document_id: Mapped[int | None] = mapped_column(ForeignKey("legal_documents.id"), nullable=True)
    terms_hash: Mapped[str] = mapped_column(String(64), default="")
    privacy_hash: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped["Consent"] = relationship("Consent", back_populates="evidence")
    notice_version: Mapped["NoticeVersion | None"] = relationship("NoticeVersion")


class ConsentContext(Base):
    __tablename__ = "consent_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="integration")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConsentDecisionLog(Base):
    __tablename__ = "consent_decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purpose_id: Mapped[int | None] = mapped_column(ForeignKey("purposes.id"), nullable=True)
    data_category_id: Mapped[int | None] = mapped_column(ForeignKey("data_categories.id"), nullable=True)
    processing_activity_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_activities.id"), nullable=True
    )
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    consent_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64), default="system")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_username: Mapped[str] = mapped_column(String(64), default="system", index=True)
    actor_role: Mapped[str] = mapped_column(String(64), default="")
    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    customer_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    purpose_id: Mapped[int | None] = mapped_column(ForeignKey("purposes.id"), nullable=True)
    purpose_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    policy_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(EncryptedText, default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    entry_hash: Mapped[str] = mapped_column(String(64), default="")
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="USER")
    actor_id: Mapped[str] = mapped_column(String(128), default="")
    ip_address: Mapped[str] = mapped_column(EncryptedString(256), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    logo_url: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    users: Mapped[list["OrganizationUser"]] = relationship("OrganizationUser", back_populates="organization")


class OrganizationUser(Base):
    __tablename__ = "organization_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString, default="")
    email_search: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="org_viewer")  # org_admin or org_viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped["Organization"] = relationship("Organization", back_populates="users")


# ============================================================================
# R1-01 — Tenant model  /  R3-01: Tenant-bound API keys
# ============================================================================

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    domain: Mapped[str] = mapped_column(String(256), default="")
    dpo_name: Mapped[str] = mapped_column(String(256), default="")
    dpo_contact: Mapped[str] = mapped_column(EncryptedText, default="")
    withdraw_url: Mapped[str] = mapped_column(String(512), default="")
    rights_url: Mapped[str] = mapped_column(String(512), default="")
    grievance_url: Mapped[str] = mapped_column(String(512), default="")
    board_complaint_url: Mapped[str] = mapped_column(String(512), default="")
    grievance_response_days: Mapped[int] = mapped_column(Integer, default=90)
    default_language: Mapped[str] = mapped_column(String(8), default="en")
    environment: Mapped[str] = mapped_column(String(32), default="development")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    api_keys: Mapped[list["ApiKey"]] = relationship("ApiKey", back_populates="tenant")
    notification_templates: Mapped[list["NotificationTemplate"]] = relationship("NotificationTemplate", back_populates="tenant")
    processors: Mapped[list["Processor"]] = relationship("Processor", back_populates="tenant")
    legal_documents: Mapped[list["LegalDocument"]] = relationship("LegalDocument", back_populates="tenant")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), default="default")
    scopes: Mapped[str] = mapped_column(String(512), default="integration.use,context.use")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="api_keys")


# ============================================================================
# RBAC extension — tenant-aware role assignments + verified guardian links.
#
# UserTenantRole lets one `User` hold different roles across different
# tenants (tenant_id set) and/or a platform-global role (tenant_id NULL, for
# platform_* roles). This is additive alongside `User.role_id`, which
# remains the user's default/primary role for backward compatibility with
# existing single-role code paths (require_permission via user.role).
# ============================================================================

class UserTenantRole(Base):
    __tablename__ = "user_tenant_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", "role_id", name="uq_user_tenant_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # NULL tenant_id = platform-global scope (platform_* roles only).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    # Phase 2: time-boxed access (e.g. platform_auditor engagement window).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    tenant: Mapped["Tenant | None"] = relationship("Tenant", foreign_keys=[tenant_id])
    role: Mapped["Role"] = relationship("Role", foreign_keys=[role_id])


class GuardianChildLink(Base):
    """Verified guardian -> child relationship, separate from role assignment.

    A `guardian` role alone never grants access to a child's data — only a
    row here with status == "VERIFIED" does. This is the DPDP "verifiable
    guardian consent" control point.
    """
    __tablename__ = "guardian_child_links"
    __table_args__ = (
        UniqueConstraint("guardian_user_id", "child_customer_id", name="uq_guardian_child"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guardian_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    child_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    verification_method: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING | VERIFIED | REVOKED
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    guardian: Mapped["User"] = relationship("User", foreign_keys=[guardian_user_id])
    child: Mapped["Customer"] = relationship("Customer", foreign_keys=[child_customer_id])


# ============================================================================
# R3-02: Staff auth hardening
# ============================================================================

class TokenRevocation(Base):
    __tablename__ = "token_revocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    jti: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MFASecret(Base):
    __tablename__ = "mfa_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(String(256), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MFARecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship("User", back_populates="mfa_recovery_codes")


# ============================================================================
# R3-05: Principal identity verification
# ============================================================================

class VerificationToken(Base):
    __tablename__ = "verification_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identifier: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    verification_method: Mapped[str] = mapped_column(String(32), nullable=False)
    is_consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================================
# R1-04 — Notice / NoticeVersion models
# ============================================================================

LEGAL_DOCUMENT_TYPES = ["TERMS_AND_CONDITIONS", "PRIVACY_POLICY"]
LEGAL_DOCUMENT_STATUSES = ["DRAFT", "PUBLISHED", "RETIRED"]

LEGAL_DOC_TYPE_ENUM = "legal_document_type"

AFFIRMATIVE_ACTIONS = [
    "ACCEPT", "REJECT", "WITHDRAW", "CONFIRM", "OTP_VERIFIED",
    "CALL_CONFIRMED", "DOCUMENT_SIGNED", "CLICK", "CHECKBOX",
    "SUBMIT", "TOPIC_SELECT", "VERBAL", "ELECTRONIC_SIGNATURE",
]

AFFIRMATIVE_ACTION_ENUM = "affirmative_action_enum"

NOTICE_STATUSES = ["DRAFT", "PUBLISHED", "RETIRED"]
NOTICE_STATUS_ENUM = "notice_status_enum"

SUPPORTED_LANGUAGES = [
    "en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "or",
    "pa", "as", "ur", "sa", "mai", "ks", "doi", "kok", "ne", "sd",
    "sat", "brx", "mni",
]


class LegalDocument(Base):
    __tablename__ = "legal_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="en", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    tenant: Mapped["Tenant"] = relationship("Tenant")


# ============================================================================
# R1-04 — Consolidated Notice model (notice + notice_version merged)
# ============================================================================


class Notice(Base):
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)

    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en", index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    data_items: Mapped[dict] = mapped_column(JSON, default=list)
    services_enabled: Mapped[str] = mapped_column(Text, default="")
    retention_text: Mapped[str] = mapped_column(Text, default="")
    consent_text: Mapped[str] = mapped_column(Text, default="")

    withdraw_url: Mapped[str] = mapped_column(String(512), default="")
    rights_url: Mapped[str] = mapped_column(String(512), default="")
    grievance_url: Mapped[str] = mapped_column(String(512), default="")
    board_complaint_url: Mapped[str] = mapped_column(String(512), default="")
    dpo_name: Mapped[str] = mapped_column(String(256), default="")
    dpo_contact: Mapped[str] = mapped_column(Text, default="")

    terms_document_id: Mapped[int | None] = mapped_column(ForeignKey("legal_documents.id"), nullable=True)
    privacy_document_id: Mapped[int | None] = mapped_column(ForeignKey("legal_documents.id"), nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    tenant: Mapped["Tenant"] = relationship("Tenant")
    purpose: Mapped["Purpose"] = relationship("Purpose")
    terms_document: Mapped["LegalDocument | None"] = relationship(
        "LegalDocument", foreign_keys=[terms_document_id]
    )
    privacy_document: Mapped["LegalDocument | None"] = relationship(
        "LegalDocument", foreign_keys=[privacy_document_id]
    )


class NoticeVersion(Base):
    """Legacy table — kept for FK compatibility. Data migrated to notices."""
    __tablename__ = "notice_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    data_items: Mapped[dict] = mapped_column(JSON, default=list)
    services_enabled: Mapped[str] = mapped_column(Text, default="")
    retention_text: Mapped[str] = mapped_column(Text, default="")
    withdraw_url: Mapped[str] = mapped_column(String(512), default="")
    rights_url: Mapped[str] = mapped_column(String(512), default="")
    board_complaint_url: Mapped[str] = mapped_column(String(512), default="")
    dpo_contact: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    notice: Mapped["Notice"] = relationship("Notice")


# ============================================================================
# R1-08 — Consent receipts, data-sharing events, objections
# ============================================================================

class ConsentReceipt(Base):
    __tablename__ = "consent_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), nullable=False, index=True)
    consent_evidence_id: Mapped[int] = mapped_column(ForeignKey("consent_evidence.id"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False)
    notice_version_id: Mapped[int | None] = mapped_column(ForeignKey("notice_versions.id"), nullable=True)
    notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id"), nullable=True)
    receipt_number: Mapped[str] = mapped_column(String(64), unique=True, default="")
    data_items_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    method: Mapped[str] = mapped_column(String(64), default="PORTAL")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped["Consent"] = relationship("Consent", back_populates="receipts")


SHARING_EVENT_TYPES = ["REQUESTED", "SENT", "DENIED"]


# ============================================================================
# R1-06 — Retention / erasure engine
# ============================================================================

class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    record_class: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), default="all")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    inactivity_days: Mapped[int] = mapped_column(Integer, nullable=True)
    pre_erasure_notice_hours: Mapped[int] = mapped_column(Integer, default=48)
    action: Mapped[str] = mapped_column(String(32), default="ANONYMISE")
    legal_basis_for_retention: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LegalHold(Base):
    __tablename__ = "legal_holds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    placed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


ERASURE_TRIGGERS = ["WITHDRAWAL", "REQUEST", "CLOCK", "PURGE"]
ERASURE_STATUSES = ["PENDING", "NOTICE_SENT", "EXECUTED", "CANCELLED"]


class ErasureJob(Base):
    __tablename__ = "erasure_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processors_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processors_acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    evidence_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ============================================================================
# R1-07 — Scheduler runs
# ============================================================================

class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ============================================================================
# R1-09 — Policy change log
# ============================================================================

class PolicyChangeLog(Base):
    __tablename__ = "policy_change_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by: Mapped[str] = mapped_column(String(64), nullable=False)
    is_material: Mapped[bool] = mapped_column(Boolean, default=True)
    is_cosmetic_change: Mapped[bool] = mapped_column(Boolean, default=False)
    diff_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# R1-10 — Retention floors config
# ============================================================================

class RetentionFloor(Base):
    __tablename__ = "retention_floors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_class: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    minimum_days: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")


# ============================================================================
# R3-06: Notification service
# ============================================================================

class NotificationTemplate(Base):
    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en")
    subject: Mapped[str] = mapped_column(String(256), default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="notification_templates")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en")
    recipient: Mapped[str] = mapped_column(String(512), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(64), default="")
    reference_id: Mapped[str] = mapped_column(String(64), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ============================================================================
# R3-07: Processor register and propagation
# ============================================================================

class Processor(Base):
    __tablename__ = "processors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(64), default="PROCESSOR")
    country: Mapped[str] = mapped_column(String(64), default="IN")
    contact: Mapped[str] = mapped_column(String(256), default="")
    contract_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contract_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    security_clauses: Mapped[str] = mapped_column(Text, default="")
    erasure_clause: Mapped[str] = mapped_column(Text, default="")
    webhook_url: Mapped[str] = mapped_column(String(512), default="")
    webhook_secret: Mapped[str] = mapped_column(String(256), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="processors")
    alerts: Mapped[list["ProcessorAlert"]] = relationship("ProcessorAlert", back_populates="processor")
    data_sharing_events: Mapped[list["DataSharingEvent"]] = relationship(
        "DataSharingEvent", order_by="DataSharingEvent.created_at", cascade="all, delete-orphan"
    )


class ProcessorAlert(Base):
    __tablename__ = "processor_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processor_id: Mapped[int] = mapped_column(ForeignKey("processors.id"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(64), default="")
    reference_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    processor: Mapped["Processor"] = relationship("Processor", back_populates="alerts")


class DataSharingEvent(Base):
    """Merged: R1's customer/purpose/hash-chain event log combined with
    R3's processor-register-linked propagation tracking."""

    __tablename__ = "data_sharing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    processor_id: Mapped[int | None] = mapped_column(ForeignKey("processors.id"), nullable=True, index=True)
    purpose_id: Mapped[int | None] = mapped_column(ForeignKey("purposes.id"), nullable=True)
    data_category_ids: Mapped[dict] = mapped_column(JSON, default=list)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    processor: Mapped["Processor | None"] = relationship("Processor", back_populates="data_sharing_events")
    customer: Mapped["Customer | None"] = relationship("Customer", back_populates="data_sharing_events")


OBJECTION_STATUSES = ["OPEN", "ACKNOWLEDGED"]


class Objection(Base):
    __tablename__ = "objections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="portal")
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    objected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# R3-08: Breach management
# ============================================================================

class Breach(Base):
    __tablename__ = "breaches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    reference_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    breach_type: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    aware_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    nature: Mapped[str] = mapped_column(Text, default="")
    extent: Mapped[str] = mapped_column(Text, default="")
    timing: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    likely_impact: Mapped[str] = mapped_column(Text, default="")
    cause: Mapped[str] = mapped_column(Text, default="")
    mitigation: Mapped[str] = mapped_column(Text, default="")
    remedial_measures: Mapped[str] = mapped_column(Text, default="")
    findings_on_actor: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="DETECTED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    notifications: Mapped[list["BreachNotification"]] = relationship("BreachNotification", back_populates="breach")
    extension_requests: Mapped[list["BreachExtensionRequest"]] = relationship("BreachExtensionRequest", back_populates="breach")


class BreachNotification(Base):
    __tablename__ = "breach_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    breach_id: Mapped[int] = mapped_column(ForeignKey("breaches.id"), nullable=False, index=True)
    recipient_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default="EMAIL")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    breach: Mapped["Breach"] = relationship("Breach", back_populates="notifications")


class BreachExtensionRequest(Base):
    __tablename__ = "breach_extension_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    breach_id: Mapped[int] = mapped_column(ForeignKey("breaches.id"), nullable=False, index=True)
    clock_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(Text, default="")
    new_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    breach: Mapped["Breach"] = relationship("Breach", back_populates="extension_requests")


class BreachAffectedCustomer(Base):
    __tablename__ = "breach_affected_customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    breach_id: Mapped[int] = mapped_column(ForeignKey("breaches.id"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# R3-10: Consent validation API & Consent Manager API
# ============================================================================

class ConsentArtefact(Base):
    __tablename__ = "consent_artefacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artefact_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purpose_code: Mapped[str] = mapped_column(String(64), nullable=False)
    data_category_code: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_activity_code: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), default="")
    purpose: Mapped[str] = mapped_column(String(256), default="")
    method: Mapped[str] = mapped_column(String(64), default="EXPLICIT")
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocable: Mapped[bool] = mapped_column(Boolean, default=True)
    data_life: Mapped[str] = mapped_column(String(64), default="SINGLE_USE")
    frequency: Mapped[str] = mapped_column(String(64), default="ONE_TIME")
    access_mode: Mapped[str] = mapped_column(String(32), default="PUSH")
    notification_url: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    payload_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    signature: Mapped[str] = mapped_column(String(512), nullable=False)
    source_app: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FiduciaryOnboarding(Base):
    __tablename__ = "fiduciary_onboarding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fiduciary_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    contact: Mapped[str] = mapped_column(String(256), default="")
    public_key: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Disclosure(Base):
    __tablename__ = "disclosures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ============================================================================
# R3-12: SDF readiness - Algorithm register, DPIA/audit records, data residency
# ============================================================================

class AlgorithmRegister(Base):
    __tablename__ = "algorithm_register"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    system_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(32), default="MEDIUM")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(128), default="")
    findings_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DPIARecord(Base):
    __tablename__ = "dpia_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    scope: Mapped[str] = mapped_column(Text, default="")
    findings_summary: Mapped[str] = mapped_column(Text, default="")
    next_review_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DataResidencyFlag(Base):
    __tablename__ = "data_residency_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_category_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    requires_localization: Mapped[bool] = mapped_column(Boolean, default=False)
    country: Mapped[str] = mapped_column(String(64), default="IN")
    regulation_ref: Mapped[str] = mapped_column(String(128), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# R3-04: Access logging extensions
# ============================================================================

class AccessLog(Base):
    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(String(64), default="")
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, default=10)
    window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_contacts: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
