"""R3-08 (I-01, I-02, I-03, I-04, H-12): the personal-data-breach register,
its notifications, and the Board extension-request log.

Kept in its own module rather than in `entities.py` so the breach tables can
be developed without touching the single shared model file; every foreign key
below names its target table as a string, so nothing here imports
`entities.py` and there is no import cycle. `entities.py` carries a single
`from app.models import breach  # noqa: F401` so Alembic's autogenerate (which
imports only `app.models.entities`) sees these tables.

Why the columns are what they are - each one is a mandated content item, not
a convenience field:

* **DPDP Rules 2025 (G.S.R. 846(E), 13 Nov 2025), Rule 7(1)(a)-(e)** - what a
  notice to each affected Data Principal must contain: (a) a description of
  the breach including its nature, extent and the timing of its occurrence;
  (b) the consequences relevant to her likely to arise from the breach;
  (c) the measures implemented and being implemented, if any, to mitigate
  risk; (d) the safety measures she may take to protect her interests;
  (e) business contact information of a person able to respond on the
  fiduciary's behalf.
* **Rule 7(2)(a)** - the Board's initial intimation, "without delay": a
  description of the breach including its nature, extent, timing **and
  location** of occurrence and the likely impact.
* **Rule 7(2)(b)(i)-(vi)** - the detailed report, within **seventy-two hours
  of becoming aware** of the breach "or within such longer period as the
  Board may allow on a request made in writing": (i) updated and detailed
  information in respect of clause (a); (ii) the broad facts related to the
  events, circumstances and reasons leading to the breach; (iii) the measures
  implemented and being implemented to mitigate risk; (iv) the findings
  regarding the person who caused the breach; (v) the remedial measures taken
  to prevent recurrence; (vi) a report regarding the intimations given to
  affected Data Principals.
* **CERT-In Directions No. 20(3)/2022-CERT-In of 28 April 2022 under
  s.70B(6) IT Act** - listed cyber incidents (Annexure I includes "data
  breach" and "data leak") must be reported to CERT-In **within 6 hours of
  noticing** the incident or being brought to notice of it.
* **DPDP Act s.2(u)** - a personal data breach includes *loss of access* to
  personal data, so an availability incident (ransomware, a prolonged outage)
  belongs in this register too; `nature` is free text for that reason.

**`detected_at` and `aware_at` are different columns on purpose.** Rule 7 and
the CERT-In direction both start their clocks from *awareness* ("on becoming
aware", "within 6 hours of noticing"), not from the moment a monitoring
system first emitted an alert. A SIEM alert at 02:00 that the on-call
engineer triages into "this is a personal data breach" at 09:00 gives
`detected_at = 02:00` and `aware_at = 09:00`, and every deadline in
`app/services/breach.py` is computed from the latter. Conflating the two
would either overstate lateness or - far worse - understate a deadline that
has actually already passed.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.encryption import EncryptedString, EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

# detection -> classification -> containment -> notification -> closure
# (the workflow named in DPDP_COMPLIANCE_GAP_ANALYSIS.md item I-01).
BREACH_STATUSES = ["DETECTED", "CLASSIFIED", "CONTAINED", "NOTIFIED", "CLOSED"]

BREACH_TRANSITIONS: dict[str, list[str]] = {
    "DETECTED": ["CLASSIFIED"],
    # Containment and notification are genuinely concurrent in an incident -
    # you do not withhold a principal notice until the hole is plugged - so a
    # classified breach may go to either, and NOTIFIED may still go back to
    # CONTAINED when containment finishes after the notices went out.
    "CLASSIFIED": ["CONTAINED", "NOTIFIED"],
    "CONTAINED": ["NOTIFIED", "CLOSED"],
    "NOTIFIED": ["CONTAINED", "CLOSED"],
    "CLOSED": [],
}

BREACH_SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Who a notification goes to. The workbook's three recipient types.
BREACH_RECIPIENT_TYPES = ["PRINCIPAL", "BOARD", "CERT_IN"]

# Which filing this is for that recipient. PRINCIPAL only ever has NOTICE;
# BOARD has both the R.7(2)(a) initial intimation and the R.7(2)(b) detailed
# report; CERT_IN has the 6-hour initial report.
BREACH_NOTIFICATION_STAGES = ["NOTICE", "INITIAL", "DETAILED"]

# PENDING  - generated and hashed, not yet sent anywhere.
# QUEUED   - handed to app/services/notifications.py's dispatcher.
# SENT     - left this platform (or was filed manually, see filing_reference).
# DELIVERED- the transport reported delivery.
# FAILED   - the dispatcher gave up after its retries.
BREACH_NOTIFICATION_STATUSES = ["PENDING", "QUEUED", "SENT", "DELIVERED", "FAILED"]

BREACH_EXTENSION_STATUSES = ["REQUESTED", "GRANTED", "REFUSED", "WITHDRAWN"]


class Breach(Base):
    """One personal data breach. The narrative columns map one-to-one onto the
    Rule 7 content items so a notice or report can never be assembled from
    fields that do not exist - see the module docstring for the mapping."""

    __tablename__ = "breaches"
    __table_args__ = (
        UniqueConstraint("breach_ref", name="uq_breaches_breach_ref"),
        CheckConstraint(
            "status IN ('DETECTED','CLASSIFIED','CONTAINED','NOTIFIED','CLOSED')",
            name="ck_breaches_status",
        ),
        CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_breaches_severity",
        ),
        # aware_at is the start of all three statutory clocks; it can never
        # precede the moment the incident was detected. Enforced in the
        # database, not only in the service, because a clock that starts too
        # early is a compliance claim this platform would be making falsely.
        CheckConstraint(
            "aware_at IS NULL OR detected_at IS NULL OR aware_at >= detected_at",
            name="ck_breaches_aware_after_detected",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    breach_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DETECTED", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", index=True)

    # --- the three timestamps that drive every clock ---
    # When the breach actually happened, to the best of the fiduciary's
    # knowledge (R.7(1)(a) "timing of its occurrence"). Often unknown at
    # first; nullable for that reason.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When it was first detected - an alert, a report, a customer complaint.
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    # When the fiduciary became AWARE it is a personal data breach. THE clock
    # start for R.7(1), R.7(2)(a), R.7(2)(b) and CERT-In's 6 hours. NULL until
    # triage confirms it, and until then no deadline exists to miss.
    aware_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # --- Rule 7 content items ---
    nature: Mapped[str] = mapped_column(Text, default="")            # R.7(1)(a), R.7(2)(a)
    extent: Mapped[str] = mapped_column(Text, default="")            # R.7(1)(a), R.7(2)(a)
    location: Mapped[str] = mapped_column(Text, default="")          # R.7(2)(a) only
    likely_impact: Mapped[str] = mapped_column(Text, default="")     # R.7(2)(a) only
    likely_consequences: Mapped[str] = mapped_column(Text, default="")  # R.7(1)(b), principal-facing
    mitigation_measures: Mapped[str] = mapped_column(Text, default="")  # R.7(1)(c), R.7(2)(b)(iii)
    safety_measures: Mapped[str] = mapped_column(Text, default="")   # R.7(1)(d) only
    cause: Mapped[str] = mapped_column(Text, default="")             # R.7(2)(b)(ii)
    findings_on_actor: Mapped[str] = mapped_column(Text, default="") # R.7(2)(b)(iv)
    remedial_measures: Mapped[str] = mapped_column(Text, default="") # R.7(2)(b)(v)

    # R.7(1)(e): the person who can answer an affected principal. Defaults are
    # copied from the tenant's Organization DPO contact when the breach is
    # registered, so a notice is never sent with nobody to reply to.
    contact_name: Mapped[str] = mapped_column(String(256), default="")
    contact_email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    contact_phone: Mapped[str] = mapped_column(EncryptedString(256), default="")

    # CERT-In Directions 2022: does this incident fall in Annexure I (a data
    # breach/leak or unauthorised access always does)? Defaults to True; an
    # operator may clear it, and the CERT-In clock then reports
    # NOT_APPLICABLE with that decision on the record rather than silently
    # vanishing.
    cert_in_reportable: Mapped[bool] = mapped_column(Boolean, default=True)
    cert_in_not_reportable_reason: Mapped[str] = mapped_column(Text, default="")

    # Cached scope counters; the authoritative list is breach_affected_principals.
    affected_count: Mapped[int] = mapped_column(Integer, default=0)
    # Set once the operator asserts the affected-principal list is final -
    # until then "every affected principal has been notified" is unknowable
    # and the principal clock reports SCOPING rather than a false ON_TIME.
    scope_finalised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    affected: Mapped[list["BreachAffectedPrincipal"]] = relationship(
        "BreachAffectedPrincipal", back_populates="breach", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["BreachNotification"]] = relationship(
        "BreachNotification", back_populates="breach", cascade="all, delete-orphan"
    )
    extension_requests: Mapped[list["BreachExtensionRequest"]] = relationship(
        "BreachExtensionRequest", back_populates="breach", cascade="all, delete-orphan"
    )


class BreachAffectedPrincipal(Base):
    """The scoping half of R.7(1): who is affected, decided before and
    independently of whether a notice has yet gone out.

    Kept separate from `breach_notifications` because "we have not worked out
    who is affected yet" and "we know who is affected and have not told them"
    are different compliance positions, and a single table cannot express
    both. K-41 (affected principals per breach, notice delivery rate) is the
    join of the two.
    """

    __tablename__ = "breach_affected_principals"
    __table_args__ = (
        UniqueConstraint("breach_id", "customer_id", name="uq_breach_affected_customer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    breach_id: Mapped[int] = mapped_column(ForeignKey("breaches.id"), nullable=False, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    # What of this principal's data was involved - free text, because the
    # answer is incident-specific and Rule 7 requires "the consequences
    # relevant to her", which cannot be a fixed enum.
    data_involved: Mapped[str] = mapped_column(Text, default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    added_by: Mapped[str] = mapped_column(String(64), default="system")

    breach: Mapped["Breach"] = relationship("Breach", back_populates="affected")
    customer = relationship("Customer")


class BreachNotification(Base):
    """One filing: a notice to one principal, or a report to the Board or
    CERT-In.

    `content_hash` exists so a filing can be proved unaltered later. It is a
    SHA-256 over the *canonical JSON* of `payload` - the structured notice, not
    the rendered prose - using exactly the canonicalisation
    `app/core/audit_chain.py::_canonical` uses for the audit ledger
    (`json.dumps(..., sort_keys=True, default=str)` with UTC-normalised
    datetimes), so this platform has one hashing scheme rather than two. See
    `app/services/breach.py::content_hash` / `verify_notification_hash`.

    `deadline_at` is the *statutory* deadline and is NULL where the Rules
    prescribe no fixed period: R.7(1) and R.7(2)(a) both say "without delay",
    which is a standard, not a number. Inventing a number and calling it the
    law would be worse than leaving it null, so the operational number lives
    in `target_at` instead and every report says which of the two it is
    measuring against.
    """

    __tablename__ = "breach_notifications"
    __table_args__ = (
        CheckConstraint(
            "recipient_type IN ('PRINCIPAL','BOARD','CERT_IN')",
            name="ck_breach_notifications_recipient_type",
        ),
        CheckConstraint(
            "stage IN ('NOTICE','INITIAL','DETAILED')",
            name="ck_breach_notifications_stage",
        ),
        CheckConstraint(
            "status IN ('PENDING','QUEUED','SENT','DELIVERED','FAILED')",
            name="ck_breach_notifications_status",
        ),
        # One filing of each kind per principal. Stops a double-notified
        # principal; regenerating updates the row (and its hash) instead of
        # appending a second one.
        UniqueConstraint(
            "breach_id", "recipient_type", "stage", "customer_id",
            name="uq_breach_notification_filing",
        ),
        # ...and the same guarantee for the regulator filings, which the
        # constraint above CANNOT give: their customer_id is NULL, and
        # Postgres treats NULLs as distinct in a unique constraint, so two
        # Board initial intimations for one breach would both be accepted.
        # A partial unique index over the non-NULL columns closes that in the
        # database rather than relying on the service layer remembering to
        # look first.
        Index(
            "uq_breach_notification_regulator_filing",
            "breach_id", "recipient_type", "stage",
            unique=True,
            postgresql_where=text("customer_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    breach_id: Mapped[int] = mapped_column(ForeignKey("breaches.id"), nullable=False, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    recipient_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="NOTICE")
    # Set for PRINCIPAL rows only; NULL on BOARD/CERT_IN rows, which is why
    # the regulator filings need the partial unique index above rather than
    # the plain unique constraint.
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    # The app/services/notifications.py row that actually carries this to the
    # transport, when there is one. NULL for a filing made out of band (the
    # Board and CERT-In have portals, not APIs) - see filing_reference.
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id"), nullable=True)

    recipient: Mapped[str] = mapped_column(EncryptedString(512), default="")
    channel: Mapped[str] = mapped_column(String(16), default="EMAIL")
    language: Mapped[str] = mapped_column(String(8), default="en")

    clock_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The statutory deadline, where one is prescribed (R.7(2)(b): +72h from
    # awareness, extended if the Board allows; CERT-In: +6h from noticing).
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # The internal operational target applied to a "without delay" obligation.
    # Never presented as law.
    target_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_basis: Mapped[str] = mapped_column(String(256), default="")

    subject: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(EncryptedText, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)

    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    # The regulator's own acknowledgement/reference for an out-of-band filing.
    filing_reference: Mapped[str] = mapped_column(String(256), default="")
    generated_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    breach: Mapped["Breach"] = relationship("Breach", back_populates="notifications")


class BreachExtensionRequest(Base):
    """R.7(2)(b): the detailed report is due within 72 hours of awareness "or
    within such longer period as the Board may allow on a request made in
    writing in this behalf". This is that written request, and the Board's
    answer to it.

    A GRANTED request with a `granted_until` moves the 72-hour deadline out;
    nothing else does. A request that is merely REQUESTED does not - an
    unanswered ask is not an extension, and treating it as one would let a
    fiduciary grant itself relief the Board never gave.
    """

    __tablename__ = "breach_extension_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','GRANTED','REFUSED','WITHDRAWN')",
            name="ck_breach_extension_status",
        ),
        CheckConstraint(
            "status <> 'GRANTED' OR granted_until IS NOT NULL",
            name="ck_breach_extension_granted_has_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    breach_id: Mapped[int] = mapped_column(ForeignKey("breaches.id"), nullable=False, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    requested_by: Mapped[str] = mapped_column(String(64), default="system")
    requested_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    written_request_ref: Mapped[str] = mapped_column(String(256), default="")

    status: Mapped[str] = mapped_column(String(16), default="REQUESTED", index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    granted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    board_reference: Mapped[str] = mapped_column(String(256), default="")
    decision_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    breach: Mapped["Breach"] = relationship("Breach", back_populates="extension_requests")
