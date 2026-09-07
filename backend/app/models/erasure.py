"""R1-06 (G-01..G-04, G-06, D-03): the retention and erasure engine's tables -
`retention_policies`, `legal_holds` and `erasure_jobs`.

Kept in its own module rather than in `entities.py` so these tables could be
built without contending for the single shared model file; every foreign key
names its target table as a string, so nothing here imports `entities.py` and
there is no import cycle. `entities.py` carries a single
`from app.models import erasure  # noqa: F401` at its tail so Alembic's
autogenerate (which imports only `app.models.entities`) sees these tables.

--------------------------------------------------------------------------
A FLOOR AND A CEILING, AND WHICH ONE WINS
--------------------------------------------------------------------------
R1-10 (`app/services/retention.py`) already built retention **floors**: a
minimum holding period per record class, in code, that `enforce_retention`
refuses to delete inside. This module is the opposite half - the **ceiling**
that s.8(7)/R.8(1) demand: erase personal data once the purpose it was
collected for is served.

The two can disagree. A ceiling that says "erase this principal's data now"
and a floor that says "this consent-evidence row may not be destroyed for a
year" are both real obligations, and the resolution is not a matter of taste:
s.8(7) itself carves out retention that is "necessary for compliance with any
law for the time being in force", and R.8(3) is exactly such a law. **The
floor wins, and it wins in code, not in a comment**:

* `app/services/erasure.py::assert_policy_respects_floors` refuses to persist
  a retention policy whose `retention_days` is below the floor in force for a
  record class R1-10 governs, and refuses `action="ERASE"` for a class R1-10
  marks `deletable=False`. Writing such a policy is a 422, not a warning.
* At execution time `app/services/erasure.py::_erase_record_class` computes
  the floor cutoff for every class it touches and destroys only rows older
  than it. Rows inside the floor are counted into `ErasureJob.records_retained`
  together with the floor that saved them and its statutory basis - so a
  regulator reading one erasure job can see exactly what was kept, for how
  long, and under which provision.

The consequence, stated plainly: a principal's *identifiers* are erased or
anonymised, and the evidence that they once consented is retained,
pseudonymised, until its own floor elapses. That is the same choice the
existing customer-purge path already makes (routes/crm.py::_purge_customer_data
anonymises the Customer and keeps every consent, evidence and audit row), and
this engine deliberately reuses that exact function rather than growing a
second, divergent anonymisation.

--------------------------------------------------------------------------
WHY ERASURE IS NEVER A SILENT BACKGROUND DELETE
--------------------------------------------------------------------------
Erasure is irreversible, so every one of them is an authorised,
actor-attributed act evidenced by an `erasure_jobs` row that exists *before*
anything is destroyed. Two distinct authorisation routes, and the difference
between them is who decided:

* **The principal decided.** A withdrawal that leaves no remaining lawful
  basis (s.8(7)), or an approved erasure request (s.12(3)). The job is
  created already carrying `authorised_by`/`authorised_at`/
  `authorisation_basis`, because the authorising act has already happened and
  is itself evidenced elsewhere (the consent history row, the grievance
  register entry named in `trigger_ref`). `erasure_executor` may act on it
  once the notice period has elapsed.
* **A clock inferred it.** The retention scan or the Third Schedule
  inactivity scan noticing that a period has run. That is a machine's
  inference about someone else's data, not anybody's decision, so such a job
  is created `PROPOSED` with no authorisation at all and `erasure_executor`
  will not touch it. A named staff user must authorise it through
  `POST /erasure/jobs/{job_ref}/authorise`, exactly as R1-10 made enforcement
  an authorised endpoint rather than a scheduled job.

--------------------------------------------------------------------------
THE 48-HOUR NOTICE IS A PRECONDITION, NOT A COURTESY
--------------------------------------------------------------------------
R.8(2) requires the Data Fiduciary to give the Data Principal notice at least
forty-eight hours before erasing under the R.8(1)/Third Schedule clock. Here
that is enforced rather than documented:

* `retention_policies.pre_erasure_notice_hours` carries a CHECK constraint of
  `>= 48`. The Rule states a minimum, so a longer notice is configurable and a
  shorter one is not representable.
* A job whose policy requires notice cannot be executed until
  `notice_sent_at` is set *and* `execute_after` (= notice_sent_at + the
  policy's hours) has passed. `execute_erasure_job` raises
  `ErasureNotAuthorised` otherwise; it does not warn and proceed.
* `notice_sent_at` is only ever set by `send_pre_erasure_notices`, from the
  ids `app/services/notifications.py::queue_notification` actually returned
  for the ERASURE_WARNING_48H event. A notice that could not be queued for any
  channel leaves the field null and the erasure blocked, which is the correct
  failure direction: no notice, no erasure.
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.encryption import EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# R.8(2): "at least forty-eight hours". A minimum, so this is a floor on the
# notice period, never a fixed value.
MIN_PRE_ERASURE_NOTICE_HOURS = 48

# R.8(1) read with the Third Schedule: three years from the date the Data
# Principal last approached the Data Fiduciary for the performance of the
# specified purpose or last exercised her rights (or the commencement of the
# Rules, whichever is later).
THIRD_SCHEDULE_INACTIVITY_DAYS = 3 * 365

# The classes of *personal data* this engine can act on. Deliberately a
# DIFFERENT vocabulary from app/services/retention.py::RETENTION_CLASSES,
# which enumerates classes of *evidence* and their statutory minimum holding
# periods. The two overlap by design at `notifications`, and that overlap is
# where floor-versus-ceiling is actually resolved - see the module docstring.
#
#   principal_personal_data - the `customers` row's direct identifiers: name,
#       email, phone, external id. ANONYMISE only; the row is the parent of
#       consents, consent history, evidence, receipts and audit rows that are
#       all inside their own R1-10 floors, so a DELETE would orphan records
#       that may not yet be destroyed. This is the floor beating the ceiling.
#   directory_record - the `crm_customers` demo-directory row (name, email,
#       phone, address, age, stored cookie preferences). Not an evidence
#       class, not referenced by any consent record, carries no R1-10 floor -
#       so this one genuinely supports a hard DELETE, and is what makes
#       "ERASE" a real action rather than a synonym for anonymisation.
#   consent_contexts - `consent_contexts` rows: short-lived context tokens
#       and their metadata. Hard DELETE; a spent credential is not evidence.
#   notifications - the principal's own queued/sent notifications. An R1-10
#       class with a 1-year floor (s.5/s.8(6): proof a required communication
#       was sent), which in practice means the erasure engine reports these as
#       retained rather than destroying them - including, pointedly, the very
#       ERASURE_WARNING_48H row that proves the notice was given.
ERASURE_RECORD_CLASSES = [
    "principal_personal_data",
    "directory_record",
    "consent_contexts",
    "notifications",
]

# ERASE     - the rows are destroyed.
# ANONYMISE - the identifying fields are overwritten and the row is kept,
#             which is the only lawful option for a record another record
#             still inside its floor points at.
ERASURE_ACTIONS = ["ERASE", "ANONYMISE"]

# Which classes may ever be hard-deleted by this engine, independent of what
# a policy asks for. Consulted by services/erasure.py::assert_policy_respects_floors
# and again at execution time, so a policy row written directly into the
# database still cannot produce a delete this list forbids.
HARD_DELETABLE_RECORD_CLASSES = {"directory_record", "consent_contexts"}

# What set the erasure in motion.
#   WITHDRAWAL     - s.8(7): consent withdrawn and no lawful basis remains.
#   RIGHTS_REQUEST - s.12(3): the principal asked for erasure and it was approved.
#   RETENTION      - the configured retention period for the class ran out.
#   INACTIVITY     - R.8(1)/Third Schedule: the three-year clock ran out.
#   MANUAL         - a staff user raised it directly, with a recorded reason.
ERASURE_TRIGGERS = ["WITHDRAWAL", "RIGHTS_REQUEST", "RETENTION", "INACTIVITY", "MANUAL"]

# PROPOSED  - a clock inferred this; nobody has authorised it. Terminal until
#             a named human authorises or cancels it.
# SCHEDULED - authorised, waiting for its pre-erasure notice to go out.
# NOTIFIED  - the notice was queued; `execute_after` is now set and running.
# BLOCKED   - a legal hold (or a floor) stands in the way. Re-checked on every
#             executor pass; a released hold moves it back to NOTIFIED.
# EXECUTED  - done, with `records_erased`, `records_retained` and an evidence hash.
# CANCELLED - withdrawn before execution, by a named actor with a reason.
# FAILED    - execution raised; the error is on the row and nothing partial was
#             committed (each job executes inside one transaction).
ERASURE_JOB_STATUSES = [
    "PROPOSED", "SCHEDULED", "NOTIFIED", "BLOCKED", "EXECUTED", "CANCELLED", "FAILED",
]

# Statuses from which a job can still reach execution. Anything else is done
# with, and the executor skips it.
ERASURE_JOB_LIVE_STATUSES = ("PROPOSED", "SCHEDULED", "NOTIFIED", "BLOCKED")


class RetentionPolicy(Base):
    """One configured retention *ceiling* per record class and scope.

    This is the operator-settable half of R1-06. It is not the floor: the
    floors are statutory minima that live in code
    (app/services/retention.py::RETENTION_CLASSES) precisely so nobody can
    lower them from an admin screen. A policy row that would erase inside a
    floor is refused when it is written and again when it is executed.

    `scope` is a `source_app` - the tenant code every customer, consent and
    audit row in this platform is already keyed by - or the literal `"*"`
    meaning "every tenant". Resolution is most-specific-first: a policy scoped
    to `CODEX` beats the `"*"` default for a CODEX customer. That keeps the
    common case (one platform-wide default per class) to a single row while
    letting a tenant with, say, a Third Schedule obligation carry its own.

    `retention_days` is the ceiling for data whose purpose has been served;
    `inactivity_days` is the separate R.8(1)/Third Schedule clock measured
    from `customers.last_interaction_at`. They are independent: a tenant that
    is not in the Third Schedule leaves `inactivity_days` null and only the
    first clock runs.

    `legal_basis_for_retention` is required (a non-empty CHECK) and is the
    answer to "why is this data still here at all until that day?" - s.8(7)
    permits retention only while the purpose is served or a law requires it,
    so a policy that cannot name one is not a policy.
    """

    __tablename__ = "retention_policies"
    __table_args__ = (
        UniqueConstraint("record_class", "scope", name="uq_retention_policies_class_scope"),
        CheckConstraint(
            "record_class IN ('principal_personal_data','directory_record',"
            "'consent_contexts','notifications')",
            name="ck_retention_policies_record_class",
        ),
        CheckConstraint("action IN ('ERASE','ANONYMISE')", name="ck_retention_policies_action"),
        # R.8(2) states a minimum ("at least forty-eight hours"), so a longer
        # notice is configurable and a shorter one is not representable.
        CheckConstraint(
            f"pre_erasure_notice_hours >= {MIN_PRE_ERASURE_NOTICE_HOURS}",
            name="ck_retention_policies_notice_hours",
        ),
        CheckConstraint(
            "retention_days IS NULL OR retention_days > 0",
            name="ck_retention_policies_retention_days",
        ),
        CheckConstraint(
            "inactivity_days IS NULL OR inactivity_days > 0",
            name="ck_retention_policies_inactivity_days",
        ),
        # s.8(7) permits retention only while the purpose is served or a law
        # requires it. A policy that cannot name the basis is not a policy.
        CheckConstraint(
            "length(trim(legal_basis_for_retention)) > 0",
            name="ck_retention_policies_legal_basis_present",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    policy_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    record_class: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: A source_app, or "*" for every tenant.
    scope: Mapped[str] = mapped_column(String(128), nullable=False, default="*")
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inactivity_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pre_erasure_notice_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=MIN_PRE_ERASURE_NOTICE_HOURS
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="ANONYMISE")
    legal_basis_for_retention: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class LegalHold(Base):
    """A standing instruction that named data must NOT be erased.

    Litigation, a regulatory investigation, a Board proceeding: all of them
    make erasure unlawful for as long as they run, and all of them outrank
    both the retention ceiling and the data principal's own s.12(3) request
    (s.8(7)'s "unless retention is necessary for compliance with any law").
    A hold therefore blocks erasure rather than delaying it - the job moves to
    BLOCKED with the hold named on it and stays there until the hold is
    released, at which point the next executor pass picks it up again.

    Scope narrows in two independent dimensions, each nullable to mean "all":
    `customer_id` (one principal, or every principal in the tenant) and
    `record_class` (one class, or every class). A hold with both null is a
    tenant-wide freeze, which is exactly what a regulator's preservation
    notice looks like.

    `released_at` rather than a DELETE: a hold that was in force between two
    dates is itself the evidence that an erasure was lawfully deferred, so
    releasing one is an update, never a removal.
    """

    __tablename__ = "legal_holds"
    __table_args__ = (
        CheckConstraint(
            "record_class IS NULL OR record_class IN ('principal_personal_data',"
            "'directory_record','consent_contexts','notifications')",
            name="ck_legal_holds_record_class",
        ),
        CheckConstraint(
            "length(trim(legal_basis)) > 0", name="ck_legal_holds_legal_basis_present"
        ),
        # The executor's sweep is "is there an active hold for this tenant
        # touching this customer?", which this serves directly.
        Index("ix_legal_holds_tenant_active", "tenant_id", "is_active", "customer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    hold_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    #: Null means every principal in scope of `tenant_id`.
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    #: Null means every record class.
    record_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Encrypted for the same reason a grievance description is: the reason a
    # hold exists routinely names the person and the proceeding.
    reason: Mapped[str] = mapped_column(EncryptedText, default="")
    legal_basis: Mapped[str] = mapped_column(String(512), nullable=False)
    placed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Optional self-expiry. A hold past this instant no longer blocks.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_reason: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ErasureJob(Base):
    """One erasure, from the moment it becomes due to the evidence that it
    happened. This row is the DoD's evidence, and it exists *before* anything
    is destroyed rather than being written afterwards as a report.

    `trigger_ref` is what makes the whole thing idempotent and reconcilable.
    It names the act that caused the erasure in the same shape
    app/services/processors.py already uses for its fan-outs
    (``withdrawal:<consent_id>:v<version>``, ``grievance:<reference_no>``,
    ``inactivity:<customer_id>:<iso date>``), it is unique per customer, and
    the processor-side ERASURE_INSTRUCTION alerts raised for this job are
    grouped under ``erasure:<job_ref>`` so the two sides can be joined without
    guessing.

    `records_erased` and `records_retained` are the outcome, per record class.
    The second is not padding: it is where the floor-versus-ceiling resolution
    becomes visible, carrying for each class the rows kept, the floor in days
    that kept them and the statutory basis for that floor. An auditor asking
    "you say you erased this person - what is this row still doing here?" is
    answered from the erasure job itself.

    `evidence_hash` is SHA-256 over the canonical JSON of the job's own
    outcome (see services/erasure.py::compute_evidence_hash). It is not a
    tamper-proofing mechanism on its own - this table has no append-only
    trigger - but the same hash is written into the `audit_logs` row for
    ERASURE_EXECUTED, and *that* ledger is hash-chained and immutable, so the
    two can be compared and a later edit to this row is detectable.
    """

    __tablename__ = "erasure_jobs"
    __table_args__ = (
        UniqueConstraint("customer_id", "trigger_ref", name="uq_erasure_jobs_customer_trigger"),
        CheckConstraint(
            "trigger IN ('WITHDRAWAL','RIGHTS_REQUEST','RETENTION','INACTIVITY','MANUAL')",
            name="ck_erasure_jobs_trigger",
        ),
        CheckConstraint("action IN ('ERASE','ANONYMISE')", name="ck_erasure_jobs_action"),
        CheckConstraint(
            "status IN ('PROPOSED','SCHEDULED','NOTIFIED','BLOCKED','EXECUTED','CANCELLED','FAILED')",
            name="ck_erasure_jobs_status",
        ),
        # An executed job must carry its evidence. This is the DoD as a
        # database constraint: no "erased" row without the hash and the
        # timestamp that make it evidence.
        CheckConstraint(
            "status <> 'EXECUTED' OR (executed_at IS NOT NULL AND evidence_hash IS NOT NULL)",
            name="ck_erasure_jobs_executed_has_evidence",
        ),
        # And it must have been authorised by someone. A row that reached
        # EXECUTED with no named authoriser is exactly the silent background
        # deletion this engine refuses to perform.
        CheckConstraint(
            "status <> 'EXECUTED' OR authorised_by IS NOT NULL",
            name="ck_erasure_jobs_executed_is_authorised",
        ),
        # The executor's sweep: live jobs for a tenant whose notice window has
        # elapsed, oldest first.
        Index("ix_erasure_jobs_status_execute_after", "status", "execute_after"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    job_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    #: The purpose whose withdrawal triggered this, where there was one.
    #: Informational: erasure of a shared `customers` row is never partial,
    #: which is precisely why a withdrawal only makes it due when NO lawful
    #: basis remains (see services/erasure.py::erasure_due_after_withdrawal).
    purpose_id: Mapped[int | None] = mapped_column(ForeignKey("purposes.id"), nullable=True)
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("retention_policies.id"), nullable=True
    )

    trigger: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_ref: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="ANONYMISE")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROPOSED", index=True)

    authorised_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorisation_basis: Mapped[str] = mapped_column(Text, default="")

    notice_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notice_hours: Mapped[int] = mapped_column(
        Integer, default=MIN_PRE_ERASURE_NOTICE_HOURS, nullable=False
    )
    notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_ids: Mapped[list] = mapped_column(JSON, default=list)
    #: notice_sent_at + notice_hours. Nothing executes before this instant.
    execute_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    records_erased: Mapped[dict] = mapped_column(JSON, default=dict)
    records_retained: Mapped[dict] = mapped_column(JSON, default=dict)
    processor_alert_ids: Mapped[list] = mapped_column(JSON, default=list)
    #: The anonymisation reference minted for the principal, where the action
    #: was ANONYMISE. The only handle left on the record after execution.
    anonymised_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    hold_id: Mapped[int | None] = mapped_column(ForeignKey("legal_holds.id"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_app: Mapped[str] = mapped_column(String(128), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
