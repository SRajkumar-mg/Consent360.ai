from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict
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

# Valid lifecycle transitions.
#
# This table is the single source of truth: every status-changing function in
# app/services/consent.py (request_consent, grant_consent, deny_consent,
# withdraw_consent, renew_consent) enforces it via _validate_transition, so an
# entry removed here is actually forbidden everywhere, not just on paper.
#
# GRANTED-from-NOT_REQUESTED/REQUESTED, DENIED-from-NOT_REQUESTED, and the
# RENEWED entries added below (self-loop, and from UPDATED/WITHDRAWN) were
# reconciled from grant_consent/deny_consent/renew_consent's own pre-existing
# hand-rolled status lists - i.e. this is what those functions already
# allowed in production before they were routed through this table. In
# particular NOT_REQUESTED -> GRANTED (and -> DENIED) is the
# direct-accept/direct-decline flow (e.g. a cookie banner's
# accept-all/reject-all, or a portal grant with no prior formal REQUESTED
# step) and is intentional, not an oversight.
CONSENT_TRANSITIONS = {
    "NOT_REQUESTED": ["REQUESTED", "GRANTED", "DENIED"],
    "REQUESTED": ["PENDING", "DENIED", "GRANTED"],
    "PENDING": ["GRANTED", "DENIED"],
    "GRANTED": ["ACTIVE", "WITHDRAWN", "RENEWED", "EXPIRED"],
    "ACTIVE": ["WITHDRAWN", "EXPIRED", "RENEWED", "UPDATED"],
    "RENEWED": ["ACTIVE", "WITHDRAWN", "EXPIRED", "RENEWED"],
    "UPDATED": ["ACTIVE", "WITHDRAWN", "EXPIRED", "RENEWED"],
    "DENIED": ["REQUESTED", "GRANTED"],
    "WITHDRAWN": ["GRANTED", "REQUESTED", "RENEWED"],
    "EXPIRED": ["RENEWED", "GRANTED", "REQUESTED"],
    "SUPERSEDED": [],
}

DECISION_OUTCOMES = ["ALLOW", "DENY", "REQUIRE_CONSENT", "EXPIRED", "WITHDRAWN"]

# The complete lawful-gateway taxonomy under the DPDP Act: s.6 consent, or one
# of the nine s.7 "certain legitimate uses" clauses (a)-(i). No other value is
# a lawful gateway - in particular "LEGITIMATE_INTEREST" (a GDPR concept the
# seed data used to write, see B-06) does not exist under DPDP and must never
# be accepted. Every Purpose/PurposeVersion.legal_basis is constrained to this
# set both at the API layer (schemas.PurposeIn/PurposeUpdate) and at the
# database layer (CHECK constraint below), so "no purpose can be saved
# without a valid gateway" holds even for a write that bypasses the API
# (a script, a future migration, direct ORM use in seed.py).
#
# Clause text (DPDP Act 2023 s.7, "Certain legitimate uses"), confirmed
# against https://www.dpdpa.com/dpdpa2023/chapter-2/section7.html and
# https://indiankanoon.org/doc/62814281/ (2026-09-04):
#   S7_A - voluntary provision by the Data Principal for a specified purpose,
#          where she has not indicated she does not consent (the ground C-06
#          objections attach to)
#   S7_B - determining eligibility for a State-provided subsidy/benefit/
#          service/certificate/licence/permit (L-03 State-instrumentality)
#   S7_C - performance by the State/instrumentality of a function under law,
#          or in the interest of sovereignty/integrity/security of India
#   S7_D - fulfilling a legal obligation to disclose information to the State
#   S7_E - compliance with a judgment, decree or order
#   S7_F - responding to a medical emergency threatening life/health
#   S7_G - providing medical treatment during an epidemic/public-health threat
#   S7_H - safety/assistance during a disaster or breakdown of public order
#   S7_I - employment purposes, or safeguarding the employer from loss or
#          liability arising from an employee (L-02 employment template)
LEGAL_BASIS_CONSENT = "CONSENT"
LEGAL_BASIS_S7_VALUES = [f"S7_{letter}" for letter in "ABCDEFGHI"]
LEGAL_BASIS_VALUES = [LEGAL_BASIS_CONSENT] + LEGAL_BASIS_S7_VALUES

