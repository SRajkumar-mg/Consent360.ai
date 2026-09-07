"""R1-06 (G-01..G-04, G-06): the retention and erasure engine.

Read `app/models/erasure.py`'s docstring first - it carries the design
rationale (floor versus ceiling, why erasure is never a silent background
delete, why the 48-hour notice is a precondition rather than a courtesy).
This module is the mechanism.

The shape of an erasure, end to end:

    a trigger (s.8(7) withdrawal / s.12(3) request / a clock)
        -> propose_erasure_job()        an erasure_jobs row, BEFORE anything acts
        -> authorise_erasure_job()      a named actor, unless the principal
                                        already authorised it themselves
        -> send_pre_erasure_notices()   R.8(2), through queue_notification;
                                        sets notice_sent_at + execute_after
        -> execute_erasure_job()        anonymise/erase, per class, floor-checked
             |- raise_erasure_instructions()   the processor half (R3-07)
             |- RetentionAction(action=ANONYMISE|DELETE)  into R1-10's ledger
             '- compute_evidence_hash()        onto the job and into audit_logs

Every step is idempotent and every step writes to the same `erasure_jobs`
row, so a retried request, a re-run job or a duplicated trigger cannot produce
two erasures or two notices.

--------------------------------------------------------------------------
SECURE DELETION - what this module actually guarantees
--------------------------------------------------------------------------
Documented in full in `docs/compliance/SECURE_DELETION.md`; the short version,
because it bounds what the code below can honestly claim:

* An ANONYMISE overwrites the identifying columns in place and commits. The
  old ciphertext may survive in the table's dead tuples until VACUUM and in
  the WAL until it is recycled; that is a property of Postgres, not something
  application code can assert away.
* An ERASE issues a real DELETE, with the same caveat.
* Neither reaches backups. The controlling protection there is documented
  backup expiry plus the fact that `customers.email/name/phone`,
  `crm_customers.*` and `audit_logs.reason` are all stored under
  AES-256-GCM with a single `FIELD_ENCRYPTION_KEY`
  (app/core/encryption.py) - so a restored backup is only readable while
  that key exists. Per-principal crypto-shredding (a key per data principal,
  destroyed on erasure, which would make a restore unreadable for that one
  person) is NOT implemented and is called out as the residual gap G-06.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.entities import (
    Consent,
    ConsentContext,
    CrmCustomer,
    Customer,
    Notification,
    Purpose,
    RetentionAction,
)
from app.models.erasure import (
    ERASURE_ACTIONS,
    ERASURE_JOB_LIVE_STATUSES,
    ERASURE_RECORD_CLASSES,
    ERASURE_TRIGGERS,
    HARD_DELETABLE_RECORD_CLASSES,
    MIN_PRE_ERASURE_NOTICE_HOURS,
    THIRD_SCHEDULE_INACTIVITY_DAYS,
    ErasureJob,
    LegalHold,
    RetentionPolicy,
)
from app.services.audit import log_audit
from app.services.retention import (
    CLASSES_BY_CODE,
    RetentionFloorViolation,
    effective_floor_days,
)
from app.services.tenancy import resolve_tenant_id

logger = logging.getLogger("app.erasure")

#: Statuses in which a consent still constitutes a live lawful basis. Mirrors
#: services/decision_engine.py::_active_consent_statuses - kept as its own
#: constant here rather than imported so this module has no dependency on the
#: decision engine, which R1-09 is concurrently changing.
ACTIVE_CONSENT_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

#: How stale `customers.last_interaction_at` is allowed to get before a
#: principal-present request bothers to write it again. The clock it feeds is
#: three years long (R.8(1)/Third Schedule), so a one-hour resolution costs
#: nothing and saves a database write on every portal read.
LAST_INTERACTION_WRITE_INTERVAL = timedelta(hours=1)


class ErasureError(Exception):
    """Base class for the engine's refusals."""


class ErasureNotAuthorised(ErasureError):
    """Raised when a job is asked to execute before it lawfully may - no
    authorisation, no notice, or the notice period has not elapsed."""


