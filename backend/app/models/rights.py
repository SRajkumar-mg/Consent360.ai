"""R2-05 (E-01, E-02, E-03, E-05, E-07, E-09): the data-principal rights
request register and the s.14 nomination register.

DPDP Act ss.11-14 give a Data Principal four rights this platform has to be
able to *receive*, *work* and *prove it worked*: access to a summary of her
personal data and of every recipient it was shared with (s.11), correction /
completion / updating (s.12(1)-(2)), erasure (s.12(3)), and the nomination of
another individual to exercise those rights on her death or incapacity
(s.14). This module is the register that gives each of those a citable
identity, a clock and an outcome.

It is deliberately built to the same shape as `app/models/grievance.py`
(R2-06): opaque CSPRNG reference number, snapshotted response period,
received/acknowledged/due/closed timestamps, a per-request event timeline
alongside the hash-chained audit ledger, and a queue the admin console can
sort by deadline. Two registers that behave differently for the same class of
obligation is a training problem and an audit problem; the two behave alike.

Four design points are load-bearing here and are the reason this file is
worth reading before changing it.

**Identity verification is a database constraint, not a convention.**
R.14(2) requires a Data Fiduciary to verify the identity of the person making
a request, and s.15(b),(e) put a duty on the principal not to impersonate.
An ACCESS request fulfilled for the wrong person hands one human being
another human being's entire consent record, their email, their phone number
and the list of everyone their data was disclosed to - it is the single
highest-consequence failure in this module. So the register refuses to
represent it: `ck_rights_requests_fulfilled_is_verified` makes a row with
`status='FULFILLED'` and `identity_verified=false` unwritable, and
`ck_rights_requests_verified_has_evidence` makes `identity_verified=true`
without a method and a timestamp unwritable. The two together mean the
strongest claim an attacker can force through the service layer is still
bounded by what the database will store.

There is exactly one way identity is established, and no self-assertion is
part of it: the principal must hold a **verified consent context**
(`consent_contexts.verified_at`), which today is set only by
`app/services/otp.py::confirm_otp` (email OTP to the address on file) or by
`app/services/context.py`'s fiduciary assertion from a scoped API key.
A staff-logged request that arrived by phone or email starts UNVERIFIED and
stays that way until an OTP that only the principal can read has been
confirmed - a handler cannot mark a request verified by asserting that they
recognised the caller's voice.

**Closure carries evidence.** `ck_rights_requests_closed_has_evidence`
mirrors `erasure_jobs`'s `ck_erasure_jobs_executed_has_evidence`: a request
cannot reach CLOSED without `closed_at` and `closure_hash`. The hash is
SHA-256 over the canonical JSON of the request's own outcome (see
`app/services/rights_requests.py::compute_closure_hash`) and the same value
is written into the immutable, hash-chained `audit_logs` row for
RIGHTS_REQUEST_CLOSED - so a later edit to this table is detectable by
recomputing.

**An ERASURE request never erases anything itself.** It hands off to the
R1-06 engine (`app/services/erasure.py::approve_rights_request_erasure`) and
records the resulting `erasure_jobs.id` in `erasure_job_id`. Every guarantee
that engine carries - the R.8(2) forty-eight-hour notice, the retention
floors that outrank the principal's own request, the named authoriser, the
evidence hash, the legal-hold block - therefore applies unchanged. A second
deletion path in this module would be a second set of those guarantees to
keep in step, and the first time they diverged the divergence would be an
irreversible destruction of someone's data.

**The narrative is encrypted; the handles are not.** `request_detail`,
`resolution_summary`, `rejection_basis` and the correction payloads are free
text about one identified person and are AES-GCM encrypted, with the same
consequence docs/ARCHITECTURE.md documents: they cannot be filtered with `=` or `LIKE`,
so the queue filters only on structured columns. `reference_no` is a
non-identifying opaque token and is stored in the clear precisely so it stays
indexable.

Defined in its own module (not `entities.py`) so this could be built
alongside other lanes; every foreign key and relationship target is named by
STRING for the same reason - nothing here imports `entities.py`.
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
from app.core.encryption import EncryptedJSON, EncryptedString, EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Vocabulary
# --------------------------------------------------------------------------- #

# The four rights this register works, named for the section that confers
# them. Deliberately NOT a superset: withdrawal (s.6(4)) is a consent
# operation with its own lifecycle in services/consent.py, and an objection
# (s.7(a)) has its own register - folding either in here would give two
# modules an opinion about the same act.
RIGHTS_REQUEST_TYPES = [
    "ACCESS",       # s.11(1)(a)-(b)
    "CORRECTION",   # s.12(1)-(2) - correction, completion and updating
    "ERASURE",      # s.12(3)
    "NOMINATION",   # s.14
]

# RECEIVED     - logged, reference issued, nothing sent yet.
# ACKNOWLEDGED - the principal has been told we have it. Reached in the same
#                unit of work as receipt (see create_request), so the
#                acknowledgement clock cannot be missed by forgetting a step.
# VERIFYING    - R.14(2): we are establishing that the requester is who they
#                say they are. A staff-logged request sits here; a portal
#                request arrives already past it.
# IN_PROGRESS  - identity established, a handler is working it.
# FULFILLED    - the right has been given effect: the access package was
#                delivered, the correction applied, the erasure job executed,
#                the nomination verified.
# REJECTED     - lawfully refused, with a recorded basis. s.12(3) itself
#                carves out retention "necessary for the specified purpose or
#                for compliance with any law", and a legal hold or an
#                unestablished identity are the other two real reasons.
# CLOSED       - the file is closed and carries its closure evidence.
RIGHTS_REQUEST_STATUSES = [
    "RECEIVED",
    "ACKNOWLEDGED",
    "VERIFYING",
    "IN_PROGRESS",
    "FULFILLED",
    "REJECTED",
    "CLOSED",
]

# Legal transitions. Same convention as CONSENT_TRANSITIONS in entities.py and
# GRIEVANCE_TRANSITIONS in grievance.py: the service layer validates against
# this map and never assigns `status` directly.
RIGHTS_REQUEST_TRANSITIONS: dict[str, list[str]] = {
    "RECEIVED": ["ACKNOWLEDGED", "VERIFYING", "IN_PROGRESS", "REJECTED"],
    "ACKNOWLEDGED": ["VERIFYING", "IN_PROGRESS", "REJECTED"],
    "VERIFYING": ["IN_PROGRESS", "REJECTED"],
    "IN_PROGRESS": ["FULFILLED", "REJECTED"],
    # Both outcomes still have to be closed, and closing is what writes the
    # evidence hash - so neither is an end state on its own.
    "FULFILLED": ["CLOSED"],
    "REJECTED": ["CLOSED"],
    "CLOSED": [],
}

# Off the clock: excluded from the "open" and "overdue" queue filters.
RIGHTS_REQUEST_TERMINAL_STATUSES = ("FULFILLED", "REJECTED", "CLOSED")

# How the request reached us. Mirrors GRIEVANCE_CHANNELS so one queue's
# channel vocabulary is the other's.
RIGHTS_REQUEST_CHANNELS = ["PORTAL", "STAFF", "EMAIL", "PHONE", "POST", "OTHER"]

# R.14(2) identity verification. Every value here names a mechanism that
# already exists in this platform and that the *principal*, not a handler,
# has to pass:
#
#   EMAIL_OTP          - app/services/otp.py: a six-digit CSPRNG code sent to
#                        the address on file and read back. Set on the context
#                        by confirm_otp; this register copies it, it never
#                        sets it itself.
#   FIDUCIARY_ASSERTED - app/services/context.py: the tenant authenticated the
#                        principal in its own application before handing off,
#                        using an API key explicitly scoped for it.
#
# There is deliberately no "STAFF_ATTESTED" value. A handler saying "I am
# satisfied this is her" is exactly the self-assertion R.14(2) exists to
# prevent, and a vocabulary that cannot express it cannot record it.
IDENTITY_VERIFICATION_METHODS = ["EMAIL_OTP", "FIDUCIARY_ASSERTED"]

# The `customers` columns a s.12(1) correction may change. A closed list, and
# deliberately a short one:
#
#   `external_id` is the tenant's own key for this person - every consent,
#       evidence, receipt and audit row is keyed to it, so "correcting" it
#       would orphan the record rather than fix it;
#   `source_app` is the tenant binding every scoping check in this platform
#       depends on, so writing it would move a principal between tenants;
#   `status` and `anonymised_ref` are outcomes of the erasure engine, not
#       facts about the person.
#
# s.12(1) is a right to have inaccurate or misleading personal data corrected,
# completed and updated - "my surname is spelled wrong" - not a right to
# re-point the record. Defined here, with the other vocabularies, so the
# schema layer and the service layer cannot drift apart about it.
CORRECTABLE_CUSTOMER_FIELDS = ("name", "email", "phone")

# Why a request was refused. A free-text basis accompanies each; the code is
# what K-22 and the queue can count without reading anyone's narrative.
RIGHTS_REJECTION_REASONS = [
    "IDENTITY_NOT_ESTABLISHED",   # R.14(2) - we could not verify the requester
    "RETENTION_REQUIRED_BY_LAW",  # s.12(3)/s.8(7) carve-out
    "LEGAL_HOLD",                 # a preservation obligation outranks it
    "PURPOSE_STILL_SERVED",       # s.8(7) - the specified purpose is not spent
    "NOT_A_DATA_PRINCIPAL",       # no personal data of this person is held
    "DUPLICATE",                  # already answered under another reference
    "WITHDRAWN_BY_PRINCIPAL",
    "OTHER",
]

# --------------------------------------------------------------------------- #
#  Periods
# --------------------------------------------------------------------------- #
# The response period is NOT hardcoded here. It is read per tenant by
# app/services/rights_requests.py::response_days_for_tenant, from
# `organizations.settings['rights_response_days']` if the tenant has
# configured one and otherwise from `organizations.grievance_response_days`
# (which already carries a database CHECK of <= 90). The fallback is a
# deliberate, stated choice rather than a silent reuse: R.14(3) fixes a
# published maximum for the *grievance* response, and a fiduciary that
# publishes "we answer you within N days" would be publishing two different
# promises if its rights desk quietly ran on a different clock. A tenant that
# genuinely wants a separate, shorter rights clock sets the settings key.
#
# The two constants below are the defensive clamp only - the same role
# MAX_GRIEVANCE_RESPONSE_DAYS plays in grievance.py - and are never a second
# source of truth for the period itself.
MAX_RIGHTS_RESPONSE_DAYS = 90
DEFAULT_RIGHTS_RESPONSE_DAYS = 90

# K-21's target is "median hours to acknowledge <= 72h (policy)". Unlike the
# response period this one has no statutory number behind it, so it is a
# published service commitment: configurable per tenant through
# `organizations.settings['rights_acknowledgement_hours']`, defaulting to 72.
# The ceiling exists so the acknowledgement clock can never be configured to
# outlast the response clock it sits inside.
DEFAULT_RIGHTS_ACKNOWLEDGEMENT_HOURS = 72
MAX_RIGHTS_ACKNOWLEDGEMENT_HOURS = 90 * 24


class RightsRequest(Base):
    """One s.11-14 request, from intake to closure evidence."""

    __tablename__ = "rights_requests"
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('ACCESS','CORRECTION','ERASURE','NOMINATION')",
            name="ck_rights_requests_type",
        ),
        CheckConstraint(
            "status IN ('RECEIVED','ACKNOWLEDGED','VERIFYING','IN_PROGRESS',"
            "'FULFILLED','REJECTED','CLOSED')",
            name="ck_rights_requests_status",
        ),
        CheckConstraint(
            "channel IN ('PORTAL','STAFF','EMAIL','PHONE','POST','OTHER')",
            name="ck_rights_requests_channel",
        ),
        # The same ceiling organizations.grievance_response_days carries, so a
        # rights request can never be written with a longer clock than the
        # Rules permit for the comparable obligation.
        CheckConstraint(
            "response_days > 0 AND response_days <= 90",
            name="ck_rights_requests_response_days",
        ),
        CheckConstraint(
            "acknowledgement_hours > 0 AND acknowledgement_hours <= 2160",
            name="ck_rights_requests_acknowledgement_hours",
        ),
        # R.14(2), as a constraint rather than a convention. See the module
        # docstring: an ACCESS request fulfilled without an established
        # identity is a disclosure of one person's data to another.
        CheckConstraint(
            "status <> 'FULFILLED' OR identity_verified",
            name="ck_rights_requests_fulfilled_is_verified",
        ),
        # And a verification claim that names neither a method nor a moment is
        # not evidence of anything, so it cannot be stored.
        CheckConstraint(
            "identity_verified = false OR "
            "(verified_at IS NOT NULL AND verification_method IS NOT NULL)",
            name="ck_rights_requests_verified_has_evidence",
        ),
        CheckConstraint(
            "verification_method IS NULL OR "
            "verification_method IN ('EMAIL_OTP','FIDUCIARY_ASSERTED')",
            name="ck_rights_requests_verification_method",
        ),
        # Mirrors ck_erasure_jobs_executed_has_evidence: no closed request
        # without the hash and the timestamp that make the closure provable.
        CheckConstraint(
            "status <> 'CLOSED' OR (closed_at IS NOT NULL AND closure_hash IS NOT NULL)",
            name="ck_rights_requests_closed_has_evidence",
        ),
        # A refusal has to say why, in a countable way - "we said no" with no
        # recorded ground is the shape of an unlawful refusal.
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_rights_requests_rejected_has_reason",
        ),
        CheckConstraint(
            "rejection_reason IS NULL OR rejection_reason IN ("
            "'IDENTITY_NOT_ESTABLISHED','RETENTION_REQUIRED_BY_LAW','LEGAL_HOLD',"
            "'PURPOSE_STILL_SERVED','NOT_A_DATA_PRINCIPAL','DUPLICATE',"
            "'WITHDRAWN_BY_PRINCIPAL','OTHER')",
            name="ck_rights_requests_rejection_reason",
        ),
        # The queue's default view ("this tenant's open requests, soonest
        # deadline first") and the SLA sweep ("non-terminal, due_at <= now").
        Index("ix_rights_requests_tenant_status_due", "tenant_id", "status", "due_at"),
        Index("ix_rights_requests_tenant_type", "tenant_id", "request_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )

    # Opaque, non-sequential, CSPRNG-generated, unique, and stored in the
    # clear so it stays the one indexable handle on an otherwise encrypted
    # record. A sequential reference on a rights request would publish how
    # many people have asked for their data and let anyone holding one
    # reference walk to their neighbours' - see
    # app/services/rights_requests.py::generate_reference_no.
    reference_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="PORTAL", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="RECEIVED", nullable=False, index=True)

    # What the principal actually asked for, in her own words. Encrypted: a
    # request narrative routinely restates the personal data it is about.
    request_detail: Mapped[str] = mapped_column(EncryptedText, default="")
    # CORRECTION only: the field -> new value map the principal is asking for,
    # and the before-image captured when it was applied. Both are personal
    # data by definition, so both are encrypted.
    requested_changes: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    applied_changes: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)

    # ---- the clock -------------------------------------------------------
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Snapshot of the tenant's published acknowledgement commitment at receipt.
    acknowledgement_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_RIGHTS_ACKNOWLEDGEMENT_HOURS
    )
    acknowledgement_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Snapshot of the tenant's published response period at receipt. Snapshotted
    #: for the same reason grievance.response_days is: a tenant that later
    #: changes its published period must not be able to move the deadline of a
    #: request already in flight.
    response_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_RIGHTS_RESPONSE_DAYS
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # ---- R.14(2) identity verification -----------------------------------
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    verification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The consent context whose verification this request relies on. Recorded
    #: rather than re-derived so an auditor can follow the exact credential
    #: back to the OTP challenge that was confirmed against it.
    #:
    #: ``ondelete="SET NULL"`` is not cosmetic. `consent_contexts` is one of
    #: the record classes the R1-06 erasure engine HARD DELETES
    #: (`services/erasure.py::_erase_consent_contexts`) - a spent credential
    #: is not evidence, so R.8 requires it to go. A restricting foreign key
    #: here would make that DELETE raise, and an erasure request would break
    #: the very engine it delegates to at the moment of execution. Nulling is
    #: also the correct outcome on the merits: what has to survive is the fact
    #: that identity WAS established and how, and that lives in
    #: `verification_method`/`verified_at` on this row and in the immutable
    #: audit ledger - not in a short-lived token row that must be destroyed.
    verification_context_id: Mapped[int | None] = mapped_column(
        ForeignKey("consent_contexts.id", ondelete="SET NULL"), nullable=True
    )
    #: Which identifiers the principal supplied, per R.14(1) ("the particulars
    #: ... to identify her"). Never the values themselves - a note that email
    #: and phone were matched, not a second copy of them.
    verification_note: Mapped[str] = mapped_column(String(256), default="")

    # ---- handling --------------------------------------------------------
    assigned_to: Mapped[str] = mapped_column(String(128), default="")

    # ---- outcome ---------------------------------------------------------
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fulfilled_by: Mapped[str] = mapped_column(String(128), default="")
    resolution_summary: Mapped[str] = mapped_column(EncryptedText, default="")
    rejection_reason: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    rejection_basis: Mapped[str] = mapped_column(EncryptedText, default="")

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str] = mapped_column(String(128), default="")
    #: SHA-256 over the canonical JSON of the request's own outcome. The same
    #: value goes into the RIGHTS_REQUEST_CLOSED audit row, and that ledger is
    #: hash-chained and immutable - so the two can be compared.
    closure_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # ---- hand-offs -------------------------------------------------------
    #: ERASURE only. The R1-06 job that will actually carry the erasure out,
    #: with its 48-hour notice, its retention floors and its own evidence
    #: hash. This register never deletes anything itself.
    erasure_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("erasure_jobs.id"), nullable=True, index=True
    )
    #: NOMINATION only.
    nomination_id: Mapped[int | None] = mapped_column(
        ForeignKey("nominations.id"), nullable=True, index=True
    )
    #: ACCESS only: SHA-256 over the fulfilment package that was delivered, so
    #: "what exactly did you send her?" is answerable months later even though
    #: the package itself is generated on demand and never stored.
    package_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # String targets only - this module never imports entities.py, so
    # `Customer` is named rather than referenced and is deliberately left
    # un-annotated (a `Mapped["Customer"]` annotation would need the class
    # itself importable here to resolve, which is exactly the import this
    # module must not have).
    customer = relationship("Customer", lazy="joined")
    events: Mapped[list["RightsRequestEvent"]] = relationship(
        "RightsRequestEvent",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RightsRequestEvent.created_at",
    )

    # ---- derived helpers -------------------------------------------------

    def is_open(self) -> bool:
        return self.status not in RIGHTS_REQUEST_TERMINAL_STATUSES

    def is_overdue(self, now: datetime | None = None) -> bool:
        """Past its published response period and still not concluded.

        `due_at` is read back from Postgres tz-aware, but a row that has only
        been flushed (not reloaded) still holds whatever the caller set, so
        normalise before comparing rather than trusting either side.
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

    def acknowledgement_hours_taken(self) -> float | None:
        """Hours from receipt to acknowledgement, or None if unacknowledged.
        This is K-21's per-request measurement."""
        if self.acknowledged_at is None:
            return None
        received = self.received_at
        acknowledged = self.acknowledged_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        if acknowledged.tzinfo is None:
            acknowledged = acknowledged.replace(tzinfo=timezone.utc)
        return (acknowledged - received).total_seconds() / 3600.0


