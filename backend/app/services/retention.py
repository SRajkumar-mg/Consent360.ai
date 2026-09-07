"""R1-10 (S-05, D-08): record and log retention floors.

A retention *floor* is a minimum holding period: the record may not be
destroyed before it elapses. That is the opposite of the more familiar
retention *ceiling* (s.8(7)'s "erase as soon as the purpose is served"), and
both apply at once - the evidence of a consent has to outlive the processing
it authorised, so an audit two years later can still be answered.

Three pieces make the floor real rather than aspirational:

1. `RETENTION_CLASSES` - the floors themselves, in code. They are statutory
   minima, so they are deliberately NOT rows an operator can edit; a floor
   someone can lower from an admin screen is not a floor.
2. `retention_schedules` (models/entities.py::RetentionSchedule) - the period
   the operator has actually chosen per class, which `set_retention_days`
   refuses to accept below the floor. Raising it is always allowed.
3. `enforce_retention` - the ONLY sanctioned deletion path, which refuses a
   cutoff inside the floor and writes a `retention_actions` ledger row for
   every run, dry or wet. `retention_scan` then re-checks every ledger row
   against the floor that was in force when it ran, so "zero records deleted
   before their floor" is a computed result, not a claim.

What this does NOT do, stated plainly: it cannot detect a deletion made
outside `enforce_retention` - a DBA with a psql prompt, an ORM cascade, a
`DROP TABLE`. Two things narrow that gap rather than closing it. `audit_logs`
is protected at the database level (an UPDATE/DELETE trigger for every role,
plus the hash chain, see app/core/audit_chain.py), so the one class that
matters most is genuinely tamper-evident. For every other class the scan
reports `id_sequence_gaps` - ids missing from the table's monotonic sequence -
as a *signal*, explicitly not as evidence: rolled-back inserts burn sequence
values too, so a non-zero gap count means "worth a look", never "rows were
deleted".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.artefacts import ConsentArtefact, ConsentArtefactEvent
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentDecisionLog,
    ConsentEvidence,
    ConsentHistory,
    ConsentReceipt,
    DataSharingEvent,
    Notice,
    NoticeVersion,
    Notification,
    ProcessorAlert,
    RetentionAction,
    RetentionSchedule,
)
from app.services.audit import log_audit

logger = logging.getLogger("app.retention")

DAYS_1_YEAR = 365
DAYS_7_YEARS = 2555  # 7 * 365; the Rules speak in years, this is the day count used throughout
CERT_IN_MINIMUM_DAYS = 180


class RetentionFloorViolation(Exception):
    """Raised when a caller asks to destroy records inside their floor."""


@dataclass(frozen=True)
class RetentionClass:
    code: str
    label: str
    floor_days: int
    basis: str
    #: None for a class whose records do not live in this database.
    model: Optional[type] = None
    timestamp_column: Optional[str] = None
    #: "table" | "external"
    storage: str = "table"
    #: For an overlay class: the class codes whose floor this one raises.
    covers: tuple[str, ...] = ()
    #: Whether `enforce_retention` may actually issue a DELETE for this class.
    deletable: bool = False
    undeletable_reason: str = ""
    notes: str = ""


# The order here is the order the scan and the API report in.
RETENTION_CLASSES: list[RetentionClass] = [
    RetentionClass(
        code="consents",
        label="Consent records",
        floor_days=DAYS_1_YEAR,
        basis="DPDP Act s.6/s.8(7) evidence of a valid consent; minimum 1 year.",
        model=Consent, timestamp_column="created_at",
        deletable=False,
        undeletable_reason=(
            "A consent row is the parent of its own history, evidence and receipts, all of "
            "which have their own floors. Deleting it would orphan records that are still "
            "inside their retention period, so this class is retained and superseded, never "
            "deleted, by design."
        ),
    ),
    RetentionClass(
        code="consent_history",
        label="Consent lifecycle history",
        floor_days=DAYS_1_YEAR,
        basis="DPDP Act s.6(6)/s.8(7) proof of when each transition occurred; minimum 1 year.",
        model=ConsentHistory, timestamp_column="created_at",
        deletable=True,
    ),
    RetentionClass(
        code="consent_evidence",
        label="Consent evidence",
        floor_days=DAYS_1_YEAR,
        basis="DPDP Act s.6(1) proof the consent was free, specific, informed and unambiguous; minimum 1 year.",
        model=ConsentEvidence, timestamp_column="collected_at",
        deletable=False,
        undeletable_reason=(
            "Referenced by consent_receipts.evidence_id, whose own floor may not yet have "
            "elapsed; deleting evidence would leave an issued receipt unable to prove what it "
            "attests to."
        ),
    ),
    RetentionClass(
        code="consent_receipts",
        label="Consent receipts",
        floor_days=DAYS_1_YEAR,
        basis="ISO/IEC TS 27560 receipt issued to the principal; minimum 1 year.",
        model=ConsentReceipt, timestamp_column="issued_at",
        deletable=True,
    ),
    RetentionClass(
        code="audit_logs",
        label="Audit ledger",
        floor_days=DAYS_1_YEAR,
        basis="DPDP Act s.8(4)/(5) accountability trail; minimum 1 year.",
        model=AuditLog, timestamp_column="created_at",
        deletable=False,
        undeletable_reason=(
            "The audit ledger is append-only by construction: a database trigger blocks UPDATE "
            "and DELETE for every role and the rows are hash-chained per tenant. There is no "
            "retention-driven deletion path for it at any age, which is the strongest possible "
            "form of this floor."
        ),
    ),
    RetentionClass(
        code="consent_decision_logs",
        label="Decision-engine outcomes",
        floor_days=DAYS_1_YEAR,
        basis="Evidence that processing was gated on a live consent decision; minimum 1 year.",
        model=ConsentDecisionLog, timestamp_column="evaluated_at",
        deletable=True,
    ),
    # CM-03: this used to sit at the general s.8(2)/s.11 1-year floor below.
    # DPDP Rules 2025 First Schedule Part B 4(c) separately requires a
    # Consent Manager to keep its own record of what was shared with a data
    # fiduciary for at least 7 years - the same rule that gives
    # consent_artefacts/consent_artefact_events their 7-year floor just below
    # - so this is an unconditional 7-year floor of its own, not raised only
    # while the consent_manager_records overlay happens to be switched on.
    # Unlike that overlay's four classes, `deletable` stays True: nothing else
    # in this schema references a data_sharing_events row by foreign key, so
    # there is no orphaning risk from a genuine delete once the floor elapses.
    RetentionClass(
        code="data_sharing_events",
        label="Disclosure log",
        floor_days=DAYS_7_YEARS,
        basis=(
            "DPDP Act s.8(2)/s.11 record of who data was shared with, PLUS DPDP Rules 2025 "
            "First Schedule Part B 4(c): a Consent Manager's own record of what was shared with "
            "a data fiduciary must be retained at least 7 years - unconditionally, not only "
            "while the consent_manager_records overlay is active."
        ),
        model=DataSharingEvent, timestamp_column="occurred_at",
        deletable=True,
    ),
    RetentionClass(
        code="processor_alerts",
        label="Processor instructions and acknowledgements",
        floor_days=DAYS_1_YEAR,
        basis="DPDP Act s.6(6) proof processors were caused to cease; minimum 1 year.",
        model=ProcessorAlert, timestamp_column="created_at",
        deletable=True,
    ),
    RetentionClass(
        code="notifications",
        label="Principal and operational notifications",
        floor_days=DAYS_1_YEAR,
        basis="DPDP Act s.5/s.8(6) proof a required communication was sent; minimum 1 year.",
        model=Notification, timestamp_column="created_at",
        deletable=True,
    ),
    # R3-10: Consent Manager artefacts. Unlike the four consent record classes
    # the overlay below covers, these two do NOT have a 1-year floor that is
    # conditionally raised - a consent artefact only exists at all because a
    # Consent Manager is involved, so First Schedule Part B 3/4(c)'s 7 years is
    # their own, unconditional floor and survives the overlay being switched
    # off. The table list is reconciled against
    # app/models/artefacts.py::RETENTION_FLOOR_CLASSES (that module declares
    # the requirement; this one owns the mechanism) by
    # tests/test_retention_and_evidence_pack.py, so the two cannot drift.
    RetentionClass(
        code="consent_artefacts",
        label="Consent Manager artefacts",
        floor_days=DAYS_7_YEARS,
        basis=(
            "DPDP Rules 2025 First Schedule Part B 3: a Consent Manager must retain the record "
            "of consents it has enabled for 7 years."
        ),
        model=ConsentArtefact, timestamp_column="created_at",
        deletable=False,
        undeletable_reason=(
            "An artefact is the parent of its own signed event chain "
            "(consent_artefact_events) and of the links to the consents it stands for; deleting "
            "it would orphan events that are themselves still inside a 7-year floor."
        ),
    ),
    RetentionClass(
        code="consent_artefact_events",
        label="Consent Manager artefact events",
        floor_days=DAYS_7_YEARS,
        basis=(
            "DPDP Rules 2025 First Schedule Part B 4(c): the Consent Manager's record of every "
            "consent given, denied or withdrawn through it, retained 7 years."
        ),
        model=ConsentArtefactEvent, timestamp_column="occurred_at",
        deletable=False,
        undeletable_reason=(
            "Each event is an immutable, HMAC-signed link in an artefact's version chain. "
            "Removing one breaks the chain it exists to prove, which is the same reason "
            "audit_logs is never deleted."
        ),
    ),
    # CM-03: the notice side of First Schedule Part B 3/4(c) - "records of
    # consents, notices and sharing" - had no retention class at all before
    # this. Same reasoning as consent_artefacts/consent_artefact_events just
    # above: a Notice/NoticeVersion is not merely raised to 7 years while a
    # deployment happens to be a registered Consent Manager, it is one of the
    # things Part B names outright, so - like the artefact classes - this is
    # its own unconditional floor rather than a fifth class added to the
    # consent_manager_records overlay's `covers`.
    RetentionClass(
        code="notices",
        label="Notice documents",
        floor_days=DAYS_7_YEARS,
        basis=(
            "DPDP Rules 2025 First Schedule Part B 3/4(c): the record of the notices given to a "
            "Data Principal must be retained for at least 7 years."
        ),
        model=Notice, timestamp_column="created_at",
        deletable=False,
        undeletable_reason=(
            "A Notice is the parent of its own published versions (notice_versions), which carry "
            "the actual evidentiary content and are themselves inside a 7-year floor; deleting "
            "the parent would orphan them."
        ),
    ),
    RetentionClass(
        code="notice_versions",
        label="Notice versions (evidentiary content)",
        floor_days=DAYS_7_YEARS,
        basis=(
            "DPDP Rules 2025 First Schedule Part B 3/4(c): the notice actually shown to a Data "
            "Principal - the substance a granted consent evidences agreement to - must be "
            "retained for at least 7 years."
        ),
        model=NoticeVersion, timestamp_column="created_at",
        deletable=False,
        undeletable_reason=(
            "Referenced by consents.notice_version_id: deleting a published notice version would "
            "leave a consent unable to prove which notice it was given under (A-07/D-04)."
        ),
    ),
    RetentionClass(
        code="consent_manager_records",
        label="Consent Manager records (overlay)",
        floor_days=DAYS_7_YEARS,
        basis=(
            "DPDP Rules 2025, Consent Manager obligations: a Consent Manager must retain consent "
            "records for 7 years. Applied as an overlay that raises the floor of the consent "
            "record classes it covers."
        ),
        model=None, timestamp_column=None, storage="overlay",
        covers=("consents", "consent_history", "consent_evidence", "consent_receipts"),
        notes=(
            "Active by default, which is the fail-safe direction: it only ever makes the "
            "platform retain longer. A deployment that is definitively not operating as a "
            "registered Consent Manager may deactivate this class in retention_schedules, "
            "which drops the covered classes back to their own 1-year floors."
        ),
    ),
    RetentionClass(
        code="access_logs",
        label="Access logs",
        floor_days=DAYS_1_YEAR,
        basis=(
            "CERT-In Directions 2022 require ICT system logs to be maintained for a rolling "
            "180 days within India; this platform sets a 1-year floor, which is the stricter "
            "of the two."
        ),
        model=None, timestamp_column=None, storage="external",
        notes=(
            "Access logs are emitted to stdout/a rotating file by app/core/access_log.py, not "
            "stored in a table, so this floor is not enforceable by a database scan. Its "
            "enforcement point is LOG_RETENTION_DAYS in app/core/config.py (default 366) plus "
            "the operator's log shipping and rotation. Reported here so the schedule is "
            "complete and the gap is visible rather than absent."
        ),
    ),
    RetentionClass(
        code="application_logs",
        label="Application logs",
        floor_days=DAYS_1_YEAR,
        basis="CERT-In Directions 2022 (180-day minimum); 1-year floor applied, held in India.",
        model=None, timestamp_column=None, storage="external",
        notes=(
            "Same enforcement point and same limitation as access_logs: LOG_RETENTION_DAYS and "
            "the operator's log pipeline, not this database."
        ),
    ),
]

CLASSES_BY_CODE = {c.code: c for c in RETENTION_CLASSES}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
def _active_overlay_codes(db: Session) -> set[str]:
    rows = {
        r.record_class: r.is_active
        for r in db.query(RetentionSchedule).all()
    }
    return {
        c.code for c in RETENTION_CLASSES
        if c.storage == "overlay" and rows.get(c.code, True)
    }


def effective_floor_days(db: Session, record_class: str) -> int:
    """The floor actually in force for a class: its own, raised by any active
    overlay class that covers it."""
    cls = CLASSES_BY_CODE.get(record_class)
    if cls is None:
        raise KeyError(f"Unknown retention record class: {record_class}")
    floor = cls.floor_days
    for overlay_code in _active_overlay_codes(db):
        overlay = CLASSES_BY_CODE[overlay_code]
        if record_class in overlay.covers:
            floor = max(floor, overlay.floor_days)
    return floor


def ensure_schedule_rows(db: Session, *, commit: bool = True) -> list[RetentionSchedule]:
    """Idempotently create a `retention_schedules` row per class, seeded at
    the class's own floor (or the overlay floor where one applies). Called by
    the migration and safe to call again at runtime."""
    existing = {r.record_class: r for r in db.query(RetentionSchedule).all()}
    created = []
    for cls in RETENTION_CLASSES:
        if cls.code in existing:
            continue
        floor = cls.floor_days
        for overlay in RETENTION_CLASSES:
            if overlay.storage == "overlay" and cls.code in overlay.covers:
                floor = max(floor, overlay.floor_days)
        # `basis`/`notes` are deliberately left empty here: the statutory
        # basis for each class lives in RETENTION_CLASSES above and is what
        # get_schedule() reports, so writing a copy into the row would create
        # two sources of truth that can drift. Those columns exist for an
        # operator's own annotation.
        row = RetentionSchedule(
            record_class=cls.code, retention_days=floor, is_active=True, updated_by="system",
        )
        db.add(row)
        created.append(row)
    if commit:
        db.commit()
    return created


def get_schedule(db: Session) -> list[dict]:
    ensure_schedule_rows(db)
    rows = {r.record_class: r for r in db.query(RetentionSchedule).all()}
    out = []
    for cls in RETENTION_CLASSES:
        row = rows.get(cls.code)
        floor = effective_floor_days(db, cls.code)
        configured = row.retention_days if row else floor
        out.append({
            "record_class": cls.code,
            "label": cls.label,
            "storage": cls.storage,
            "table": cls.model.__tablename__ if cls.model is not None else None,
            "floor_days": cls.floor_days,
            "effective_floor_days": floor,
            "configured_retention_days": configured,
            "is_active": row.is_active if row else True,
            "schedule_meets_floor": configured >= floor,
            "deletable": cls.deletable,
            "undeletable_reason": cls.undeletable_reason,
            "basis": cls.basis,
            "notes": cls.notes,
            "covers": list(cls.covers),
        })
    return out


def set_retention_days(
    db: Session, record_class: str, retention_days: int, *,
    actor_username: str = "system", request_id: Optional[str] = None,
) -> RetentionSchedule:
    """Raise (or restate) a class's retention period. Refuses anything below
    the floor in force - that refusal is the whole point of the table."""
    if record_class not in CLASSES_BY_CODE:
        raise KeyError(f"Unknown retention record class: {record_class}")
    ensure_schedule_rows(db)
    floor = effective_floor_days(db, record_class)
    if retention_days < floor:
        raise RetentionFloorViolation(
            f"Retention for '{record_class}' cannot be set to {retention_days} days: the floor "
            f"in force is {floor} days ({CLASSES_BY_CODE[record_class].basis})"
        )
    row = db.query(RetentionSchedule).filter(RetentionSchedule.record_class == record_class).one()
    previous = row.retention_days
    row.retention_days = retention_days
    row.updated_by = actor_username
    db.flush()
    log_audit(
        db, "RETENTION_SCHEDULE_UPDATED", actor_username=actor_username,
        reason=f"Retention for '{record_class}' set to {retention_days} days (was {previous})",
        request_id=request_id,
        metadata={"record_class": record_class, "previous_days": previous,
                  "retention_days": retention_days, "floor_days": floor},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


def resync_schedule_floors(
    db: Session, *, actor_username: str = "system", request_id: Optional[str] = None,
) -> list[RetentionSchedule]:
    """Raise any already-seeded schedule row whose configured period now sits
    below its class's current effective floor, up to that floor. Never
    lowers anything - the same "raising is always allowed" invariant
    `set_retention_days` enforces for one deliberate write, applied here
    across every class at once.

    Why this exists, and why it is a function an operator calls rather than
    something `ensure_schedule_rows`/`retention_scan` do automatically: a
    `retention_schedules` row is seeded once (by a migration, or by
    `ensure_schedule_rows` the first time a class is seen) at whatever floor
    was in force *then*. When a floor is later raised in code - CM-03 raised
    `data_sharing_events` from 1 year to 7 - an already-seeded row does not
    move on its own, and it must not: `retention_scan`'s SCHEDULE_BELOW_FLOOR
    check exists precisely to catch a schedule sitting below its floor,
    whichever reason that happened for, and silently correcting it inside
    the read path would defeat the exact property
    tests/test_retention_and_evidence_pack.py::
    test_the_scan_reports_a_schedule_that_falls_below_a_raised_floor proves.

    So the fix is the same idiom `seed.py` already uses to re-sync
    `roles.permissions` from `app/core/rbac.py` on every run: an explicit,
    idempotent, audited re-sync a human runs (`python seed.py`, which calls
    this) after deploying a code change that raises a floor - not a side
    effect of reading the schedule.
    """
    ensure_schedule_rows(db)
    rows = {r.record_class: r for r in db.query(RetentionSchedule).all()}
    updated: list[RetentionSchedule] = []
    for cls in RETENTION_CLASSES:
        row = rows.get(cls.code)
        if row is None:
            continue
        floor = effective_floor_days(db, cls.code)
        if row.retention_days < floor:
            previous = row.retention_days
            row.retention_days = floor
            row.updated_by = actor_username
            db.flush()
            log_audit(
                db, "RETENTION_SCHEDULE_UPDATED", actor_username=actor_username,
                reason=(
                    f"Retention for '{cls.code}' raised from {previous} to {floor} days: the "
                    f"statutory floor was raised in code and the seeded schedule had not yet "
                    f"caught up"
                ),
                request_id=request_id,
                metadata={"record_class": cls.code, "previous_days": previous,
                          "retention_days": floor, "floor_days": floor},
                commit=False,
            )
            updated.append(row)
    if updated:
        db.commit()
        for row in updated:
            db.refresh(row)
    return updated


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def _table_exists(db: Session, table_name: str) -> bool:
    """Checked with the inspector rather than by letting a query fail: a failed
    statement aborts the transaction, and every subsequent statement on the
    same Session then fails too - so a single not-yet-migrated table would take
    the whole scan down rather than one class of it. This platform's models are
    spread across several modules that land with their own migrations, so a
    database legitimately at an older revision is a state the scan has to
    survive and report, not crash on."""
    from sqlalchemy import inspect as sa_inspect

    return sa_inspect(db.get_bind()).has_table(table_name)


def _class_counts(db: Session, cls: RetentionClass, floor_cutoff: datetime) -> dict:
    column = getattr(cls.model, cls.timestamp_column)
    total = db.query(func.count(cls.model.id)).scalar() or 0
    within_floor = db.query(func.count(cls.model.id)).filter(column > floor_cutoff).scalar() or 0
    oldest = db.query(func.min(column)).scalar()
    newest = db.query(func.max(column)).scalar()
    max_id = db.query(func.max(cls.model.id)).scalar() or 0
    min_id = db.query(func.min(cls.model.id)).scalar() or 0
    # Gap heuristic: how many ids in [min_id, max_id] are absent. Sequence
    # values burned by rolled-back inserts land here too, so this is a signal
    # to look, never proof of deletion - see the module docstring.
    span = (max_id - min_id + 1) if total else 0
    gaps = max(span - total, 0)
    return {
        "rows_total": total,
        "rows_within_floor": within_floor,
        "rows_past_floor": total - within_floor,
        "oldest_record_at": _as_utc(oldest) if oldest else None,
        "newest_record_at": _as_utc(newest) if newest else None,
        "id_sequence_gaps": gaps,
    }


def retention_scan(db: Session, *, actor_username: str = "system",
                   request_id: Optional[str] = None, audit: bool = True) -> dict:
    """The scan the DoD asks for: report whether any record was destroyed
    before its floor.

    `deleted_before_floor` counts rows destroyed by a recorded retention
    action whose cutoff fell inside the floor in force at the time. It is zero
    when the only deletion path is `enforce_retention`, which refuses exactly
    that - so a non-zero value means either the guard was bypassed or a floor
    was raised after the fact, and either way the offending action is named in
    `violations`.
    """
    now = utcnow()
    ensure_schedule_rows(db)
    schedule_rows = {r.record_class: r for r in db.query(RetentionSchedule).all()}

    classes = []
    violations: list[dict] = []
    deleted_before_floor = 0

    for cls in RETENTION_CLASSES:
        floor = effective_floor_days(db, cls.code)
        row = schedule_rows.get(cls.code)
        configured = row.retention_days if row else floor
        floor_cutoff = now - timedelta(days=floor)

        entry = {
            "record_class": cls.code,
            "label": cls.label,
            "storage": cls.storage,
            "table": cls.model.__tablename__ if cls.model is not None else None,
            "floor_days": cls.floor_days,
            "effective_floor_days": floor,
            "configured_retention_days": configured,
            "schedule_meets_floor": configured >= floor,
            "deletable": cls.deletable,
            "rows_total": None,
            "rows_within_floor": None,
            "rows_past_floor": None,
            "oldest_record_at": None,
            "newest_record_at": None,
            "id_sequence_gaps": None,
            "retention_actions": 0,
            "rows_deleted_before_floor": 0,
            "enforcement": (
                "database scan" if cls.model is not None
                else ("overlay - raises the floor of " + ", ".join(cls.covers)) if cls.storage == "overlay"
                else "LOG_RETENTION_DAYS + operator log pipeline (NOT verifiable from this database)"
            ),
            "notes": cls.notes,
        }

        if configured < floor:
            v = {
                "record_class": cls.code, "kind": "SCHEDULE_BELOW_FLOOR",
                "detail": f"configured retention {configured}d is below the {floor}d floor",
            }
            violations.append(v)

        if cls.model is not None:
            if _table_exists(db, cls.model.__tablename__):
                entry.update(_class_counts(db, cls, floor_cutoff))
            else:
                # Not a violation: the floor still stands, there is simply
                # nothing here yet to measure it against.
                entry["enforcement"] = (
                    f"table {cls.model.__tablename__} is not present on this database "
                    f"(migrations not applied) - nothing to scan"
                )

        actions = (
            db.query(RetentionAction)
            .filter(RetentionAction.record_class == cls.code, RetentionAction.dry_run.is_(False))
            .all()
        )
        entry["retention_actions"] = len(actions)
        for action in actions:
            # Compare against the floor recorded at execution time AND the
            # floor in force now: a floor that has since been raised makes an
            # old action non-compliant going forward, which an auditor needs
            # to see even though nobody did anything wrong at the time.
            cutoff = _as_utc(action.cutoff)
            executed = _as_utc(action.executed_at)
            floor_then = action.floor_days_at_execution
            if cutoff > executed - timedelta(days=floor_then):
                deleted_before_floor += action.rows_affected
                entry["rows_deleted_before_floor"] += action.rows_affected
                violations.append({
                    "record_class": cls.code, "kind": "DELETED_BEFORE_FLOOR",
                    "retention_action_id": action.id, "rows_affected": action.rows_affected,
                    "detail": (
                        f"action {action.id} used cutoff {cutoff.isoformat()} against a "
                        f"{floor_then}-day floor at {executed.isoformat()}"
                    ),
                })
            elif floor > floor_then and cutoff > executed - timedelta(days=floor):
                violations.append({
                    "record_class": cls.code, "kind": "FLOOR_RAISED_AFTER_DELETION",
                    "retention_action_id": action.id, "rows_affected": action.rows_affected,
                    "detail": (
                        f"action {action.id} was compliant with the {floor_then}-day floor then "
                        f"in force, but the floor is now {floor} days"
                    ),
                })
        classes.append(entry)

    result = {
        "generated_at": now,
        "deleted_before_floor": deleted_before_floor,
        "violations": violations,
        "compliant": deleted_before_floor == 0 and not violations,
        "classes_scanned": len(classes),
        "classes": classes,
    }
    if audit:
        log_audit(
            db, "RETENTION_SCAN_RUN", actor_username=actor_username,
            reason=(
                f"Retention scan across {len(classes)} record classes: "
                f"{deleted_before_floor} record(s) deleted before their floor, "
                f"{len(violations)} violation(s)"
            ),
            request_id=request_id,
            metadata={"deleted_before_floor": deleted_before_floor,
                      "violations": len(violations), "compliant": result["compliant"]},
        )
    return result


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
def enforce_retention(
    db: Session,
    record_class: str,
    *,
    cutoff: Optional[datetime] = None,
    dry_run: bool = True,
    actor_username: str = "system",
    request_id: Optional[str] = None,
    reason: str = "",
) -> dict:
    """The only sanctioned destruction path for a record class.

    Refuses, loudly, when `cutoff` falls inside the floor in force - that is
    the enforcement the DoD's "floor that nothing enforces is not a floor"
    demands. `cutoff` defaults to `now - configured retention`, which is
    always at or before the floor, so the default is safe.

    `dry_run=True` (the default) counts what would be destroyed and records
    the run in the ledger without touching a row, so an operator can see the
    blast radius before authorising it.
    """
    cls = CLASSES_BY_CODE.get(record_class)
    if cls is None:
        raise KeyError(f"Unknown retention record class: {record_class}")

    ensure_schedule_rows(db)
    floor = effective_floor_days(db, record_class)
    row = db.query(RetentionSchedule).filter(RetentionSchedule.record_class == record_class).one()
    configured = row.retention_days
    now = utcnow()
    latest_permitted_cutoff = now - timedelta(days=floor)
    cutoff = _as_utc(cutoff) if cutoff else now - timedelta(days=configured)

    if cutoff > latest_permitted_cutoff:
        raise RetentionFloorViolation(
            f"Refusing to apply retention to '{record_class}' with cutoff "
            f"{cutoff.isoformat()}: the {floor}-day floor in force permits nothing newer than "
            f"{latest_permitted_cutoff.isoformat()}. ({cls.basis})"
        )

    if cls.model is None:
        raise RetentionFloorViolation(
            f"'{record_class}' has no table in this database ({cls.storage}); its retention is "
            f"governed outside the application. {cls.notes}"
        )
    if not _table_exists(db, cls.model.__tablename__):
        raise RetentionFloorViolation(
            f"'{record_class}' maps to table {cls.model.__tablename__}, which is not present on "
            f"this database - apply the migrations before enforcing retention on it."
        )
    if not cls.deletable and not dry_run:
        raise RetentionFloorViolation(
            f"'{record_class}' is never deleted by this platform. {cls.undeletable_reason}"
        )

    column = getattr(cls.model, cls.timestamp_column)
    matched = db.query(func.count(cls.model.id)).filter(column <= cutoff).scalar() or 0

    rows_affected = 0
    if not dry_run and matched:
        # synchronize_session="fetch", not False: a bulk DELETE that does not
        # tell the Session what it removed leaves already-loaded instances of
        # those rows in the identity map, so a caller that still holds one
        # sees a live object for a row that no longer exists (and a refresh of
        # it raises ObjectDeletedError). "fetch" costs one extra SELECT of the
        # matching primary keys and is well worth it on an operation whose
        # entire purpose is that the rows are really gone.
        rows_affected = (
            db.query(cls.model)
            .filter(column <= cutoff)
            .delete(synchronize_session="fetch")
        )

    action = RetentionAction(
        record_class=record_class,
        action="DELETE",
        cutoff=cutoff,
        floor_days_at_execution=floor,
        retention_days_at_execution=configured,
        rows_affected=rows_affected if not dry_run else matched,
        dry_run=dry_run,
        actor_username=actor_username,
        request_id=request_id,
        reason=reason or f"Scheduled retention enforcement for {record_class}",
        details={"matched": matched, "table": cls.model.__tablename__},
    )
    db.add(action)
    db.flush()
    log_audit(
        db, "RETENTION_ACTION_EXECUTED", actor_username=actor_username,
        reason=(
            f"Retention {'dry run' if dry_run else 'DELETE'} on '{record_class}' with cutoff "
            f"{cutoff.isoformat()}: {matched} row(s) matched, {rows_affected} deleted"
        ),
        request_id=request_id,
        metadata={"record_class": record_class, "cutoff": cutoff.isoformat(),
                  "floor_days": floor, "retention_days": configured,
                  "matched": matched, "rows_deleted": rows_affected, "dry_run": dry_run,
                  "retention_action_id": action.id},
        commit=False,
    )
    db.commit()
    return {
        "record_class": record_class,
        "cutoff": cutoff,
        "floor_days": floor,
        "retention_days": configured,
        "matched": matched,
        "rows_deleted": rows_affected,
        "dry_run": dry_run,
        "retention_action_id": action.id,
    }
