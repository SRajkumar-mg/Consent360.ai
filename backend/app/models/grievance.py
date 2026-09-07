"""R2-06 (G-01..G-04): the grievance redressal register.

DPDP Act s.13 gives a Data Principal the right to a readily available means
of grievance redressal, and s.13(2) obliges the fiduciary to respond within
"such period as may be prescribed" - which DPDP Rules 2025 fix at a maximum
the fiduciary publishes for itself. This module is where a complaint gets a
citable identity, a clock, and an escalation path.

Three design points that are load-bearing:

**The response period is not a constant here.** `Organization.grievance_response_days`
(a database CHECK constraint already enforces <= 90) is the single source of
truth; a grievance snapshots it into `response_days` at the moment it is
received and derives `due_at` from that snapshot. The snapshot exists so a
tenant that later shortens or lengthens its published period cannot silently
move the deadline of a complaint already in flight - the period a complainant
was told at submission time is the period they are owed, and it must still be
provable months later from the row itself.

**`reference_no` is what a complainant quotes to the Data Protection Board.**
It is therefore (a) unique, (b) NOT derived from the primary key or any other
sequential counter - a sequential reference leaks the register's size and lets
anyone holding one reference enumerate their neighbours' - and (c) generated
from `secrets`, the OS CSPRNG, never `random`. See
`app/services/grievance.py::generate_reference_no`.

**`description`, `resolution_summary` and `feedback_comment` are encrypted.**
A grievance body is free text written by a data principal about their own
personal data; it is among the most sensitive text this platform stores. The
consequence docs/ARCHITECTURE.md documents applies in full: these columns cannot be
filtered with `=` or `LIKE`, so the admin queue filters on the structured
columns (status, category, due_at, assigned_to, customer) and never on the
narrative. No `*_search` HMAC companion is defined for them because an HMAC
digest only supports whole-value equality, which is meaningless for a
paragraph of prose - the lookup key that DOES need to work,
`reference_no`, is a non-identifying opaque token and is stored in the clear
precisely so it stays indexable.

Defined in its own module (not `entities.py`) so this feature could be built
alongside three other lanes without contending for one file; every foreign
key and relationship target is named by STRING for the same reason - nothing
here imports `entities.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.encryption import EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Vocabulary
# --------------------------------------------------------------------------- #

# What the complaint is about. Kept deliberately close to the rights the Act
# actually confers (s.11 access, s.12 correction/erasure, s.6(4)/(6)
# withdrawal, s.8(4) accuracy) plus the operational categories a redressal
# desk really receives, so the queue can be triaged without free-text
# guessing and so K-* reporting can count "grievances about erasure" without
# reading anyone's narrative.
GRIEVANCE_CATEGORIES = [
    "CONSENT_NOT_HONOURED",     # withdrawal/denial not acted on
    "ACCESS_REQUEST",           # s.11 - summary of processing not provided
    "CORRECTION_REQUEST",       # s.12(1)
    "ERASURE_REQUEST",          # s.12(3)
    "NOMINATION",               # s.14
    "UNAUTHORISED_PROCESSING",  # processing without a lawful basis
    "EXCESSIVE_COLLECTION",     # data minimisation
    "DATA_ACCURACY",            # s.8(4)
    "SECURITY_INCIDENT",        # a suspected breach affecting the principal
    "NOTICE_UNCLEAR",           # s.5 notice quality
    "OTHER",
]

# RECEIVED    - logged, reference issued, nothing sent yet
# ACKNOWLEDGED- the complainant has been told we have it (DoD: on submission)
# IN_PROGRESS - a handler is working it
# ESCALATED   - the published response period elapsed unresolved and the DPO
#               has been notified. A terminal-ish holding state that can still
#               progress to RESOLVED; it is NOT an end state.
# RESOLVED    - a resolution_summary has been recorded and sent
# CLOSED      - the file is closed (after feedback, or after the feedback
#               window lapses)
GRIEVANCE_STATUSES = [
    "RECEIVED",
    "ACKNOWLEDGED",
    "IN_PROGRESS",
    "ESCALATED",
    "RESOLVED",
    "CLOSED",
]

# Legal transitions. Mirrors the CONSENT_TRANSITIONS convention in
# entities.py: the service layer validates against this map and never
# assigns `status` directly.
GRIEVANCE_TRANSITIONS: dict[str, list[str]] = {
    "RECEIVED": ["ACKNOWLEDGED", "IN_PROGRESS", "ESCALATED", "RESOLVED"],
    "ACKNOWLEDGED": ["IN_PROGRESS", "ESCALATED", "RESOLVED"],
    "IN_PROGRESS": ["ESCALATED", "RESOLVED"],
    # An escalated grievance still has to be resolved - escalation notifies
    # the DPO, it does not discharge the obligation.
    "ESCALATED": ["IN_PROGRESS", "RESOLVED"],
    "RESOLVED": ["CLOSED"],
    "CLOSED": [],
}

# A grievance in one of these is off the clock: the escalation job ignores it
# and the "overdue" filter excludes it.
GRIEVANCE_TERMINAL_STATUSES = ("RESOLVED", "CLOSED")

# How the complaint reached us. PORTAL is the data principal's own
# self-service form (context-token authenticated); STAFF is a handler
# logging one that arrived by email, phone or post on the principal's
# behalf, which still has to enter the same register with the same clock.
GRIEVANCE_CHANNELS = ["PORTAL", "STAFF", "EMAIL", "PHONE", "POST", "OTHER"]

# Hard ceiling from DPDP Rules 2025, mirrored from the CHECK constraint
# already on organizations.grievance_response_days. Duplicated here only as
# a defensive clamp for a tenant row that predates that constraint (or a
# tenant auto-provisioned by resolve_tenant_id with the column default);
# it is never a substitute for reading the tenant's own configured value.
MAX_GRIEVANCE_RESPONSE_DAYS = 90
DEFAULT_GRIEVANCE_RESPONSE_DAYS = 90


class Grievance(Base):
    """One complaint, with its reference, its clock and its outcome."""

    __tablename__ = "grievances"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RECEIVED','ACKNOWLEDGED','IN_PROGRESS','ESCALATED','RESOLVED','CLOSED')",
            name="ck_grievances_status",
        ),
        CheckConstraint(
            "category IN ('CONSENT_NOT_HONOURED','ACCESS_REQUEST','CORRECTION_REQUEST',"
            "'ERASURE_REQUEST','NOMINATION','UNAUTHORISED_PROCESSING','EXCESSIVE_COLLECTION',"
            "'DATA_ACCURACY','SECURITY_INCIDENT','NOTICE_UNCLEAR','OTHER')",
            name="ck_grievances_category",
        ),
        CheckConstraint(
            "channel IN ('PORTAL','STAFF','EMAIL','PHONE','POST','OTHER')",
            name="ck_grievances_channel",
        ),
        # The same ceiling organizations.grievance_response_days carries, so a
        # grievance can never be written with a longer clock than the Rules
        # permit even if a tenant row somehow holds one.
        CheckConstraint(
            "response_days > 0 AND response_days <= 90",
            name="ck_grievances_response_days",
        ),
        CheckConstraint(
            "feedback_rating IS NULL OR (feedback_rating >= 1 AND feedback_rating <= 5)",
            name="ck_grievances_feedback_rating",
        ),
        # The admin queue's default view is "this tenant's open grievances,
        # soonest deadline first", and the escalation job's sweep is
        # "unescalated, non-terminal, due_at <= now". Both are served by this.
        Index("ix_grievances_tenant_status_due", "tenant_id", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)

    # Opaque, non-sequential, CSPRNG-generated. Unique and stored in the
    # clear so it stays the one indexable handle on an otherwise encrypted
    # record - see the module docstring.
    reference_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    # Optional pointers to what the complaint is ABOUT. Nullable: plenty of
    # grievances ("your notice is unintelligible") are not about one row.
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consents.id"), nullable=True)
    purpose_id: Mapped[int | None] = mapped_column(ForeignKey("purposes.id"), nullable=True)

    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="PORTAL", nullable=False)
    # A one-line handle written by the complainant. Encrypted for the same
    # reason `description` is: a subject line routinely restates the personal
    # data the complaint is about.
    subject: Mapped[str] = mapped_column(EncryptedText, default="")
    description: Mapped[str] = mapped_column(EncryptedText, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="RECEIVED", nullable=False, index=True)

    # ---- the clock -------------------------------------------------------
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Snapshot of the tenant's published grievance_response_days at receipt.
    response_days: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_GRIEVANCE_RESPONSE_DAYS)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # ---- escalation ------------------------------------------------------
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Where it went. The tenant's DPO contact at the moment of escalation,
    # recorded rather than re-derived so a later change of DPO cannot rewrite
    # who was actually notified. Encrypted: it is a named individual's
    # contact address, exactly like Organization.dpo_email.
    escalated_to: Mapped[str] = mapped_column(EncryptedText, default="")
    escalation_reason: Mapped[str] = mapped_column(String(64), default="")
    escalation_notification_id: Mapped[int | None] = mapped_column(
        ForeignKey("notifications.id"), nullable=True
    )

    # ---- outcome ---------------------------------------------------------
    assigned_to: Mapped[str] = mapped_column(String(128), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_by: Mapped[str] = mapped_column(String(128), default="")
    resolution_summary: Mapped[str] = mapped_column(EncryptedText, default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- complainant feedback -------------------------------------------
    feedback_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_comment: Mapped[str] = mapped_column(EncryptedText, default="")
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # String targets only - this module never imports entities.py, so
    # `Customer` is named rather than referenced and is deliberately left
    # un-annotated (a `Mapped["Customer"]` annotation would need the class
    # itself importable here to resolve, which is exactly the import this
    # module must not have).
    customer = relationship("Customer", lazy="joined")
    events: Mapped[list["GrievanceEvent"]] = relationship(
        "GrievanceEvent",
        back_populates="grievance",
        cascade="all, delete-orphan",
        order_by="GrievanceEvent.created_at",
    )

    # ---- derived helpers -------------------------------------------------

    def is_open(self) -> bool:
        return self.status not in GRIEVANCE_TERMINAL_STATUSES

    def is_overdue(self, now: datetime | None = None) -> bool:
        """Past its published response period and still not resolved.

        `due_at` is read back from Postgres as tz-aware, but a row that has
        only been flushed (not reloaded) still holds whatever the caller set,
        so normalise before comparing rather than trusting either side.
        """
        if not self.is_open():
            return False
        reference = now or utcnow()
        due = self.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due <= reference

    def remaining_days(self, now: datetime | None = None) -> int:
        reference = now or utcnow()
        due = self.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return (due - reference).days


class GrievanceEvent(Base):
    """The per-grievance timeline the admin queue and the complainant's own
    view render.

    This is deliberately NOT a substitute for the audit ledger: every state
    change here is also written to `audit_logs`, which is hash-chained and
    append-only and is the record a regulator would be shown. This table
    exists because that ledger is gated behind `audit.view` and is not
    per-entity queryable, and a complainant must be able to see the progress
    of their own complaint without being granted access to a tenant-wide
    audit trail.
    """

    __tablename__ = "grievance_events"
    __table_args__ = (
        Index("ix_grievance_events_grievance_created", "grievance_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grievance_id: Mapped[int] = mapped_column(
        ForeignKey("grievances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(16), default="")
    to_status: Mapped[str] = mapped_column(String(16), default="")
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    # Free text written by a handler (or by the escalation job). Encrypted:
    # a handler's note about a complaint is as sensitive as the complaint.
    note: Mapped[str] = mapped_column(EncryptedText, default="")
    actor_username: Mapped[str] = mapped_column(String(128), default="system")
    # Whether this entry is safe to show the complainant in their own portal
    # view. Internal triage notes are not.
    visible_to_principal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="events")