class ErasureBlocked(ErasureError):
    """Raised when a legal hold forbids the erasure."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ref(prefix: str) -> str:
    """An opaque, non-sequential reference. Same idiom as the grievance
    register's `reference_no`: a job reference is quoted to a data principal
    in a notice, so it must not leak how many erasures this platform has run."""
    return f"{prefix}-{secrets.token_hex(6).upper()}"


# ---------------------------------------------------------------------------
# last_interaction_at - the R.8(1)/Third Schedule clock's zero point
# ---------------------------------------------------------------------------
def touch_last_interaction(
    db: Session, customer: Optional[Customer], *, channel: str = "", commit: bool = False
) -> bool:
    """Record that the data principal approached the fiduciary, or exercised a
    right, just now. Returns True when the column was actually written.

    R.8(1) read with the Third Schedule measures its three years from "the
    date on which the Data Principal last approached the Data Fiduciary for
    the performance of the specified purpose, or exercised her rights". That
    is deliberately about the *principal's* acts, so this is called only from
    surfaces where the principal is demonstrably present - minting a consent
    context (every demo-site login and integration handoff), any authenticated
    /portal action, and lodging a grievance. It is emphatically NOT called
    from `services/consent.py::_record_transition`, which also fires for
    system-initiated transitions such as the expiry job: letting a scheduled
    expiry reset a principal's inactivity clock would silently push their
    erasure date three years into the future every time the scheduler ran,
    which is the exact failure R.8(1) exists to prevent.

    Throttled to `LAST_INTERACTION_WRITE_INTERVAL` so a portal page that makes
    several calls does not make several writes.
    """
    if customer is None:
        return False
    now = utcnow()
    previous = _as_utc(customer.last_interaction_at)
    if previous is not None and now - previous < LAST_INTERACTION_WRITE_INTERVAL:
        return False
    customer.last_interaction_at = now
    if commit:
        db.commit()
    else:
        db.flush()
    logger.debug(
        "last_interaction_at refreshed for customer_id=%s via %s", customer.id, channel or "unknown"
    )
    return True


# ---------------------------------------------------------------------------
# Retention policies - the ceiling, checked against R1-10's floors
# ---------------------------------------------------------------------------
def assert_policy_respects_floors(
    record_class: str, *, action: str, retention_days: Optional[int], floor_days: Optional[int]
) -> None:
    """Refuse a retention policy that contradicts a statutory floor.

    Two distinct contradictions, both refused here rather than at execution
    time, so an impossible policy cannot even be stored:

    1. **A ceiling below a floor.** `retention_days` shorter than the floor in
       force for a class R1-10 governs would schedule an erasure for a day on
       which destruction is still unlawful.
    2. **A hard delete of something that may never be hard-deleted.**
       `action="ERASE"` for a class outside HARD_DELETABLE_RECORD_CLASSES -
       the `customers` row (parent of consents, evidence and audit rows still
       inside their own floors) and `notifications` (an R1-10 evidence class
       in its own right, carrying the proof that the pre-erasure notice was
       given).
    """
    if record_class not in ERASURE_RECORD_CLASSES:
        raise KeyError(f"Unknown erasure record class: {record_class}")
    if action not in ERASURE_ACTIONS:
        raise ValueError(f"Unknown erasure action: {action}")

    if action == "ERASE" and record_class not in HARD_DELETABLE_RECORD_CLASSES:
        raise RetentionFloorViolation(
            f"'{record_class}' can never be hard-deleted by the erasure engine: "
            + (
                "the customers row is the parent of consents, consent history, evidence, "
                "receipts and audit rows that are all inside their own R1-10 retention "
                "floors, so it is anonymised in place and the evidence is kept "
                "pseudonymised (DPDP Act s.8(7): retention necessary for compliance with law)."
                if record_class == "principal_personal_data"
                else "it is itself a retention class with a statutory floor "
                "(app/services/retention.py::RETENTION_CLASSES) - including the "
                "ERASURE_WARNING_48H row that evidences the R.8(2) notice."
            )
            + " Use action='ANONYMISE'."
        )

    if floor_days is not None and retention_days is not None and retention_days < floor_days:
        raise RetentionFloorViolation(
            f"Retention policy for '{record_class}' cannot erase after {retention_days} days: "
            f"the statutory floor in force is {floor_days} days "
            f"({CLASSES_BY_CODE[record_class].basis if record_class in CLASSES_BY_CODE else ''}). "
            f"A floor and a ceiling cannot both be honoured below the floor; the floor wins "
            f"(DPDP Act s.8(7): retention necessary for compliance with any law)."
        )


def floor_for_record_class(db: Session, record_class: str) -> Optional[int]:
    """The R1-10 floor in force for an erasure record class, or None when
    R1-10 does not govern that class at all.

    The two vocabularies overlap by exactly one code (`notifications`), which
    is deliberate and is where the resolution actually bites - see
    app/models/erasure.py's docstring.
    """
    if record_class in CLASSES_BY_CODE:
        return effective_floor_days(db, record_class)
    return None


def upsert_retention_policy(
    db: Session,
    *,
    record_class: str,
    scope: str = "*",
    retention_days: Optional[int] = None,
    inactivity_days: Optional[int] = None,
    pre_erasure_notice_hours: int = MIN_PRE_ERASURE_NOTICE_HOURS,
    action: str = "ANONYMISE",
    legal_basis_for_retention: str,
    is_active: bool = True,
    notes: str = "",
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> RetentionPolicy:
    """Create or update the policy for one (record_class, scope) pair.

    Refuses anything that contradicts a floor, and refuses a notice period
    below R.8(2)'s forty-eight hours before the database's own CHECK gets the
    chance, so the caller gets a sentence rather than an IntegrityError.
    """
    if pre_erasure_notice_hours < MIN_PRE_ERASURE_NOTICE_HOURS:
        raise ValueError(
            f"pre_erasure_notice_hours must be at least {MIN_PRE_ERASURE_NOTICE_HOURS}: "
            f"DPDP Rules 2025 R.8(2) requires notice 'at least forty-eight hours' before erasure."
        )
    if not (legal_basis_for_retention or "").strip():
        raise ValueError(
            "legal_basis_for_retention is required: DPDP Act s.8(7) permits retention only "
            "while the specified purpose is being served or a law requires it, so a policy "
            "that cannot name the basis is not a policy."
        )
    floor = floor_for_record_class(db, record_class)
    assert_policy_respects_floors(
        record_class, action=action, retention_days=retention_days, floor_days=floor
    )

    row = (
        db.query(RetentionPolicy)
        .filter(RetentionPolicy.record_class == record_class, RetentionPolicy.scope == scope)
        .first()
    )
    created = row is None
    if row is None:
        row = RetentionPolicy(
            policy_ref=_ref("RP"),
            record_class=record_class,
            scope=scope,
            tenant_id=resolve_tenant_id(db, scope) if scope != "*" else None,
        )
        db.add(row)
    row.retention_days = retention_days
    row.inactivity_days = inactivity_days
    row.pre_erasure_notice_hours = pre_erasure_notice_hours
    row.action = action
    row.legal_basis_for_retention = legal_basis_for_retention
    row.is_active = is_active
    row.notes = notes
    row.updated_by = actor_username
    db.flush()
    log_audit(
        db,
        "RETENTION_POLICY_CREATED" if created else "RETENTION_POLICY_UPDATED",
        actor_username=actor_username,
        tenant_id=row.tenant_id,
        reason=(
            f"Retention policy {row.policy_ref} for '{record_class}' scope '{scope}': "
            f"{action} after {retention_days} day(s), inactivity {inactivity_days} day(s), "
            f"{pre_erasure_notice_hours}h notice"
        ),
        request_id=request_id,
        metadata={
            "policy_ref": row.policy_ref, "record_class": record_class, "scope": scope,
            "retention_days": retention_days, "inactivity_days": inactivity_days,
            "action": action, "pre_erasure_notice_hours": pre_erasure_notice_hours,
            "floor_days": floor,
        },
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


def resolve_policy(db: Session, record_class: str, source_app: str) -> Optional[RetentionPolicy]:
    """The policy in force for a record class and tenant: the tenant's own if
    it has one, else the `"*"` platform default, else None.

    Most-specific-first rather than "merge the two", because a half-inherited
    erasure policy (this tenant's action, the default's notice period) is not
    something anyone can reason about when the erasure turns out to have been
    wrong.
    """
    rows = (
        db.query(RetentionPolicy)
        .filter(
            RetentionPolicy.record_class == record_class,
            RetentionPolicy.is_active.is_(True),
            or_(RetentionPolicy.scope == source_app, RetentionPolicy.scope == "*"),
        )
        .all()
    )
    if not rows:
        return None
    exact = [r for r in rows if r.scope == source_app]
    return exact[0] if exact else rows[0]


def ensure_default_policies(db: Session, *, commit: bool = True) -> list[RetentionPolicy]:
    """Idempotently create one platform-wide (`scope="*"`) policy per erasure
    record class, seeded at values that are lawful on day one.

    Chosen so the engine is safe before anyone configures it: every class
    defaults to ANONYMISE where a hard delete is forbidden, the notice period
    is R.8(2)'s minimum, `retention_days` is left null for the classes whose
    erasure is event-driven rather than clock-driven (a withdrawal has no
    "period"), and `inactivity_days` carries the Third Schedule three years
    only on `principal_personal_data`, which is the class that clock is
    actually about.
    """
    defaults = [
        dict(
            record_class="principal_personal_data",
            action="ANONYMISE",
            retention_days=None,
            inactivity_days=THIRD_SCHEDULE_INACTIVITY_DAYS,
            legal_basis_for_retention=(
                "DPDP Act s.8(7): retained only while the specified purpose is being served, "
                "and thereafter only where retention is necessary for compliance with any law "
                "for the time being in force - here DPDP Rules 2025 R.8(3) and R.6(1)(e), which "
                "require the consent record and processing logs to be kept at least one year. "
                "The identifiers are therefore anonymised and the evidence kept pseudonymised."
            ),
            notes=(
                "Anonymise-only by construction: the customers row is the parent of consents, "
                "consent history, evidence, receipts and audit rows that are all inside their "
                "own R1-10 floors. inactivity_days carries R.8(1)/Third Schedule's three-year "
                "clock; a tenant outside the Third Schedule classes can clear it."
            ),
        ),
        dict(
            record_class="directory_record",
            action="ERASE",
            retention_days=None,
            inactivity_days=THIRD_SCHEDULE_INACTIVITY_DAYS,
            legal_basis_for_retention=(
                "No statutory retention applies: the CRM directory row (name, email, phone, "
                "address, age, stored cookie preferences) is an operational convenience, not "
                "evidence of a consent, and is not referenced by any consent record. It is "
                "therefore genuinely destroyed rather than anonymised."
            ),
            notes="The one class in this engine that a hard DELETE is lawful for.",
        ),
        dict(
            record_class="consent_contexts",
            action="ERASE",
            retention_days=None,
            inactivity_days=None,
            legal_basis_for_retention=(
                "No statutory retention applies: a consent context is a short-lived credential "
                "(15 minutes) and its use is separately evidenced in the append-only audit "
                "ledger by CONTEXT_CREATED/CONTEXT_CONSUMED rows, which are retained. Deleting "
                "the spent credential destroys no evidence."
            ),
            notes="",
        ),
        dict(
            record_class="notifications",
            action="ANONYMISE",
            retention_days=None,
            inactivity_days=None,
            legal_basis_for_retention=(
                "DPDP Rules 2025 R.8(3)/R.6(1)(e) and DPDP Act s.5/s.8(6): a notification is "
                "the proof that a required communication was made - including the R.8(2) "
                "pre-erasure notice itself - and carries a one-year floor "
                "(app/services/retention.py::RETENTION_CLASSES['notifications'])."
            ),
            notes=(
                "In practice this class is reported as RETAINED by every erasure job rather "
                "than acted on: its floor is one year and the rows an erasure would reach are "
                "by definition newer than that. That is the floor beating the ceiling, and it "
                "is visible in ErasureJob.records_retained."
            ),
        ),
    ]
    existing = {
        (r.record_class, r.scope)
        for r in db.query(RetentionPolicy.record_class, RetentionPolicy.scope).all()
    }
    created: list[RetentionPolicy] = []
    for spec in defaults:
        if (spec["record_class"], "*") in existing:
            continue
        floor = floor_for_record_class(db, spec["record_class"])
        assert_policy_respects_floors(
            spec["record_class"], action=spec["action"],
            retention_days=spec["retention_days"], floor_days=floor,
        )
        row = RetentionPolicy(
            policy_ref=_ref("RP"),
            scope="*",
            tenant_id=None,
            pre_erasure_notice_hours=MIN_PRE_ERASURE_NOTICE_HOURS,
            is_active=True,
            updated_by="system",
            **spec,
        )
        db.add(row)
        created.append(row)
    if created:
        db.flush()
        if commit:
            db.commit()
            for row in created:
                db.refresh(row)
    return created


# ---------------------------------------------------------------------------
# Legal holds
# ---------------------------------------------------------------------------
def place_legal_hold(
    db: Session,
    *,
    legal_basis: str,
    reason: str = "",
    customer_id: Optional[int] = None,
    record_class: Optional[str] = None,
    tenant_id: Optional[int] = None,
    expires_at: Optional[datetime] = None,
    placed_by: str = "system",
    request_id: Optional[str] = None,
) -> LegalHold:
    if not (legal_basis or "").strip():
        raise ValueError(
            "legal_basis is required: a hold that cannot name the law, proceeding or "
            "investigation it serves is indistinguishable from simply refusing to erase."
        )
    if record_class is not None and record_class not in ERASURE_RECORD_CLASSES:
        raise KeyError(f"Unknown erasure record class: {record_class}")
    hold = LegalHold(
        hold_ref=_ref("LH"),
        tenant_id=tenant_id,
        customer_id=customer_id,
        record_class=record_class,
        reason=reason,
        legal_basis=legal_basis,
        placed_by=placed_by,
        placed_at=utcnow(),
        expires_at=expires_at,
        is_active=True,
        request_id=request_id,
    )
    db.add(hold)
    db.flush()
    log_audit(
        db, "LEGAL_HOLD_PLACED", actor_username=placed_by, tenant_id=tenant_id,
        customer_id=customer_id,
        reason=f"Legal hold {hold.hold_ref} placed: {legal_basis}",
        request_id=request_id,
        metadata={"hold_ref": hold.hold_ref, "customer_id": customer_id,
                  "record_class": record_class,
                  "expires_at": expires_at.isoformat() if expires_at else None},
        commit=False,
    )
    db.commit()
    db.refresh(hold)
    return hold


def release_legal_hold(
    db: Session, hold: LegalHold, *, released_by: str, release_reason: str = "",
    request_id: Optional[str] = None,
) -> LegalHold:
    hold.is_active = False
    hold.released_at = utcnow()
    hold.released_by = released_by
    hold.release_reason = release_reason
    db.flush()
    log_audit(
        db, "LEGAL_HOLD_RELEASED", actor_username=released_by, tenant_id=hold.tenant_id,
        customer_id=hold.customer_id,
        reason=f"Legal hold {hold.hold_ref} released: {release_reason}",
        request_id=request_id, metadata={"hold_ref": hold.hold_ref}, commit=False,
    )
    db.commit()
    db.refresh(hold)
    return hold


def active_hold_for(
    db: Session, customer: Customer, *, record_class: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[LegalHold]:
    """The first active, unexpired hold that covers this principal (and, when
    given, this record class). A hold with a null `customer_id` covers every
    principal in its tenant; one with a null `record_class` covers every class.
    """
    now = now or utcnow()
    rows = (
        db.query(LegalHold)
        .filter(LegalHold.is_active.is_(True))
        .filter(or_(LegalHold.customer_id == customer.id, LegalHold.customer_id.is_(None)))
        .filter(or_(LegalHold.tenant_id == customer.tenant_id, LegalHold.tenant_id.is_(None)))
        .order_by(LegalHold.id.asc())
        .all()
    )
    for hold in rows:
        expires = _as_utc(hold.expires_at)
        if expires is not None and expires <= now:
            continue
        if record_class is not None and hold.record_class not in (None, record_class):
            continue
        return hold
    return None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
def erasure_due_after_withdrawal(db: Session, consent: Consent) -> tuple[bool, str]:
    """Is s.8(7) erasure due because of this withdrawal? Returns (due, why).

    s.8(7) requires erasure "upon the Data Principal withdrawing her consent
    ... unless retention is necessary for compliance with any law". It does
    NOT say "erase everything the moment any one consent is withdrawn", and
    doing so would be wrong here: `customers` holds one identity row shared
    across every purpose, so wiping it on a single withdrawal would destroy
    the identity underpinning consents the principal has deliberately kept
    active. There is no per-purpose copy of the identifiers to erase instead.

    So the rule is the narrowest one that is still faithful to s.8(7): the
    erasure becomes due when the withdrawal leaves the principal with **no
    remaining active consent** in this tenant. While any consent is still
    live, the purpose is still being served and the identifiers are still
    necessary for it - which is exactly the s.8(7) carve-out, applied at the
    grain the data model actually has. Every withdrawal still causes the
    processor cease-processing fan-out (services/consent.py), which is the
    s.6(6) obligation and is separate from this one.
    """
    remaining = (
        db.query(func.count(Consent.id))
        .filter(
            Consent.customer_id == consent.customer_id,
            Consent.id != consent.id,
            Consent.status.in_(ACTIVE_CONSENT_STATUSES),
        )
        .scalar()
        or 0
    )
    if remaining:
        return False, (
            f"{remaining} consent(s) remain active for this principal, so the specified "
            f"purpose is still being served and s.8(7) does not yet require erasure"
        )
    return True, (
        "no consent remains active for this principal after the withdrawal, so no lawful "
        "basis is left and DPDP Act s.8(7) requires erasure"
    )


def approve_rights_request_erasure(
    db: Session,
    customer: Customer,
    *,
    request_ref: str,
    approved_by: str,
    basis: str = "",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> ErasureJob:
    """s.12(3): the principal asked for erasure and a named officer approved it.

    `request_ref` is the handle on the request as it exists elsewhere - in
    this platform, a grievance `reference_no` from the s.12(3)
    ERASURE_REQUEST category (app/models/grievance.py). It is recorded on the
    job as ``rights-request:<ref>`` so the register entry and the erasure can
    be joined from either side.

    The approval is what authorises the erasure, so the approver is the
    authoriser - unlike a clock-driven proposal, this job needs no second
    human. It still gets the R.8(2) notice and its period before anything is
    destroyed: the Rule's notice requirement does not have an exception for
    an erasure the principal herself asked for, and the window is what lets
    her say "not this account" if the request was not really hers.
    """
    return propose_erasure_job(
        db, customer, trigger="RIGHTS_REQUEST",
        trigger_ref=f"rights-request:{request_ref}",
        reason=f"Erasure request {request_ref} approved by {approved_by}",
        source_app=source_app, actor_username=approved_by, request_id=request_id,
        authorised_by=approved_by,
        authorisation_basis=(
            basis
            or f"DPDP Act s.12(3): erasure request {request_ref} approved by {approved_by}."
        ),
    )


def propose_erasure_job(
    db: Session,
    customer: Customer,
    *,
    trigger: str,
    trigger_ref: str,
    reason: str = "",
    purpose_id: Optional[int] = None,
    source_app: str = "",
    actor_username: str = "system",
    request_id: Optional[str] = None,
    authorised_by: Optional[str] = None,
    authorisation_basis: str = "",
    commit: bool = True,
) -> ErasureJob:
    """Create the `erasure_jobs` row that will authorise, evidence and bound
    an erasure - **before** anything is destroyed.

    Idempotent per (customer, trigger_ref): a retried withdrawal, a re-run
    scan or a duplicated request returns the row already raised rather than a
    second erasure and a second notice.

    `authorised_by` is passed only where the authorising act has already
    happened and is separately evidenced - the principal's own withdrawal, or
    an approved s.12(3) request. A clock-driven proposal passes none and the
    job stays PROPOSED until a named human authorises it.
    """
    if trigger not in ERASURE_TRIGGERS:
        raise ValueError(f"Unknown erasure trigger: {trigger}")

    # Seeded lazily rather than in the migration: the default policies carry
    # long statutory-basis prose that belongs in one place (this module), and
    # a migration copying it would create a second source of truth that drifts.
    # Idempotent and cheap - one SELECT when the rows already exist.
    ensure_default_policies(db)

    existing = (
        db.query(ErasureJob)
        .filter(ErasureJob.customer_id == customer.id, ErasureJob.trigger_ref == trigger_ref)
        .first()
    )
    if existing:
        return existing

    source_app = source_app or customer.source_app or ""
    policy = resolve_policy(db, "principal_personal_data", source_app)
    notice_hours = policy.pre_erasure_notice_hours if policy else MIN_PRE_ERASURE_NOTICE_HOURS
    action = policy.action if policy else "ANONYMISE"

    now = utcnow()
    job = ErasureJob(
        job_ref=_ref("ERJ"),
        tenant_id=customer.tenant_id,
        customer_id=customer.id,
        purpose_id=purpose_id,
        policy_id=policy.id if policy else None,
        trigger=trigger,
        trigger_ref=trigger_ref,
        action=action,
        status="SCHEDULED" if authorised_by else "PROPOSED",
        authorised_by=authorised_by,
        authorised_at=now if authorised_by else None,
        authorisation_basis=authorisation_basis,
        notice_required=True,
        notice_hours=notice_hours,
        source_app=source_app,
        created_by=actor_username,
        request_id=request_id,
        reason=reason,
        details={},
        created_at=now,
    )
    db.add(job)
    db.flush()
    log_audit(
        db, "ERASURE_JOB_PROPOSED", actor_username=actor_username, source_app=source_app,
        tenant_id=customer.tenant_id, customer_id=customer.id,
        customer_external_id=customer.external_id, purpose_id=purpose_id,
        reason=reason or f"Erasure job {job.job_ref} raised by {trigger}",
        request_id=request_id,
        metadata={"job_ref": job.job_ref, "trigger": trigger, "trigger_ref": trigger_ref,
                  "action": action, "status": job.status, "notice_hours": notice_hours,
                  "policy_ref": policy.policy_ref if policy else None},
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(job)
    return job


def authorise_erasure_job(
    db: Session, job: ErasureJob, *, actor_username: str, basis: str,
    request_id: Optional[str] = None,
) -> ErasureJob:
    """A named actor takes responsibility for an irreversible act.

    Required for every clock-driven proposal, because a scan noticing that a
    period elapsed is an inference about somebody else's data and not a
    decision anyone made. R1-10 drew the same line for record destruction
    (`POST /retention/enforce`, gated on policy.manage, never scheduled).
    """
    if job.status not in ("PROPOSED", "SCHEDULED", "BLOCKED", "NOTIFIED"):
        raise ErasureNotAuthorised(
            f"Erasure job {job.job_ref} is {job.status} and can no longer be authorised."
        )
    if not (basis or "").strip():
        raise ValueError("An authorisation basis is required: erasure is irreversible.")
    job.authorised_by = actor_username
    job.authorised_at = utcnow()
    job.authorisation_basis = basis
    if job.status == "PROPOSED":
        job.status = "SCHEDULED"
    db.flush()
    log_audit(
        db, "ERASURE_JOB_AUTHORISED", actor_username=actor_username, source_app=job.source_app,
        tenant_id=job.tenant_id, customer_id=job.customer_id,
        reason=f"Erasure job {job.job_ref} authorised: {basis}",
        request_id=request_id, metadata={"job_ref": job.job_ref}, commit=False,
    )
    db.commit()
    db.refresh(job)
    return job


def cancel_erasure_job(
    db: Session, job: ErasureJob, *, actor_username: str, reason: str,
    request_id: Optional[str] = None,
) -> ErasureJob:
    if job.status == "EXECUTED":
        raise ErasureError(
            f"Erasure job {job.job_ref} has already executed; erasure is irreversible and "
            f"cancelling the record of it would be a falsification."
        )
    job.status = "CANCELLED"
    job.cancelled_at = utcnow()
    job.cancelled_by = actor_username
    job.cancel_reason = reason
    db.flush()
    log_audit(
        db, "ERASURE_JOB_CANCELLED", actor_username=actor_username, source_app=job.source_app,
        tenant_id=job.tenant_id, customer_id=job.customer_id,
        reason=f"Erasure job {job.job_ref} cancelled: {reason}",
        request_id=request_id, metadata={"job_ref": job.job_ref}, commit=False,
    )
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# R.8(2): the 48-hour pre-erasure notice
# ---------------------------------------------------------------------------
def send_pre_erasure_notice(
    db: Session, job: ErasureJob, *, actor_username: str = "system",
    request_id: Optional[str] = None, now: Optional[datetime] = None,
) -> ErasureJob:
    """Queue the R.8(2) notice for one job and start its clock.

    `notice_sent_at` and `execute_after` are written **only** from the ids
    `queue_notification` actually returned. If it queued nothing - no email,
    no phone, no in-app row - the fields stay null, the job stays SCHEDULED
    and `execute_erasure_job` will refuse it. No notice, no erasure: that is
    the correct failure direction for a right the principal is meant to be
    able to exercise ("contact us before then if this was not requested by
    you") in the window the notice creates.

    Idempotent: a job that already has `notice_sent_at` is returned untouched,
    so re-running the job cannot restart a principal's 48 hours.
    """
    now = now or utcnow()
    if job.notice_sent_at is not None:
        return job
    if not job.notice_required:
        return job
    if job.status not in ("SCHEDULED", "BLOCKED", "NOTIFIED"):
        raise ErasureNotAuthorised(
            f"Erasure job {job.job_ref} is {job.status}; a pre-erasure notice is only sent for "
            f"an authorised job (an unauthorised proposal must not tell a principal their data "
            f"is about to be erased when nobody has decided that it will be)."
        )

    from app.services.notifications import queue_notification

    customer = db.get(Customer, job.customer_id)
    if customer is None:
        raise ErasureError(f"Erasure job {job.job_ref} has no customer row to notify.")

    deadline = now + timedelta(hours=job.notice_hours)
    notifications = queue_notification(
        db,
        customer=customer,
        event_type="ERASURE_WARNING_48H",
        source_app=job.source_app or customer.source_app or "",
        context={
            "erasure_ref": job.job_ref,
            "erasure_at": deadline.isoformat(),
            "notice_hours": job.notice_hours,
        },
        request_id=request_id,
        actor_username=actor_username,
    )
    if not notifications:
        logger.warning(
            "Pre-erasure notice for job %s could not be queued on any channel; the erasure "
            "stays blocked until it can be", job.job_ref,
        )
        return job

    job.notice_sent_at = now
    job.notification_ids = [n.id for n in notifications]
    job.execute_after = deadline
    job.status = "NOTIFIED"
    db.flush()
    log_audit(
        db, "ERASURE_NOTICE_SENT", actor_username=actor_username, source_app=job.source_app,
        tenant_id=job.tenant_id, customer_id=job.customer_id,
        customer_external_id=customer.external_id,
        reason=(
            f"Pre-erasure notice for job {job.job_ref} queued on "
            f"{len(notifications)} channel(s); erasure not before {deadline.isoformat()} "
            f"(DPDP Rules 2025 R.8(2), {job.notice_hours}h)"
        ),
        request_id=request_id,
        metadata={"job_ref": job.job_ref, "notification_ids": job.notification_ids,
                  "channels": [n.channel for n in notifications],
                  "notice_hours": job.notice_hours, "execute_after": deadline.isoformat()},
        commit=False,
    )
    db.commit()
    db.refresh(job)
    return job


def send_due_pre_erasure_notices(
    db: Session, *, limit: int = 200, actor_username: str = "scheduler",
    now: Optional[datetime] = None,
) -> dict:
    """Every authorised job still waiting for its notice. Backs the
    `pre_erasure_notices` scheduled job."""
    now = now or utcnow()
    jobs = (
        db.query(ErasureJob)
        .filter(
            ErasureJob.status.in_(("SCHEDULED", "BLOCKED")),
            ErasureJob.notice_required.is_(True),
            ErasureJob.notice_sent_at.is_(None),
            ErasureJob.authorised_by.isnot(None),
        )
        .order_by(ErasureJob.id.asc())
        .limit(limit)
        .all()
    )
    sent = 0
    failed = 0
    for job in jobs:
        try:
            before = job.notice_sent_at
            send_pre_erasure_notice(db, job, actor_username=actor_username, now=now)
            if job.notice_sent_at is not None and before is None:
                sent += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001 - one undeliverable notice must not stop the sweep
            db.rollback()
            failed += 1
            logger.exception("Failed to send pre-erasure notice for job %s", job.job_ref)
    return {"candidates": len(jobs), "notices_sent": sent, "notices_failed": failed}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _retained_entry(db: Session, record_class: str, rows: int, floor_days: int) -> dict:
    basis = CLASSES_BY_CODE[record_class].basis if record_class in CLASSES_BY_CODE else ""
    return {
        "rows_retained": rows,
        "floor_days": floor_days,
        "basis": basis,
        "why": (
            "Retained under a statutory retention floor that outranks the erasure ceiling: "
            "DPDP Act s.8(7) permits retention that is necessary for compliance with any law "
            "for the time being in force."
        ),
    }


def _erase_principal_personal_data(db: Session, customer: Customer) -> tuple[int, str]:
    """Anonymise the `customers` row's direct identifiers.

    Delegates to the customer-purge path rather than reimplementing it. There
    must be exactly one definition of "this principal's identifiers are gone"
    in this platform: two would drift, and the day they drift is the day one
    of them leaves an identifier behind on a record someone has been told was
    erased. `routes/crm.py::_purge_customer_data` is that definition (it also
    deactivates the principal's contexts), it is already exercised by
    tests/test_purge.py, and the import is local to keep module import order
    free of a service -> route edge at import time.
    """
    from app.api.routes.crm import _purge_customer_data

    anon_ref = _purge_customer_data(db, customer)
    return 1, anon_ref


def _erase_directory_record(db: Session, customer: Customer, *, action: str) -> int:
    """The `crm_customers` directory row for this principal, matched by the
    HMAC search digest of the email the Customer row carried *before* it was
    anonymised - so this must run before `_erase_principal_personal_data`.
    """
    from app.core.encryption import hmac_digest

    email = (customer.email or "").strip().lower()
    if not email:
        return 0
    rows = (
        db.query(CrmCustomer)
        .filter(CrmCustomer.email_search == hmac_digest(email))
        .all()
    )
    if not rows:
        return 0
    if action == "ERASE":
        count = 0
        for row in rows:
            db.delete(row)
            count += 1
        db.flush()
        return count
    for row in rows:
        row.name = "[anonymised]"
        row.email = f"anon-{uuid.uuid4().hex[:12]}@invalid"
        row.email_search = hmac_digest(row.email)
        row.phone = None
        row.address = None
        row.age = None
        row.consent_preferences = {}
    db.flush()
    return len(rows)


def _erase_consent_contexts(db: Session, customer: Customer) -> int:
    return (
        db.query(ConsentContext)
        .filter(ConsentContext.customer_id == customer.id)
        .delete(synchronize_session="fetch")
    )


def compute_evidence_hash(job: ErasureJob) -> str:
    """SHA-256 over the canonical JSON of what the job actually did.

    Canonical means sorted keys and no whitespace, the same discipline
    app/core/audit_chain.py applies, so the hash is reproducible from the row
    by anyone reading it. The same value is written into the ERASURE_EXECUTED
    audit row, and *that* ledger is hash-chained and immutable - so an
    after-the-fact edit to this table is detectable by recomputing.
    """
    payload = {
        "job_ref": job.job_ref,
        "trigger": job.trigger,
        "trigger_ref": job.trigger_ref,
        "action": job.action,
        "anonymised_ref": job.anonymised_ref,
        "authorised_by": job.authorised_by,
        "authorised_at": _as_utc(job.authorised_at).isoformat() if job.authorised_at else None,
        "notice_sent_at": _as_utc(job.notice_sent_at).isoformat() if job.notice_sent_at else None,
        "notice_hours": job.notice_hours,
        "execute_after": _as_utc(job.execute_after).isoformat() if job.execute_after else None,
        "executed_at": _as_utc(job.executed_at).isoformat() if job.executed_at else None,
        "executed_by": job.executed_by,
        "records_erased": job.records_erased or {},
        "records_retained": job.records_retained or {},
        "processor_alert_ids": sorted(job.processor_alert_ids or []),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def execute_erasure_job(
    db: Session, job: ErasureJob, *, actor_username: str = "system",
    request_id: Optional[str] = None, now: Optional[datetime] = None,
) -> ErasureJob:
    """Perform the erasure this job authorises, and record what it did.

    Refuses, loudly, unless every precondition holds - these are the DoD, as
    guard clauses:

    * the job is still live (not already executed, cancelled or failed);
    * a named actor authorised it;
    * where notice was required, it was actually sent and its period elapsed;
    * no legal hold covers the principal.

    Everything then happens in one transaction, so a failure part-way leaves
    nothing half-erased. The processor fan-out is raised *before* the local
    destruction commits, on purpose: s.8(7)(b) requires causing processors to
    erase too, and an instruction that was never raised because we crashed
    after wiping our own copy is unrecoverable - we would no longer know whom
    to tell.
    """
    now = now or utcnow()

    if job.status == "EXECUTED":
        return job
    if job.status not in ERASURE_JOB_LIVE_STATUSES:
        raise ErasureNotAuthorised(f"Erasure job {job.job_ref} is {job.status}.")
    if not job.authorised_by:
        raise ErasureNotAuthorised(
            f"Erasure job {job.job_ref} has not been authorised. Erasure is irreversible, so it "
            f"is never a silent background act: a named actor must authorise it "
            f"(POST /erasure/jobs/{job.job_ref}/authorise), or - for a withdrawal or an "
            f"approved rights request - the principal's own act supplies the authorisation."
        )
    if job.notice_required:
        if job.notice_sent_at is None:
            raise ErasureNotAuthorised(
                f"Erasure job {job.job_ref} has no pre-erasure notice on record. DPDP Rules "
                f"2025 R.8(2) requires the principal to be told at least "
                f"{job.notice_hours} hours before erasure; no notice, no erasure."
            )
        if job.execute_after is not None and _as_utc(job.execute_after) > now:
            raise ErasureNotAuthorised(
                f"Erasure job {job.job_ref} may not execute before "
                f"{_as_utc(job.execute_after).isoformat()}: the R.8(2) notice period "
                f"({job.notice_hours}h) has not elapsed."
            )

    customer = db.get(Customer, job.customer_id)
    if customer is None:
        raise ErasureError(f"Erasure job {job.job_ref} has no customer row to erase.")

    hold = active_hold_for(db, customer, now=now)
    if hold is not None:
        job.status = "BLOCKED"
        job.hold_id = hold.id
        job.blocked_reason = (
            f"Legal hold {hold.hold_ref} is in force ({hold.legal_basis}); erasure is deferred "
            f"until it is released."
        )
        db.flush()
        log_audit(
            db, "ERASURE_JOB_BLOCKED", actor_username=actor_username, source_app=job.source_app,
            tenant_id=job.tenant_id, customer_id=job.customer_id,
            reason=job.blocked_reason, request_id=request_id,
            metadata={"job_ref": job.job_ref, "hold_ref": hold.hold_ref}, commit=False,
        )
        db.commit()
        db.refresh(job)
        raise ErasureBlocked(job.blocked_reason)

    source_app = job.source_app or customer.source_app or ""
    erased: dict = {}
    retained: dict = {}

    # s.8(7)(b): cause the processors to erase too. Raised first - see the
    # docstring. Fails soft, exactly like the withdrawal fan-out in
    # services/consent.py: our own s.8(7) duty must not be defeated by a
    # third party's outage, and the alerts are idempotent per trigger_ref so
    # they can be re-raised.
    alert_ids: list[int] = []
    try:
        from app.services.processors import raise_erasure_instructions

        alerts = raise_erasure_instructions(
            db, customer=customer, request_ref=job.job_ref,
            purpose_id=None,
            reason=(
                f"Erasure job {job.job_ref} ({job.trigger}): erase this data principal's "
                f"personal data held on our behalf and any copies (DPDP Act s.8(7))."
            ),
            actor_username=actor_username, source_app=source_app, request_id=request_id,
        )
        alert_ids = [a.id for a in alerts]
    except Exception:  # noqa: BLE001 - a processor outage must not defeat s.8(7)
        db.rollback()
        logger.exception(
            "Failed to raise processor erasure instructions for job %s; the local erasure "
            "proceeds and the instructions can be re-raised (they are idempotent per "
            "trigger_ref)", job.job_ref,
        )
        job = db.merge(job)
        customer = db.get(Customer, job.customer_id)

    # Order matters: the directory row is found via the email the Customer row
    # still carries, so it must be handled before the Customer is anonymised.
    for record_class in ("directory_record", "consent_contexts", "notifications",
                         "principal_personal_data"):
        policy = resolve_policy(db, record_class, source_app)
        if policy is not None and not policy.is_active:
            continue
        action = policy.action if policy else (
            "ERASE" if record_class in HARD_DELETABLE_RECORD_CLASSES else "ANONYMISE"
        )
        floor = floor_for_record_class(db, record_class)

        # The floor beats the ceiling, computed rather than assumed: anything
        # newer than the floor cutoff is retained and reported, whatever the
        # policy asked for.
        if floor is not None:
            cutoff = now - timedelta(days=floor)
            if record_class == "notifications":
                within = (
                    db.query(func.count(Notification.id))
                    .filter(
                        Notification.customer_id == customer.id,
                        Notification.created_at > cutoff,
                    )
                    .scalar()
                    or 0
                )
                if within:
                    retained[record_class] = _retained_entry(db, record_class, within, floor)
                    continue

        try:
            if record_class == "principal_personal_data":
                count, anon_ref = _erase_principal_personal_data(db, customer)
                job.anonymised_ref = anon_ref
                erased[record_class] = {"rows": count, "action": "ANONYMISE",
                                        "anonymised_ref": anon_ref}
            elif record_class == "directory_record":
                count = _erase_directory_record(db, customer, action=action)
                erased[record_class] = {"rows": count, "action": action}
            elif record_class == "consent_contexts":
                count = _erase_consent_contexts(db, customer)
                erased[record_class] = {"rows": count, "action": "ERASE"}
            elif record_class == "notifications":
                # Reached only when no notification is inside the floor, which
                # in practice means the principal has none at all.
                erased[record_class] = {"rows": 0, "action": action}
        except RetentionFloorViolation as exc:
            retained[record_class] = {"rows_retained": None, "floor_days": floor,
                                      "why": str(exc)}

    # The audit ledger is append-only (a database trigger blocks UPDATE and
    # DELETE for every role), so "audit anonymisation" cannot mean editing
    # audit rows and this engine does not try. It records instead exactly what
    # survives, so the residue is visible rather than implied: the pre-erasure
    # external id remains on this principal's historical audit rows, under the
    # 1-year floor for the class, and the mapping from the new anonymisation
    # reference back to it is held nowhere else once this job's own audit row
    # ages out of anyone's memory. See docs/compliance/SECURE_DELETION.md.
    from app.models.entities import AuditLog

    audit_rows = (
        db.query(func.count(AuditLog.id)).filter(AuditLog.customer_id == customer.id).scalar() or 0
    )
    retained["audit_logs"] = {
        "rows_retained": audit_rows,
        "floor_days": effective_floor_days(db, "audit_logs"),
        "basis": CLASSES_BY_CODE["audit_logs"].basis,
        "why": (
            "The audit ledger is append-only by construction (a database trigger blocks UPDATE "
            "and DELETE for every role, and the rows are hash-chained per tenant), so audit "
            "rows are never rewritten or removed - anonymisation of the principal happens on "
            "the customers row instead. These rows still carry the pre-erasure external id in "
            "audit_logs.customer_external_id."
        ),
    }

    job.status = "EXECUTED"
    job.executed_at = now
    job.executed_by = actor_username
    job.records_erased = erased
    job.records_retained = retained
    job.processor_alert_ids = alert_ids
    job.evidence_hash = compute_evidence_hash(job)
    db.flush()

    # Into R1-10's own ledger as well, so `GET /retention/scan` sees every
    # destruction this platform performed, from whichever engine performed it.
    db.add(
        RetentionAction(
            tenant_id=job.tenant_id,
            record_class="principal_personal_data",
            action="ANONYMISE" if job.action == "ANONYMISE" else "DELETE",
            cutoff=now,
            floor_days_at_execution=0,
            retention_days_at_execution=0,
            rows_affected=sum(
                int(v.get("rows") or 0) for v in erased.values() if isinstance(v, dict)
            ),
            dry_run=False,
            actor_username=actor_username,
            request_id=request_id,
            reason=f"Erasure job {job.job_ref} ({job.trigger}) executed",
            details={"job_ref": job.job_ref, "erased": erased, "retained": list(retained)},
            executed_at=now,
        )
    )

    log_audit(
        db, "ERASURE_EXECUTED", actor_username=actor_username, source_app=source_app,
        tenant_id=job.tenant_id, customer_id=job.customer_id,
        customer_external_id=job.anonymised_ref,
        reason=(
            f"Erasure job {job.job_ref} executed ({job.trigger}, authorised by "
            f"{job.authorised_by}): "
            + ", ".join(f"{k}={v.get('rows')} {v.get('action')}" for k, v in erased.items())
        ),
        request_id=request_id,
        metadata={
            "job_ref": job.job_ref, "trigger": job.trigger, "trigger_ref": job.trigger_ref,
            "action": job.action, "authorised_by": job.authorised_by,
            "notice_sent_at": _as_utc(job.notice_sent_at).isoformat() if job.notice_sent_at else None,
            "execute_after": _as_utc(job.execute_after).isoformat() if job.execute_after else None,
            "records_erased": erased, "records_retained": retained,
            "processor_alert_ids": alert_ids, "evidence_hash": job.evidence_hash,
        },
        commit=False,
    )
    db.commit()
    db.refresh(job)
    return job


def execute_due_erasure_jobs(
    db: Session, *, limit: int = 100, actor_username: str = "scheduler",
    now: Optional[datetime] = None,
) -> dict:
    """Every authorised, notified job whose notice period has elapsed. Backs
    the `erasure_executor` scheduled job.

    Deliberately blind to PROPOSED jobs: a clock-driven proposal that nobody
    has authorised is never executed by a background pass, however long it has
    been sitting there.
    """
    now = now or utcnow()
    jobs = (
        db.query(ErasureJob)
        .filter(
            ErasureJob.status.in_(("NOTIFIED", "BLOCKED")),
            ErasureJob.authorised_by.isnot(None),
            ErasureJob.execute_after.isnot(None),
            ErasureJob.execute_after <= now,
        )
        .order_by(ErasureJob.execute_after.asc())
        .limit(limit)
        .all()
    )
    executed = blocked = failed = 0
    for job in jobs:
        try:
            execute_erasure_job(db, job, actor_username=actor_username, now=now)
            executed += 1
        except ErasureBlocked:
            blocked += 1
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the sweep
            db.rollback()
            failed += 1
            logger.exception("Erasure job %s failed", job.job_ref)
            try:
                job = db.merge(job)
                job.status = "FAILED"
                job.error = str(exc)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
    return {"candidates": len(jobs), "executed": executed, "blocked": blocked, "failed": failed}


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------
def _customers_query(db: Session, scope: Optional[str]):
    q = db.query(Customer).filter(Customer.status != "ANONYMISED")
    if scope:
        q = q.filter(Customer.source_app == scope)
    return q


def _already_live_job(db: Session, customer_id: int) -> bool:
    return (
        db.query(func.count(ErasureJob.id))
        .filter(
            ErasureJob.customer_id == customer_id,
            ErasureJob.status.in_(ERASURE_JOB_LIVE_STATUSES),
        )
        .scalar()
        or 0
    ) > 0


def erasure_retention_scan(
    db: Session, *, limit: int = 500, actor_username: str = "scheduler",
    now: Optional[datetime] = None, propose: bool = True,
) -> dict:
    """The retention-clock half of the engine: principals whose configured
    `retention_days` has run out since their last interaction.

    Named `erasure_retention_scan`, not `retention_scan`, because R1-10
    already owns that name for a different and complementary thing - a
    read-only re-check that no *record class* was destroyed before its floor
    (app/services/retention.py::retention_scan). This one looks at *people*
    and proposes work. Two scans, two questions, no shared name.

    Proposes; never erases. Each candidate gets a PROPOSED `erasure_jobs` row
    with no authorisation, which a named human must authorise before anything
    can act on it.
    """
    now = now or utcnow()
    ensure_default_policies(db)
    policies = (
        db.query(RetentionPolicy)
        .filter(
            RetentionPolicy.record_class == "principal_personal_data",
            RetentionPolicy.is_active.is_(True),
            RetentionPolicy.retention_days.isnot(None),
        )
        .all()
    )
    proposed = 0
    candidates = 0
    for policy in policies:
        cutoff = now - timedelta(days=policy.retention_days)
        scope = None if policy.scope == "*" else policy.scope
        rows = (
            _customers_query(db, scope)
            .filter(
                or_(
                    Customer.last_interaction_at.is_(None),
                    Customer.last_interaction_at <= cutoff,
                ),
                Customer.created_at <= cutoff,
            )
            .order_by(Customer.id.asc())
            .limit(limit)
            .all()
        )
        for customer in rows:
            candidates += 1
            if not propose or _already_live_job(db, customer.id):
                continue
            propose_erasure_job(
                db, customer, trigger="RETENTION",
                trigger_ref=f"retention:{customer.id}:{cutoff.date().isoformat()}",
                reason=(
                    f"Configured retention period of {policy.retention_days} day(s) for "
                    f"'{policy.record_class}' has elapsed since this principal last "
                    f"interacted (policy {policy.policy_ref})"
                ),
                source_app=customer.source_app or "", actor_username=actor_username,
            )
            proposed += 1
    return {"policies": len(policies), "candidates": candidates, "jobs_proposed": proposed}


def inactivity_scan(
    db: Session, *, limit: int = 500, actor_username: str = "scheduler",
    now: Optional[datetime] = None, propose: bool = True,
) -> dict:
    """R.8(1) read with the Third Schedule: the three-year inactivity clock,
    per tenant class.

    The Third Schedule attaches the clock to specific classes of Data
    Fiduciary (e-commerce with at least two crore registered users, online
    gaming intermediaries with at least fifty lakh, social media
    intermediaries with at least two crore) rather than to everyone, so the
    period is configured per tenant through `retention_policies.scope` +
    `inactivity_days` rather than hard-coded for the whole platform. A tenant
    outside those classes clears `inactivity_days` and no clock runs for it;
    the platform default carries the three years so a Third Schedule tenant is
    covered before anyone configures anything.

    The clock's zero point is `customers.last_interaction_at`, maintained by
    `touch_last_interaction` from the surfaces where the principal is actually
    present. A customer that has never interacted falls back to `created_at`,
    which is the earliest defensible reading of "last approached".

    Proposes; never erases - same reason as `erasure_retention_scan`.
    """
    now = now or utcnow()
    ensure_default_policies(db)
    policies = (
        db.query(RetentionPolicy)
        .filter(
            RetentionPolicy.record_class == "principal_personal_data",
            RetentionPolicy.is_active.is_(True),
            RetentionPolicy.inactivity_days.isnot(None),
        )
        .all()
    )
    proposed = 0
    candidates = 0
    breaches = 0
    for policy in policies:
        cutoff = now - timedelta(days=policy.inactivity_days)
        scope = None if policy.scope == "*" else policy.scope
        rows = (
            _customers_query(db, scope)
            .filter(
                or_(
                    Customer.last_interaction_at <= cutoff,
                    Customer.last_interaction_at.is_(None),
                ),
                Customer.created_at <= cutoff,
            )
            .order_by(Customer.id.asc())
            .limit(limit)
            .all()
        )
        for customer in rows:
            candidates += 1
            if _already_live_job(db, customer.id):
                continue
            # K-31: past the Third Schedule period with neither an erasure in
            # flight nor a legal hold explaining why not.
            if active_hold_for(db, customer, now=now) is None:
                breaches += 1
            if not propose:
                continue
            last = _as_utc(customer.last_interaction_at) or _as_utc(customer.created_at)
            propose_erasure_job(
                db, customer, trigger="INACTIVITY",
                trigger_ref=f"inactivity:{customer.id}:{cutoff.date().isoformat()}",
                reason=(
                    f"The data principal has not approached this fiduciary for the specified "
                    f"purpose, nor exercised her rights, since "
                    f"{last.isoformat() if last else 'record creation'} - more than "
                    f"{policy.inactivity_days} days (DPDP Rules 2025 R.8(1) read with the "
                    f"Third Schedule; policy {policy.policy_ref})"
                ),
                source_app=customer.source_app or "", actor_username=actor_username,
            )
            proposed += 1
    return {
        "policies": len(policies), "candidates": candidates, "jobs_proposed": proposed,
        "inactivity_clock_breaches": breaches,
    }


# ---------------------------------------------------------------------------
# Metrics (G-08 / K-28..K-31)
# ---------------------------------------------------------------------------
def erasure_metrics(db: Session, *, now: Optional[datetime] = None) -> dict:
    """The four numbers G-08 asks for, computed rather than asserted."""
    now = now or utcnow()
    rows = db.query(ErasureJob).all()
    executed = [j for j in rows if j.status == "EXECUTED"]
    backlog = [j for j in rows if j.status in ERASURE_JOB_LIVE_STATUSES]

    tats = [
        (_as_utc(j.executed_at) - _as_utc(j.created_at)).total_seconds() / 3600.0
        for j in executed
        if j.executed_at and j.created_at
    ]
    notices_ok = sum(
        1 for j in executed
        if not j.notice_required
        or (
            j.notice_sent_at is not None
            and j.executed_at is not None
            and (_as_utc(j.executed_at) - _as_utc(j.notice_sent_at))
            >= timedelta(hours=j.notice_hours)
        )
    )
    inactivity = inactivity_scan(db, propose=False, now=now)
    return {
        "generated_at": now,
        # K-28: records past retention awaiting erasure.
        "erasure_backlog": len(backlog),
        "backlog_by_status": {
            s: sum(1 for j in backlog if j.status == s) for s in ERASURE_JOB_LIVE_STATUSES
        },
        "jobs_executed": len(executed),
        # K-29: turnaround, proposal to execution.
        "median_tat_hours": round(sorted(tats)[len(tats) // 2], 2) if tats else None,
        # K-30: pre-erasure notices given at least the required period before.
        # None, not 100.0, when no erasure has executed yet - "nothing has
        # happened" is not "every notice was compliant".
        "executed_with_compliant_notice": notices_ok,
        "notice_compliance_pct": (
            round(notices_ok / len(executed) * 100, 2) if executed else None
        ),
        # K-31: principals past the Third Schedule period with no erasure in
        # flight and no legal hold.
        "inactivity_clock_breaches": inactivity["inactivity_clock_breaches"],
        "legal_holds_active": (
            db.query(func.count(LegalHold.id)).filter(LegalHold.is_active.is_(True)).scalar() or 0
        ),
    }