LEGAL_BASIS_LABELS = {
    "CONSENT": "Consent (s.6)",
    "S7_A": "s.7(a) voluntary provision, not objected to",
    "S7_B": "s.7(b) State subsidy/benefit/service/licence/permit",
    "S7_C": "s.7(c) State function under law / sovereignty & security",
    "S7_D": "s.7(d) legal obligation to disclose to the State",
    "S7_E": "s.7(e) compliance with judgment/decree/order",
    "S7_F": "s.7(f) medical emergency",
    "S7_G": "s.7(g) medical treatment during epidemic/public-health threat",
    "S7_H": "s.7(h) disaster safety/assistance or breakdown of public order",
    "S7_I": "s.7(i) employment purposes",
}

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
    "NOTICE_CREATED",
    "NOTICE_UPDATED",
    "NOTICE_PUBLISHED",
    "NOTICE_RETIRED",
    "NOTICE_DELETED",
    "RECEIPT_ISSUED",
    "SHARING_EVENT_LOGGED",
    "OBJECTION_RECORDED",
    "OBJECTION_RESOLVED",
    "DECISION_EVALUATED",
    "CONTEXT_CREATED",
    "CONTEXT_CONSUMED",
    "CONTEXT_FIDUCIARY_ASSERTED",
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
    "ORGANIZATION_SETTINGS_UPDATED",
    "API_KEY_CREATED",
    "API_KEY_ROTATED",
    "API_KEY_REVOKED",
    "CUSTOMER_PURGED",
    "AUDIT_EXPORTED",
    "CHATBOT_BLOCKED",
    # R3-06 (O-01/O-02/C-04): notification service lifecycle.
    "NOTIFICATION_QUEUED",
    "NOTIFICATION_DELIVERED",
    "NOTIFICATION_SEND_FAILED",
    "NOTIFICATION_ACKNOWLEDGED",
    # R1-11 (D-06/CM-04): principal self-service record access/export.
    "PRINCIPAL_RECORD_VIEWED",
    "PRINCIPAL_RECORD_EXPORTED",
    # R3-07 (C-02/M-02/M-05): processor register and cease-processing
    # propagation. Note there is no PROCESSOR_WEBHOOK_SECRET_* event carrying
    # the secret itself - rotation is audited by fingerprint only.
    "PROCESSOR_REGISTERED",
    "PROCESSOR_UPDATED",
    "PROCESSOR_WEBHOOK_SECRET_ROTATED",
    "PROCESSOR_ALERT_RAISED",
    "PROCESSOR_ALERT_SENT",
    "PROCESSOR_ALERT_SEND_FAILED",
    "PROCESSOR_ALERT_ACKNOWLEDGED",
    "PROCESSOR_ALERT_ESCALATED",
    # R1-10 (S-05/D-08): retention floors and the regulator evidence pack.
    "RETENTION_SCHEDULE_UPDATED",
    "RETENTION_SCAN_RUN",
    "RETENTION_ACTION_EXECUTED",
    "EVIDENCE_PACK_EXPORTED",
    # R2-06 (G-01..G-04): grievance redressal register. The rows are written
    # either way; listing them here is what lets the audit-log filter
    # dropdown (GET /audit/events) offer them.
    "GRIEVANCE_RECEIVED",
    "GRIEVANCE_ACKNOWLEDGED",
    "GRIEVANCE_UPDATED",
    "GRIEVANCE_ESCALATED",
    "GRIEVANCE_RESOLVED",
    "GRIEVANCE_CLOSED",
    "GRIEVANCE_FEEDBACK_RECORDED",
    # R3-08 (I-01..I-04, H-12): personal data breach register and Rule 7 filings.
    "BREACH_REGISTERED",
    "BREACH_UPDATED",
    "BREACH_AWARENESS_RECORDED",
    "BREACH_STATUS_CHANGED",
    "BREACH_SCOPE_UPDATED",
    "BREACH_SCOPE_FINALISED",
    "BREACH_PRINCIPAL_NOTICES_ISSUED",
    "BREACH_BOARD_NOTIFIED",
    "BREACH_CERT_IN_NOTIFIED",
    "BREACH_FILING_RECORDED",
    "BREACH_EXTENSION_REQUESTED",
    "BREACH_EXTENSION_DECIDED",
    # R3-10 (CM-01, CM-02, J-04): Consent Manager registry, fiduciary
    # onboarding, and the consent-artefact lifecycle. The first four are
    # governance actions by a staff user; the last three are the artefact
    # operations a registered Consent Manager performs on behalf of a
    # principal, and each carries the CM's own ref in `actor_id`.
    "CONSENT_MANAGER_REGISTERED",
    "CONSENT_MANAGER_UPDATED",
    "FIDUCIARY_ONBOARDED",
    "FIDUCIARY_ONBOARDING_UPDATED",
    "ARTEFACT_CREATED",
    "ARTEFACT_UPDATED",
    "ARTEFACT_WITHDRAWN",
    # R1-06 (G-01..G-04, G-06): the retention and erasure engine. Note the
    # split between ERASURE_JOB_* (the lifecycle of the authorisation) and
    # ERASURE_EXECUTED (the irreversible act itself, which carries the
    # evidence hash) - an auditor tracing one erasure reads the second and
    # can then find every decision that led to it under the same job_ref.
    "RETENTION_POLICY_CREATED",
    "RETENTION_POLICY_UPDATED",
    "LEGAL_HOLD_PLACED",
    "LEGAL_HOLD_RELEASED",
    "ERASURE_JOB_PROPOSED",
    "ERASURE_JOB_AUTHORISED",
    "ERASURE_JOB_CANCELLED",
    "ERASURE_JOB_BLOCKED",
    "ERASURE_NOTICE_SENT",
    "ERASURE_EXECUTED",
    # R1-09 (P-01..P-03, K-09): re-consent on material change. POLICY_CHANGE_
    # LOGGED is written for EVERY publication, cosmetic ones included - a log
    # that records only the changes someone judged material cannot be used to
    # check that judgement.
    "POLICY_CHANGE_LOGGED",
    "RE_CONSENT_CAMPAIGN_STARTED",
    "RE_CONSENT_REQUIRED",
    "RE_CONSENT_RECEIVED",
    "RE_CONSENT_CAMPAIGN_CLOSED",
    "COOKIE_POLICY_PUBLISHED",
    "COOKIE_PREFERENCES_INVALIDATED",
    # R2-11 (A-08, K-26): the s.5(2) legacy-notice campaign. CAMPAIGN_STARTED
    # is written even when the cohort turns out to be empty - "we ran this
    # cut-off and found nobody" is an audit answer, and an absent row is not.
    # SENT means queued for delivery, one row per principal; whether it
    # actually arrived is `notifications.status`, not this.
    "LEGACY_NOTICE_CAMPAIGN_STARTED",
    "LEGACY_NOTICE_SENT",
    # R2-11 (Q-07): a server-observed Global Privacy Control objection
    # (Sec-GPC: 1) that actually changed an outcome - the grant or renewal
    # was refused rather than recorded. Written only when GPC bites, so an
    # absence of these rows means no objection was ever overridden; the
    # signal itself is separately durable on consent_evidence.gpc_signal
    # whether or not it changed anything.
    "GPC_OBJECTION_ENFORCED",
]


# ---------------------------------------------------------------------------
# Notifications (R3-06: O-01, O-02, O-04, C-04)
# ---------------------------------------------------------------------------
NOTIFICATION_CHANNELS = ["EMAIL", "SMS", "IN_APP"]

# PENDING (queued, not yet attempted or awaiting retry) -> SENT (handed to
# the channel adapter successfully) -> DELIVERED (this interim service has
# no delivery-webhook/callback integration for any channel, so a successful
# send is treated as delivered in the same step - see
# app/services/notifications.py) -> optionally ACKNOWLEDGED (the recipient
# confirmed it, e.g. via the self-service portal). FAILED is terminal, only
# reached once the dispatch job's retry budget (Notification.max_retries)
# is exhausted.
NOTIFICATION_STATUSES = ["PENDING", "SENT", "DELIVERED", "FAILED", "ACKNOWLEDGED"]