class RightsRequestEvent(Base):
    """The per-request timeline the admin queue and the principal's own view
    render.

    Deliberately NOT a substitute for the audit ledger: every state change
    here is also written to `audit_logs`, which is hash-chained and
    append-only and is the record a regulator would be shown. This table
    exists because that ledger is gated behind `audit.view` and is not
    per-entity queryable, and a principal must be able to follow the progress
    of her own request without being granted a tenant-wide audit trail.
    """

    __tablename__ = "rights_request_events"
    __table_args__ = (
        Index("ix_rights_request_events_request_created", "request_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("rights_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(16), default="")
    to_status: Mapped[str] = mapped_column(String(16), default="")
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    #: Free text written by a handler. Encrypted: a handler's note about a
    #: rights request is as sensitive as the request.
    note: Mapped[str] = mapped_column(EncryptedText, default="")
    actor_username: Mapped[str] = mapped_column(String(128), default="system")
    #: Whether this entry is safe to show the principal in her own view.
    #: Internal triage notes are not.
    visible_to_principal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    request: Mapped["RightsRequest"] = relationship("RightsRequest", back_populates="events")


# --------------------------------------------------------------------------- #
#  s.14 nomination
# --------------------------------------------------------------------------- #

# PENDING  - recorded by a verified principal; the nominee has not yet
#            confirmed. A nomination in this state confers nothing.
# VERIFIED - the nominee confirmed, from the contact details the principal
#            gave, using a code only that address received. This is the state
#            a nomination rests in for years: recorded, confirmed, dormant.
# ACTIVE   - a named officer activated it against recorded evidence of the
#            principal's death or incapacity. Only now may the nominee
#            exercise anything.
# DECLINED - the nominee said no.
# REVOKED  - the principal (or, after activation, a correction of the
#            triggering evidence) withdrew it.
NOMINATION_STATUSES = ["PENDING", "VERIFIED", "ACTIVE", "DECLINED", "REVOKED"]

NOMINATION_TRANSITIONS: dict[str, list[str]] = {
    "PENDING": ["VERIFIED", "DECLINED", "REVOKED"],
    # Activation is only ever reachable from VERIFIED: a nomination whose
    # nominee was never confirmed cannot be switched on at the moment the
    # principal is least able to contradict it.
    "VERIFIED": ["ACTIVE", "REVOKED"],
    "ACTIVE": ["REVOKED"],
    "DECLINED": [],
    "REVOKED": [],
}

# Why a nomination was activated. s.14 speaks of "death or incapacity"; the
# code is what makes the ground countable and the evidence reference is what
# makes it checkable.
NOMINATION_ACTIVATION_GROUNDS = ["DEATH", "INCAPACITY"]


class Nomination(Base):
    """s.14 / R.14(4): the individual a Data Principal nominates to exercise
    her rights in the event of her death or incapacity.

    This is the one register in the platform that records a *third party's*
    contact details, given by someone else, about a future event. Three
    consequences shaped it:

    **The nominee's details are personal data belonging to the nominee.**
    Name, email and phone are encrypted exactly as `Customer`'s are, and
    `nominee_email_search` is the HMAC companion that makes the encrypted
    address findable (see docs/ARCHITECTURE.md's field-encryption note - an encrypted
    column cannot be filtered with `=`).

    **A nomination confers nothing until it is confirmed by the nominee.**
    The principal alone naming an email address is an assertion about someone
    who has not been asked. `verification_code_hash` holds the HMAC of a
    CSPRNG code sent to the nominee's own address; only confirming it moves
    the row to VERIFIED. The code is never stored or returned in plain form,
    the comparison is constant-time, and the attempt budget is capped - the
    same discipline `app/services/otp.py` applies to a principal's own OTP,
    for the same reason: this code is a credential, not a nonce.

    **Activation is never self-service, and never automatic.** The event that
    activates a nomination is the principal's death or incapacity - precisely
    the moment she cannot contradict a false claim. So activation is a staff
    act under `rights.manage`, requires the row to already be VERIFIED,
    requires a recorded ground and an evidence reference (a death certificate
    number, a court order), and is enforced by
    `ck_nominations_active_is_authorised` rather than by a code path anyone
    could route around. A nominee cannot activate their own nomination by
    asserting a death.
    """

    __tablename__ = "nominations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','VERIFIED','ACTIVE','DECLINED','REVOKED')",
            name="ck_nominations_status",
        ),
        # VERIFIED and everything beyond it means the nominee actually
        # confirmed. A row claiming either state with no moment of
        # confirmation is not representable.
        CheckConstraint(
            "status NOT IN ('VERIFIED','ACTIVE') OR verified_at IS NOT NULL",
            name="ck_nominations_verified_has_timestamp",
        ),
        # Activation needs a named human, a moment, a statutory ground and a
        # reference to the evidence for it. See the class docstring.
        CheckConstraint(
            "status <> 'ACTIVE' OR (activated_at IS NOT NULL AND activated_by IS NOT NULL "
            "AND activation_ground IS NOT NULL AND length(trim(activation_evidence_ref)) > 0)",
            name="ck_nominations_active_is_authorised",
        ),
        CheckConstraint(
            "activation_ground IS NULL OR activation_ground IN ('DEATH','INCAPACITY')",
            name="ck_nominations_activation_ground",
        ),
        CheckConstraint(
            "verification_attempts >= 0 AND max_verification_attempts > 0",
            name="ck_nominations_attempt_budget",
        ),
        Index("ix_nominations_tenant_customer_status", "tenant_id", "customer_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    #: Opaque and CSPRNG-generated for the same reason reference_no is.
    nomination_ref: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)

    nominee_name: Mapped[str] = mapped_column(EncryptedString(512), nullable=False)
    nominee_email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    #: HMAC companion for the encrypted address - nothing fills it
    #: automatically, the service sets it (see docs/ARCHITECTURE.md).
    nominee_email_search: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    nominee_phone: Mapped[str] = mapped_column(EncryptedString(256), default="")
    nominee_relationship: Mapped[str] = mapped_column(String(64), default="")

    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False, index=True)

    # ---- the principal's own act -----------------------------------------
    #: The verified context the principal held when she made the nomination.
    #: A nomination is a rights act like any other and gets the same R.14(2)
    #: treatment - it is never accepted from an unverified caller.
    #: ``ondelete="SET NULL"`` for the same reason
    #: `RightsRequest.verification_context_id` carries it - see that column.
    principal_context_id: Mapped[int | None] = mapped_column(
        ForeignKey("consent_contexts.id", ondelete="SET NULL"), nullable=True
    )
    nominated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # ---- the nominee's confirmation --------------------------------------
    #: HMAC of a CSPRNG code, never the code. See the class docstring.
    verification_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_verification_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- activation ------------------------------------------------------
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activation_ground: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: A death certificate number, a court order reference. Stored in the
    #: clear: it is a document identifier, it is what an auditor checks the
    #: activation against, and it must stay searchable.
    activation_evidence_ref: Mapped[str] = mapped_column(String(256), default="")
    activation_note: Mapped[str] = mapped_column(EncryptedText, default="")

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revocation_reason: Mapped[str] = mapped_column(EncryptedText, default="")

    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    customer = relationship("Customer", lazy="joined")

    def is_verified(self) -> bool:
        return self.status in ("VERIFIED", "ACTIVE")
