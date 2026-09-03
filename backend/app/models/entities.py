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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    consents: Mapped[list["Consent"]] = relationship(
        "Consent", back_populates="customer", cascade="all, delete-orphan"
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DataCategory(Base):
    __tablename__ = "data_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    requires_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_period_days: Mapped[int] = mapped_column(Integer, default=365)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["PurposeVersion"]] = relationship(
        "PurposeVersion", back_populates="purpose", order_by="PurposeVersion.version_number.desc()"
    )


class PurposeVersion(Base):
    __tablename__ = "purpose_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    legal_basis: Mapped[str] = mapped_column(String(64), default="CONSENT")
    requires_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_period_days: Mapped[int] = mapped_column(Integer, default=365)
    data_category_ids: Mapped[list] = mapped_column(JSON, default=list)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["PolicyVersion"]] = relationship(
        "PolicyVersion", back_populates="policy", order_by="PolicyVersion.version_number.desc()"
    )


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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped["Customer"] = relationship("Customer", back_populates="consents")
    purpose: Mapped["Purpose"] = relationship("Purpose")
    purpose_version: Mapped["PurposeVersion"] = relationship("PurposeVersion")
    data_category: Mapped["DataCategory"] = relationship("DataCategory")
    processing_activity: Mapped["ProcessingActivity"] = relationship("ProcessingActivity")
    policy: Mapped["Policy"] = relationship("Policy")
    policy_version: Mapped["PolicyVersion"] = relationship("PolicyVersion")

    history: Mapped[list["ConsentHistory"]] = relationship(
        "ConsentHistory", back_populates="consent", order_by="ConsentHistory.created_at"
    )
    evidence: Mapped[list["ConsentEvidence"]] = relationship("ConsentEvidence", back_populates="consent")


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped["Consent"] = relationship("Consent", back_populates="evidence")


class ConsentContext(Base):
    __tablename__ = "consent_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
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