# O-02's named trigger catalogue. Every value here is a real, callable
# app.services.notifications.queue_notification(event_type=...) target;
# CONSENT_ACKNOWLEDGEMENT, WITHDRAWAL_CONFIRMATION, PURPOSE_CHANGE_RECONSENT
# and RENEWAL_REMINDER are wired to their actual trigger points in this
# round (services/consent.py and app/jobs/renewal_reminders_job.py). The
# remaining four have no upstream workflow yet to fire them automatically
# (no breach-management, DSR/grievance-tracking or legacy-notice-campaign
# entity exists in this codebase today - each is its own not-yet-built
# task), so they are reachable only via the staff-triggered
# POST /notifications/trigger endpoint until that workflow task lands and
# calls queue_notification itself, exactly the pattern the Objection entity
# already uses for "recorded now, enforced/automated by a later task".
NOTIFICATION_EVENT_TYPES = [
    "CONSENT_ACKNOWLEDGEMENT",
    "WITHDRAWAL_CONFIRMATION",
    "RENEWAL_REMINDER",
    "PURPOSE_CHANGE_RECONSENT",
    # P-02: the policy-level twin of PURPOSE_CHANGE_RECONSENT - a material
    # change to a Policy's rules or default decision (app/services/
    # material_change.py::publish_policy_change), rather than to a Purpose.
    # Its own event type so a principal's notification history and K-44/K-45
    # can tell the two apart rather than reporting every re-consent prompt
    # as if it were about the purpose's own wording.
    "POLICY_CHANGE_RECONSENT",
    "ERASURE_WARNING_48H",
    "BREACH_NOTICE",
    "REQUEST_STATUS",
    "GRIEVANCE_STATUS",
    "LEGACY_NOTICE",
    # R3-07/C-02: the only event type here whose recipient is NOT a data
    # principal. It escalates an unacknowledged cease-processing/erasure
    # instruction to the tenant's DPO, and is queued through
    # services/notifications.py::queue_operational_notification (which takes
    # an explicit recipient) rather than queue_notification (which resolves
    # one from a Customer). Reusing the same table/dispatcher keeps retries,
    # backoff and K-44/K-45 delivery metrics identical for both audiences.
    "PROCESSOR_ESCALATION",
    # R2-06/G-03: an overdue grievance escalated to the tenant's DPO. Also a
    # DPO-facing event rather than a principal-facing one; without this
    # constant the escalation falls back to a principal-facing template and
    # the DPO receives generic wording.
    "GRIEVANCE_ESCALATION",
]

# The 22 Indian languages + English that frontend/src/translations/*.ts
# already covers (see docs/ARCHITECTURE.md's "23 Indian languages" note) - reused here
# rather than an independently maintained list, so
# notification_templates.language can never drift from what the admin
# console actually supports. Real per-language template COPY is a
# translation/content task (the same kind of one-off generation docs/ARCHITECTURE.md
# notes for the frontend's own translations/_gen*.py), not a schema gap;
# this CHECK constraint is what makes "23 languages" a structural property
# of the table rather than a convention someone can silently violate.
NOTIFICATION_LANGUAGES = [
    "en", "hi", "as", "bn", "brx", "doi", "gu", "kn", "kok", "ks", "mai",
    "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
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
    # Durable token-revocation generation. Bumped on logout and on
    # deactivation; a token minted before the bump carries the older value and
    # is refused. This exists because the current revocation store is
    # in-memory (or Redis, when REDIS_URL is set): without a durable
    # generation counter, "log out -> restart the process -> replay the token"
    # succeeds, and a deployment without Redis has no revocation at all.
    #
    # NOT YET ENFORCED. Minting the claim lives in app/core/security.py and
    # comparing it per request lives in app/api/deps.py, both of which belong
    # to another workstream right now - see the lane report. The column and
    # its migration land here so that side is unblocked; until those two
    # changes are made this value is written by nothing and read by nothing.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    role: Mapped["Role"] = relationship("Role", back_populates="users")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("external_id", name="uq_customers_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    external_id: Mapped[str] = mapped_column(EncryptedString(256), unique=True, nullable=False, index=True)
    external_id_search: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(EncryptedString(512), nullable=False)
    email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    email_search: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    phone: Mapped[str] = mapped_column(EncryptedString(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    anonymised_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # R1-06/G-02: the zero point of the DPDP Rules 2025 R.8(1) + Third
    # Schedule three-year inactivity clock - "the date on which the Data
    # Principal last approached the Data Fiduciary for the performance of the
    # specified purpose, or exercised her rights". Deliberately NOT
    # `updated_at`: that moves whenever this platform touches the row for its
    # own reasons (a scheduled expiry, a backfill), and letting a machine's
    # write reset a principal's erasure clock would push their erasure date
    # three years into the future every time the scheduler ran.
    #
    # Written only by app/services/erasure.py::touch_last_interaction, called
    # from the surfaces where the principal is demonstrably present: minting a
    # consent context (every demo-site login and integration handoff), any
    # authenticated /portal action, and lodging a grievance. Null means "never
    # observed interacting", and the inactivity scan then falls back to
    # `created_at`.
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

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
    # aadhar_number (R1-12/H-11) removed: Aadhaar collection needs a specific
    # lawful basis under the Aadhaar Act that this demo CRM never actually
    # established (the login flow, CrmCustomerLoginIn, never even collected
    # it - only seed.py's demo rows ever set it), so it was pure unnecessary
    # data minimisation risk with no purpose behind it. See the migration
    # that drops the column for the removal itself.
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
    __table_args__ = (
        # Belt-and-braces for B-06/L-01: even a write that bypasses the API's
        # Pydantic validation (a script, a future migration, direct ORM use)
        # cannot persist a purpose without a real DPDP lawful gateway.
        CheckConstraint(
            "legal_basis IN ('CONSENT','S7_A','S7_B','S7_C','S7_D','S7_E','S7_F','S7_G','S7_H','S7_I')",
            name="ck_purposes_legal_basis",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    legal_basis: Mapped[str] = mapped_column(String(64), default="CONSENT")
    requires_consent: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_period_days: Mapped[int] = mapped_column(Integer, default=365)
    # A specific description of the goods/services/uses this purpose enables
    # (A-02) - mirrors the current PurposeVersion's own `services_enabled`.
    services_enabled: Mapped[str] = mapped_column(Text, default="")
    # Whether this purpose's processing is directed at, or knowingly involves,
    # children (s.9) - surfaced so notices/UI can apply the stricter child
    # rules; not itself an enforcement mechanism.
    child_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Forward-compatible reference to a future retention-schedule/policy
    # record (D-03 is a separate, not-yet-built task); deliberately a plain
    # string id rather than a hard FK so this task does not have to invent
    # that entity to satisfy the workbook's "retention_policy_id" field.
    retention_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    __table_args__ = (
        CheckConstraint(
            "legal_basis IN ('CONSENT','S7_A','S7_B','S7_C','S7_D','S7_E','S7_F','S7_G','S7_H','S7_I')",
            name="ck_purpose_versions_legal_basis",
        ),
    )

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
    # Itemised data collected for this purpose (A-01/B-04): one entry per
    # data category actually used, each `{data_category_id, necessity,
    # description}` - `necessity` marks whether that item is strictly needed
    # for the purpose (vs. merely useful), which is what a Notice's itemised
    # list and B-04's minimisation check both read from. Kept on the
    # PurposeVersion only (like data_category_ids/processing_activity_ids),
    # never mirrored to Purpose, so a historical version's itemisation never
    # silently changes under a consent that already pinned it.
    data_items: Mapped[list] = mapped_column(JSON, default=list)
    services_enabled: Mapped[str] = mapped_column(Text, default="")
    child_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_text: Mapped[str] = mapped_column(EncryptedText, default="")
    translations: Mapped[dict] = mapped_column(JSON, default=dict)
    # R2-09/A-09: the plain-language & dark-pattern review record that gates
    # publishing (frontend/src/components/NoticeChecklist.tsx), persisted
    # server-side instead of only in the reviewing browser's localStorage -
    # {"reviewer": str, "completed_at": iso datetime, "items": [str, ...]}.
    # Null until a reviewer actually completes the checklist for this
    # version; see schemas.ReviewChecklistIn for the accepted shape.
    checklist: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    purpose: Mapped["Purpose"] = relationship("Purpose", back_populates="versions")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
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
    # See PurposeVersion.checklist's docstring - identical shape and purpose,
    # scoped to a policy version instead of a purpose version.
    checklist: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    policy: Mapped["Policy"] = relationship("Policy", back_populates="versions")


class Notice(Base):
    """R1-04 (A-01, A-02, A-07, D-04, A-12): the notice a Purpose is presented
    under. One Notice per Purpose (a Purpose's processing is always disclosed
    through exactly one notice document); the actual rendered, evidentiary
    content lives on NoticeVersion, following the same create-then-publish,
    versions-are-immutable-once-current pattern as Purpose/PurposeVersion and
    Policy/PolicyVersion. `current_version` is 0 until the first publish -
    unlike a Purpose, a Notice is not live the moment it is created; it must
    be explicitly published."""
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")  # DRAFT | ACTIVE | RETIRED
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    purpose: Mapped["Purpose"] = relationship("Purpose")
    versions: Mapped[list["NoticeVersion"]] = relationship(
        "NoticeVersion", back_populates="notice", order_by="NoticeVersion.version_number.desc()"
    )


class NoticeVersion(Base):
    """One immutable, evidentiary rendering of a Notice. Everything a
    consent/evidence row needs to prove "the exact notice version and
    language actually shown" (A-07) without depending on any other table
    that could later change is snapshotted here at publish time: `purposes`
    (the covered purpose's own descriptive fields as they stood at publish),
    `links` and `contact_snapshot` (from the tenant Organization), plus this
    version's own itemised data_items/services_enabled/retention. Only
    `translations` is looked up live at render time to pick a language, since
    the *set* of language variants is exactly this version's own content, not
    borrowed from elsewhere."""
    __tablename__ = "notice_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    language_default: Mapped[str] = mapped_column(String(8), default="en")
    title: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    # {lang_code: {"title": ..., "body": ...}} - additional languages beyond
    # `language_default`, mirroring PurposeVersion.translations's shape.
    translations: Mapped[dict] = mapped_column(JSON, default=dict)

    # Itemised data list (A-01): [{data_category_id, necessity, description}],
    # snapshotted from the purpose version's own data_items at publish time.
    data_items: Mapped[list] = mapped_column(JSON, default=list)
    # Snapshot of the covered purpose's own descriptive fields at publish
    # time (a list so a future notice covering >1 purpose needs no schema
    # change): [{code, name, description, legal_basis, requires_consent,
    # retention_period_days}].
    purposes: Mapped[list] = mapped_column(JSON, default=list)
    # Specific description of the goods/services/uses this notice enables (A-02).
    services_enabled: Mapped[str] = mapped_column(Text, default="")
    retention_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_note: Mapped[str] = mapped_column(Text, default="")
    child_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Communication links (A-04): withdraw/rights/grievance/board-complaint,
    # snapshotted from the tenant Organization at publish time.
    links: Mapped[dict] = mapped_column(JSON, default=dict)
    # DPO/contact snapshot (A-05): dpo_name/dpo_email/dpo_phone.
    contact_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    # SHA-256 over the canonical (sorted-keys) JSON of every field above,
    # computed at publish - lets ConsentEvidence.notice_hash be independently
    # re-verified against this row without trusting either side blindly.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # R2-09/A-09: see PurposeVersion.checklist's docstring - identical shape
    # ({"reviewer": str, "completed_at": iso datetime, "items": [str, ...]})
    # and purpose, scoped to a notice version instead. Unlike
    # PurposeVersion/PolicyVersion (where this column is stored but never
    # checked server-side), POST /notices/{id}/publish below actually
    # refuses to publish without one - see that route's docstring for why
    # notices get the real gate purposes/policies still lack, and for the
    # grandfathering decision on notice versions published before this
    # column existed (they keep is_current=True; NULL here is the visible
    # marker that they predate the gate).
    checklist: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")

    notice: Mapped["Notice"] = relationship("Notice", back_populates="versions")


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (UniqueConstraint("customer_id", "purpose_id", "data_category_id",
                                        "processing_activity_id", "consent_version", "source_app",
                                        name="uq_consent_identity_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    purpose_version_id: Mapped[int] = mapped_column(ForeignKey("purpose_versions.id"), nullable=False)
    data_category_id: Mapped[int] = mapped_column(ForeignKey("data_categories.id"), nullable=False)
    processing_activity_id: Mapped[int] = mapped_column(
        ForeignKey("processing_activities.id"), nullable=False
    )
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    policy_version_id: Mapped[int | None] = mapped_column(ForeignKey("policy_versions.id"), nullable=True)
    # The notice version in force at the moment this consent was last
    # granted/renewed (A-07/D-04) - pinned exactly like purpose_version_id
    # and policy_version_id, and always kept equal to the matching
    # ConsentEvidence row's own notice_version_id (see
    # services/consent.py::grant_consent/renew_consent). Nullable because a
    # consent granted before any Notice exists/is published for its purpose
    # has none to pin.
    notice_version_id: Mapped[int | None] = mapped_column(ForeignKey("notice_versions.id"), nullable=True)

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

    # R1-09/P-01: set when a MATERIAL change is published to the purpose (or
    # cookie policy) this consent was given under - see
    # app/services/material_change.py for what "material" means, field by
    # field, and why.
    #
    # This is NOT a UI hint. `services/decision_engine.py::evaluate_decision`
    # reads it and refuses to ALLOW while it is set, even for a consent whose
    # status is GRANTED/ACTIVE/RENEWED/UPDATED: s.6(1) consent is agreement to
    # a *specified* purpose, and a consent that no longer describes what is
    # being done is not consent to it. The flag is cleared only by a real
    # `grant_consent` / `renew_consent` - a fresh, affirmative act by the
    # principal - never by the platform re-pointing the consent at the new
    # version, which is exactly the "consent cannot be assumed" failure
    # (BRD 4.1.3) this closes.
    re_consent_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # When the re-consent was asked for. Deliberately NOT `requested_at`
    # above, which already means something different and load-bearing: the
    # moment `request_consent` moved this consent to REQUESTED for the first
    # time. Two clocks, two columns.
    re_consent_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    re_consent_campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("re_consent_campaigns.id"), nullable=True
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="consents")
    purpose: Mapped["Purpose"] = relationship("Purpose")
    purpose_version: Mapped["PurposeVersion"] = relationship("PurposeVersion")
    data_category: Mapped["DataCategory"] = relationship("DataCategory")
    processing_activity: Mapped["ProcessingActivity"] = relationship("ProcessingActivity")
    policy: Mapped["Policy"] = relationship("Policy")
    policy_version: Mapped["PolicyVersion"] = relationship("PolicyVersion")
    notice_version: Mapped["NoticeVersion"] = relationship("NoticeVersion")

    history: Mapped[list["ConsentHistory"]] = relationship(
        "ConsentHistory", back_populates="consent", order_by="ConsentHistory.created_at"
    )
    # Explicit order_by (matching `history` above): with none, a plain
    # `SELECT ... WHERE consent_id = ?` has no defined row order at all -
    # Postgres is free to return it in any order it likes, and a query
    # issued after enough OTHER writes have happened in the same session
    # (e.g. a notification queued alongside a grant/withdrawal) can flip
    # that incidental order. `consent.evidence[-1]` is used throughout this
    # codebase (services/consent.py, tests) to mean "the most recent
    # evidence row" - ordering by `id` (strictly increasing with insertion,
    # unlike `collected_at`, which two rows created in the same request can
    # tie on) makes that actually true instead of merely usually true.
    evidence: Mapped[list["ConsentEvidence"]] = relationship(
        "ConsentEvidence", back_populates="consent", order_by="ConsentEvidence.id"
    )


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
    # MutableDict, not plain JSON: app.services.consent.grant_consent (and
    # renew_consent/withdraw_consent) mutate an already-flushed row's
    # `details` in place (`consent.history[-1].details["evidence_ref"] =
    # ...`) to backfill the evidence_ref after the ConsentEvidence row is
    # created. A plain JSON-typed column never notices that in-place
    # mutation - SQLAlchemy only detects a *replacement* of the attribute
    # (`x.details = {...}`), not a mutation of the dict object already
    # sitting there - so the write was silently discarded, no UPDATE was
    # ever issued for it, and evidence_ref was never actually persisted.
    details: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped["Consent"] = relationship("Consent", back_populates="history")


class ConsentEvidence(Base):
    __tablename__ = "consent_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
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
    # Repointed from purpose_versions.id to notice_versions.id (R1-04): before
    # the Notice entity existed this stood in for it using the purpose
    # version's own id as a placeholder (see the migration that introduced
    # notices/notice_versions for the data fix this required).
    notice_version_id: Mapped[int | None] = mapped_column(ForeignKey("notice_versions.id"), nullable=True)
    notice_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    ip_address: Mapped[str | None] = mapped_column(EncryptedString(256), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ui_control_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    banner_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    screen_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    affirmative_action: Mapped[str] = mapped_column(String(16), default="CLICK")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Global Privacy Control (Sec-GPC request header), read and stamped
    # server-side by the caller (app/api/routes/portal.py) at the moment
    # this evidence row is written - not the client-claimed
    # ClientContext.gpc_signal (kept separately in `details["claimed_gpc_signal"]`
    # by services/consent.py::_create_evidence, exactly like claimed_ip_address/
    # claimed_user_agent below). Null when the caller sent no Sec-GPC header
    # at all (most staff/API calls); True/False when it did. Persisting this
    # here makes a GPC objection durable server-side evidence instead of a
    # signal that only ever existed inside the browser that sent it.
    gpc_signal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consent: Mapped["Consent"] = relationship("Consent", back_populates="evidence")

    @property
    def signature_valid(self) -> bool | None:
        """Whether `signature` still matches (content_hash, notice_hash).

        `signature` was written but never checked anywhere until now, which
        made it decorative: a direct database edit of either hash - the exact
        tampering this column exists to detect - went unnoticed. Surfaced on
        ConsentEvidenceOut so every staff and principal-facing evidence
        response carries the verdict.

        Three-valued on purpose. None means "unsigned, so there is nothing to
        verify" (a legacy row written before the column was populated);
        conflating that with False would report an old but untouched row as
        tampered, which is a worse error than saying nothing.

        Verification goes through `hmac_signature_matches`, which accepts a
        signature made under any key in the HMAC key ring - see
        app/services/receipts.py::verify_receipt for why a direct comparison
        would raise a false alarm on every row predating a key rotation.
        """
        if not self.signature:
            return None
        from app.core.encryption import hmac_signature_matches

        return hmac_signature_matches(self.signature, self.content_hash, self.notice_hash)


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
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)


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
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_username: Mapped[str] = mapped_column(String(64), default="system", index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="SYSTEM")
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
    ip_address: Mapped[str | None] = mapped_column(EncryptedString(256), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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

    dpo_name: Mapped[str] = mapped_column(String(256), default="")
    dpo_email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    dpo_phone: Mapped[str] = mapped_column(EncryptedString(256), default="")
    withdraw_url: Mapped[str] = mapped_column(String(512), default="")
    rights_url: Mapped[str] = mapped_column(String(512), default="")
    grievance_url: Mapped[str] = mapped_column(String(512), default="")
    board_complaint_url: Mapped[str] = mapped_column(String(512), default="")
    grievance_response_days: Mapped[int] = mapped_column(Integer, default=90)
    default_language: Mapped[str] = mapped_column(String(8), default="en")
    environment: Mapped[str] = mapped_column(String(16), default="DEV")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

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


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KpiSnapshot(Base):
    __tablename__ = "kpi_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Organization"] = relationship("Organization")


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("consent_contexts.id"), nullable=False, index=True)
    email_search: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Shared constant for `Processor.type` so a future admin screen (or any other
# caller) cannot silently disable the chatbot's contract gate with a typo -
# every place that needs to identify "the LLM provider processor row" should
# compare against this constant rather than a hand-typed string literal.
PROCESSOR_TYPE_LLM_PROVIDER = "LLM_PROVIDER"


# R3-07/C-02/M-05: the processor register's own vocabulary. `type` stays a
# free String column (an operator can name a processor category this list
# does not anticipate) but these are the values the register, the contract
# coverage report and the chatbot's contract gate actually reason about.
PROCESSOR_TYPES = [
    "LLM_PROVIDER",
    "CLOUD_HOSTING",
    "ANALYTICS",
    "MARKETING",
    "PAYMENTS",
    "COMMUNICATIONS",
    "SUPPORT",
    "OTHER",
]


class Processor(Base):
    """R3-07/C-02/M-02/M-05: the register of third-party data processors the
    platform discloses personal data to.

    `is_active` and `contract_valid_until` together gate whether the platform
    is permitted to send it any data at all (see routes/chatbot.py's contract
    gate for the LLM-provider case). The R3-07 columns below add the three
    things s.8(2)/s.6(6) actually need from a register:

    * **who to reach** - `contact_*` for the processor's own DPO/contact and
      `escalation_email` for a contractual escalation address, both encrypted
      like every other contact detail in this schema (Customer.email,
      Organization.dpo_email).
    * **what the contract says** - `contract_signed_on`/`contract_valid_from`/
      `contract_valid_until` plus `security_clause_ref`/`security_measures`
      and `erasure_clause_ref`/`erasure_sla_days`, which is what
      services/processors.py::contract_coverage_report scores.
    * **how to instruct it** - `webhook_url` and `webhook_secret`, the
      transport for a cease-processing or erasure instruction, and
      `ack_sla_hours`, the configured SLA clock K-08 is measured against.

    `webhook_secret` is stored with `EncryptedString` (AES-256-GCM), NOT
    hashed like `ApiKey.key_hash`. That difference is deliberate and is the
    one place this codebase cannot reuse the api_keys pattern: an inbound API
    key only ever has to be *verified*, so a one-way hash suffices, whereas an
    outbound webhook secret has to be *used* to compute an HMAC over every
    payload we send - a hash of it would be useless for that. Reversible
    encryption at rest is therefore the strongest available option, and the
    secret is compensated for elsewhere: it is never returned by any endpoint
    after the single creation/rotation response, never logged, and
    `webhook_secret_fingerprint` (SHA-256 of the plaintext) exists so an admin
    UI, an audit row or a support conversation can identify *which* secret is
    in force without ever disclosing it.
    """

    __tablename__ = "processors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = a platform-wide processor shared by every tenant (the seeded LLM
    # provider is one); a tenant_id scopes the row to one fiduciary.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(64), default="")
    country: Mapped[str] = mapped_column(String(8), default="")

    contact_name: Mapped[str] = mapped_column(String(256), default="")
    contact_email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    contact_phone: Mapped[str] = mapped_column(EncryptedString(256), default="")
    escalation_email: Mapped[str] = mapped_column(EncryptedString(512), default="")

    contract_ref: Mapped[str] = mapped_column(String(128), default="")
    contract_signed_on: Mapped[Date | None] = mapped_column(Date, nullable=True)
    contract_valid_from: Mapped[Date | None] = mapped_column(Date, nullable=True)
    contract_valid_until: Mapped[Date | None] = mapped_column(Date, nullable=True)
    security_clause_ref: Mapped[str] = mapped_column(String(128), default="")
    security_measures: Mapped[str] = mapped_column(Text, default="")
    erasure_clause_ref: Mapped[str] = mapped_column(String(128), default="")
    erasure_sla_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    webhook_url: Mapped[str] = mapped_column(String(512), default="")
    webhook_secret: Mapped[str] = mapped_column(EncryptedString(512), default="")
    webhook_secret_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    webhook_secret_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The configured N in K-08 ("% withdrawals acknowledged by all processors
    # within N hours"). Per processor rather than global, because the SLA is a
    # contractual term negotiated with that processor, not a platform setting.
    ack_sla_hours: Mapped[int] = mapped_column(Integer, default=24)

    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Transfer(Base):
    """A cross-border transfer register entry: one row per processor x
    destination country the platform actually sends data to."""

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processor_id: Mapped[int] = mapped_column(ForeignKey("processors.id"), nullable=False, index=True)
    destination_country: Mapped[str] = mapped_column(String(8), nullable=False)
    data_categories: Mapped[list] = mapped_column(JSON, default=list)
    lawful_basis: Mapped[str] = mapped_column(String(64), default="")
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    processor: Mapped["Processor"] = relationship("Processor")


# Sharing-event types (D-05/M-02), matching the MeitY Electronic Consent
# Framework's DATA-REQUESTED / DATA-SENT / DATA-DENIED log events and the
# workbook's own wording.
SHARING_EVENT_TYPES = ["REQUESTED", "SENT", "DENIED"]


class ConsentReceipt(Base):
    """R1-08/B-09: a signed, hash-linked consent artefact issued to the
    principal on every grant/renew (see services/receipts.py::issue_receipt,
    called from services/consent.py). `payload` is a self-contained
    ISO/IEC TS 27560-shaped snapshot - it does not merely reference the
    consent/evidence/notice rows, it restates their content at issuance time,
    so the receipt still proves what it claims even if those rows are later
    superseded by a new version. `payload_hash`/`signature` let the principal
    (or an auditor) detect any tampering with the stored payload after the
    fact, the same pattern already used for ConsentEvidence.content_hash/
    signature."""
    __tablename__ = "consent_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    receipt_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), nullable=False, index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("consent_evidence.id"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    source_app: Mapped[str] = mapped_column(String(128), default="")
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # GRANTED | RENEWED
    consent_version: Mapped[int] = mapped_column(Integer, default=1)
    notice_version_id: Mapped[int | None] = mapped_column(ForeignKey("notice_versions.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    consent: Mapped["Consent"] = relationship("Consent")
    evidence: Mapped["ConsentEvidence"] = relationship("ConsentEvidence")


class DataSharingEvent(Base):
    """R1-08/D-05/M-02: a record of one disclosure (or attempted disclosure)
    of personal data to a transferee fiduciary/processor. `signed` is an
    HMAC over the event's own fields (see services that write this table),
    the same tamper-evidence pattern as ConsentEvidence/ConsentReceipt."""
    __tablename__ = "data_sharing_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('REQUESTED','SENT','DENIED')", name="ck_data_sharing_events_event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    processor_id: Mapped[int] = mapped_column(ForeignKey("processors.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    data_category_ids: Mapped[list] = mapped_column(JSON, default=list)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # REQUESTED | SENT | DENIED
    legal_basis: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    processor: Mapped["Processor"] = relationship("Processor")
    purpose: Mapped["Purpose"] = relationship("Purpose")
    consent: Mapped["Consent"] = relationship("Consent")


class Objection(Base):
    """R1-08/C-06: a data principal's objection to processing carried out
    under the s.7(a) "voluntary provision, not objected to" gateway. Once
    lodged, the purpose no longer satisfies s.7(a) for that principal - s.7(a)
    is defined by the *absence* of an objection - though enforcing that in
    the decision engine is left to a follow-up task; this register is the
    system of record an enforcement point would read."""
    __tablename__ = "objections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    reason: Mapped[str] = mapped_column(EncryptedText, default="")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE | RESOLVED
    source_app: Mapped[str] = mapped_column(String(128), default="")
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    objected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")

    purpose: Mapped["Purpose"] = relationship("Purpose")
    consent: Mapped["Consent"] = relationship("Consent")


class NotificationTemplate(Base):
    """R3-06/O-01: one template per (tenant, event_type, channel, language).

    `tenant_id` is nullable so a platform-wide default template
    (tenant_id=None) can be seeded once per event_type/channel/language and
    a tenant can optionally override it with its own row - see
    app/services/notifications.py::_select_template for the lookup order
    (tenant-specific -> platform default -> language "en" fallback,
    mirroring PurposeVersion.translations's own per-language fallback).
    `body_template` is a plain ``str.format``-style template
    (``{placeholder}``); see that same module for the placeholder set each
    event_type fills in.
    """
    __tablename__ = "notification_templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "event_type", "channel", "language",
            name="uq_notification_templates_identity",
        ),
        CheckConstraint(
            "channel IN ('EMAIL','SMS','IN_APP')", name="ck_notification_templates_channel"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    subject: Mapped[str] = mapped_column(String(512), default="")
    body_template: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")


class Notification(Base):
    """R3-06/O-01/O-02/C-04: one row per notification attempt/delivery.

    `recipient` is the destination address/number resolved at queue time
    (encrypted like every other contact-detail column - Customer.email/
    phone - see `recipient_search` for the HMAC companion needed for
    equality lookups). `retry_count`/`next_attempt_at`/`max_retries` back
    the dispatch job's retry loop (app/jobs/notification_dispatch_job.py ->
    app/services/notifications.py::dispatch_pending); `provider_ref` is
    whatever identifier the channel adapter's send() call returned. K-44
    (delivery rate) and K-45 (acknowledgement rate) are computed straight
    off `status`/`sent_at`/`delivered_at`/`acknowledged_at` - see
    app/services/kpi.py::notification_delivery_metrics.
    """
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('EMAIL','SMS','IN_APP')", name="ck_notifications_channel"),
        CheckConstraint(
            "status IN ('PENDING','SENT','DELIVERED','FAILED','ACKNOWLEDGED')",
            name="ck_notifications_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("notification_templates.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en")
    recipient: Mapped[str] = mapped_column(EncryptedString(512), default="")
    recipient_search: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(EncryptedText, default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_app: Mapped[str] = mapped_column(String(128), default="")
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    customer: Mapped["Customer"] = relationship("Customer")
    template: Mapped["NotificationTemplate"] = relationship("NotificationTemplate")


# ---------------------------------------------------------------------------
# R3-07: processor alerts (C-02 cease-processing propagation, K-08)
# ---------------------------------------------------------------------------
# CEASE_PROCESSING     - s.6(6): a principal withdrew consent, so every
#                        processor holding data under that purpose must stop.
# ERASURE_INSTRUCTION  - s.8(7): the erasure engine (R1-06, NOT BUILT YET)
#                        instructs a processor to erase. This platform raises
#                        and tracks the instruction; it does not itself erase
#                        anything in a processor's systems - see
#                        services/processors.py::raise_erasure_instructions.
# SHARING_EVENT        - M-02: a disclosure was logged against this processor
#                        and it is being told what it received, so the two
#                        sides' records can be reconciled.
PROCESSOR_ALERT_TYPES = ["CEASE_PROCESSING", "ERASURE_INSTRUCTION", "SHARING_EVENT"]

# PENDING   - queued, not yet attempted (or awaiting a retry).
# SENT      - the processor's webhook accepted the POST (2xx). NOT compliance:
#             s.6(6) needs the processor to confirm it acted, which is
#             ACKNOWLEDGED.
# ACKNOWLEDGED - the processor confirmed, either by signing an ack callback or
#             by a staff member recording an out-of-band confirmation.
# FAILED    - delivery retries exhausted.
# ESCALATED - the ack SLA elapsed with no acknowledgement, so the tenant's DPO
#             was notified. `escalated_at` is kept independently of `status`
#             so a late acknowledgement can move the row on to ACKNOWLEDGED
#             without erasing the fact that it had to be escalated.
PROCESSOR_ALERT_STATUSES = ["PENDING", "SENT", "ACKNOWLEDGED", "FAILED", "ESCALATED"]


class ProcessorAlert(Base):
    """One outbound instruction to one processor, with its delivery attempts,
    its acknowledgement and its SLA clock.

    `trigger_ref` groups every alert produced by a single upstream event (one
    withdrawal fans out to N processors). K-08 is computed over trigger_refs,
    not over rows: the DoD is "acknowledged by *every* processor holding that
    data within the SLA", so a withdrawal only counts as met when all of its
    alerts are acknowledged in time.

    `signature` is the HMAC-SHA256 this platform sent in the
    `X-Consent360-Signature` header, computed with the processor's own webhook
    secret over `"{timestamp}.{body}"` - stored so an ack dispute can be
    settled against exactly what was signed, never re-derived from a secret
    that may since have been rotated.
    """

    __tablename__ = "processor_alerts"
    __table_args__ = (
        CheckConstraint(
            "alert_type IN ('CEASE_PROCESSING','ERASURE_INSTRUCTION','SHARING_EVENT')",
            name="ck_processor_alerts_alert_type",
        ),
        CheckConstraint(
            "status IN ('PENDING','SENT','ACKNOWLEDGED','FAILED','ESCALATED')",
            name="ck_processor_alerts_status",
        ),
        CheckConstraint("ack_sla_hours > 0", name="ck_processor_alerts_sla_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    alert_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    trigger_ref: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    processor_id: Mapped[int] = mapped_column(ForeignKey("processors.id"), nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    purpose_id: Mapped[int | None] = mapped_column(ForeignKey("purposes.id"), nullable=True)
    sharing_event_id: Mapped[int | None] = mapped_column(ForeignKey("data_sharing_events.id"), nullable=True)

    alert_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    signed_timestamp: Mapped[int | None] = mapped_column(Integer, nullable=True)

    webhook_url: Mapped[str] = mapped_column(String(512), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ack_sla_hours: Mapped[int] = mapped_column(Integer, default=24)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str] = mapped_column(String(128), default="")
    ack_reference: Mapped[str] = mapped_column(String(128), default="")
    ack_method: Mapped[str] = mapped_column(String(16), default="")  # WEBHOOK | MANUAL
    ack_note: Mapped[str] = mapped_column(Text, default="")
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_app: Mapped[str] = mapped_column(String(128), default="")
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    processor: Mapped["Processor"] = relationship("Processor")


# ---------------------------------------------------------------------------
# R1-10: retention floors and retention actions (S-05, D-08)
# ---------------------------------------------------------------------------
# DELETE     - rows were removed permanently.
# ANONYMISE  - identifying fields were overwritten, the row itself kept (what
#              the customer purge path already does - see routes/crm.py).
# ARCHIVE    - rows moved to cold storage outside this database.
RETENTION_ACTION_TYPES = ["DELETE", "ANONYMISE", "ARCHIVE"]


class RetentionSchedule(Base):
    """The operator-settable retention period for one record class.

    The *floor* for each class is a statutory minimum and lives in code
    (app/services/retention.py::RETENTION_CLASSES) - it is not editable
    through this table, because a row an operator can edit downward is not a
    floor. This table only carries the period the operator has actually
    chosen, which the retention service refuses to accept below the floor.
    Raising it (retaining longer) is always allowed.
    """

    __tablename__ = "retention_schedules"
    __table_args__ = (
        CheckConstraint("retention_days > 0", name="ck_retention_schedules_days_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_class: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    basis: Mapped[str] = mapped_column(String(512), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class RetentionAction(Base):
    """A ledger row for every retention-driven deletion/anonymisation this
    platform performs - including dry runs, which record what *would* have
    been removed.

    This table is what makes "zero records deleted before their floor" a
    checkable statement rather than an assertion: every sanctioned deletion
    goes through services/retention.py::enforce_retention, which refuses a
    cutoff inside the floor and writes one of these rows, and
    services/retention.py::retention_scan re-checks every recorded action's
    cutoff against the floor that was in force. `floor_days_at_execution`
    freezes the floor the action was checked against, so a later change to
    the constant cannot retroactively make a compliant action look like a
    violation (or hide a real one).
    """

    __tablename__ = "retention_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('DELETE','ANONYMISE','ARCHIVE')", name="ck_retention_actions_action"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    record_class: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    floor_days_at_execution: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_days_at_execution: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_affected: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---------------------------------------------------------------------------
# Auto-fill HMAC search-digest columns on write.
#
# Encrypted columns (EncryptedString/EncryptedText) hold plaintext at the
# Python/ORM level right up until SQLAlchemy's TypeDecorator encrypts them on
# flush, so it is safe to read `target.email` / `target.external_id` here and
# compute the deterministic digest that companion `*_search` columns need for
# equality lookups. Without this, every insert path that forgets to set the
# search column by hand (seed.py did) leaves it NULL/stale and lookups 404.
# ---------------------------------------------------------------------------
from sqlalchemy import event as _event  # noqa: E402


def _fill_search_columns(mapper, connection, target) -> None:
    if hasattr(target, "email") and hasattr(target, "email_search") and target.email:
        target.email_search = hmac_digest(target.email)
    if hasattr(target, "external_id") and hasattr(target, "external_id_search") and target.external_id:
        target.external_id_search = hmac_digest(target.external_id)
    if hasattr(target, "recipient") and hasattr(target, "recipient_search") and target.recipient:
        target.recipient_search = hmac_digest(target.recipient)


for _cls in (User, Customer, CrmCustomer, OrganizationUser, Notification):
    _event.listen(_cls, "before_insert", _fill_search_columns)
    _event.listen(_cls, "before_update", _fill_search_columns)


# ---------------------------------------------------------------------------
# Sibling model modules.
#
# These live in their own files so several features could be built in
# parallel without contending for this one, but Alembic's autogenerate only
# sees a table whose class was imported by the time `Base.metadata` is read -
# and alembic/env.py imports this module, not those. Importing them HERE, at
# the end, is what registers them.
#
# The position is deliberate: after every class above is defined, so a
# relationship in one of those modules that names a table defined here
# resolves. They avoid an import cycle by referring to this module's tables
# only through string-based ForeignKey targets, never by importing it.
# ---------------------------------------------------------------------------
from app.models import artefacts  # noqa: F401,E402  (R3-10 Consent Manager artefact tables)
from app.models import breach  # noqa: F401,E402  (R3-08: breach register tables)
from app.models import erasure  # noqa: F401,E402  (R1-06 retention policies, legal holds, erasure jobs)
from app.models import grievance  # noqa: F401,E402  (R2-06 grievance register)
from app.models import reconsent  # noqa: F401,E402  (R1-09 policy change log, re-consent campaigns, cookie policy versions)
from app.models import rights  # noqa: F401,E402  (R2-05 rights requests, request events, nominations)
