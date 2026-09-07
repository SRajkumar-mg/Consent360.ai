"""R1-10 (S-05, D-08): retention floors and the regulator evidence pack.

Definition of done: "Retention scan reports zero records deleted before their
floor; evidence pack generated for a chosen period." Both halves are exercised
here against real rows, including the negative case - a hand-written ledger
entry recording a deletion inside its floor, which the scan must actually
catch. A scan that can only ever report zero is not a check.

The wet-run deletion tests deliberately operate on `consent_decision_logs`
rows this file backdates past the one-year floor. Nothing else in the suite
creates a decision log that old, so a real DELETE here cannot take another
test's data with it.

As with tests/test_processor_propagation.py, the HTTP tests mount the
retention router on a local app because `app/main.py` does not yet register it
(that registration is the coordinating agent's change).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.entities import (
    AuditLog,
    ConsentDecisionLog,
    Notice,
    NoticeVersion,
    Purpose,
    RetentionAction,
    RetentionSchedule,
)
from app.services import retention as retention_service
from app.services.evidence_pack import build_evidence_pack
from app.services.retention import (
    DAYS_1_YEAR,
    DAYS_7_YEARS,
    RetentionFloorViolation,
    effective_floor_days,
    enforce_retention,
    ensure_schedule_rows,
    get_schedule,
    resync_schedule_floors,
    retention_scan,
    set_retention_days,
)
from app.services.tenancy import resolve_tenant_id


@pytest.fixture(autouse=True)
def _schedule_matches_current_floors(db):
    """CM-03 raised `data_sharing_events` (and added `notices`/
    `notice_versions`) to a 7-year floor. `retention_schedules` rows already
    seeded by an earlier migration (`a7c1e9d4b302`, at the 1-year floor that
    was correct when it was written) do not move on their own - see
    `resync_schedule_floors`'s own docstring for why that must stay true.
    Every test in this module is written against a schedule that already
    reflects the current floors (the same state `python seed.py` produces on
    a real deployment after this change lands), so this brings a freshly
    migrated test database to that state once, the same way seed.py would."""
    resync_schedule_floors(db, actor_username="test-fixture")


@pytest.fixture()
def retention_client(db):
    from app.api.routes import retention as retention_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(retention_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _retention_customer(db):
    """One reusable principal for the decision-log fixtures. Resolved through
    services/tenancy.py::resolve_customer rather than a raw query - `name` is
    an encrypted column and cannot be matched by equality, and the raw
    `*_search` shapes are exactly what tests/test_customer_resolution_guard.py
    forbids."""
    from app.models.entities import Customer
    from app.services.tenancy import resolve_customer

    source_app = "RETENTION_TEST"
    customer = resolve_customer(
        db, source_app=source_app, external_id="R110-CUST-RETENTION-001"
    )
    if customer is None:
        customer = Customer(
            external_id="R110-CUST-RETENTION-001", name="Retention Fixture",
            source_app=source_app, tenant_id=resolve_tenant_id(db, source_app),
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
    return customer


def _old_decision_log(db, *, days_old: int, outcome="ALLOW"):
    """A decision log backdated past the floor, so a retention pass may
    legitimately remove it."""
    row = ConsentDecisionLog(
        customer_id=_retention_customer(db).id, purpose_id=None, decision=outcome,
        reason="retention test fixture", source_app="RETENTION_TEST",
        evaluated_at=datetime.now(timezone.utc) - timedelta(days=days_old),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# The schedule
# --------------------------------------------------------------------------- #
def test_the_schedule_carries_every_floor_the_task_names(db):
    schedule = {row["record_class"]: row for row in get_schedule(db)}

    # consents / evidence / history / audit: at least 1 year.
    for code in ("consents", "consent_evidence", "consent_history", "audit_logs"):
        assert schedule[code]["floor_days"] >= DAYS_1_YEAR, code

    # Consent Manager records: 7 years, applied as an overlay that raises the
    # floor of the four consent record classes it covers.
    overlay = schedule["consent_manager_records"]
    assert overlay["floor_days"] == DAYS_7_YEARS
    assert set(overlay["covers"]) == {
        "consents", "consent_history", "consent_evidence", "consent_receipts"
    }
    for code in overlay["covers"]:
        assert schedule[code]["effective_floor_days"] == DAYS_7_YEARS, code

    # Application and access logs: 1 year in India, above CERT-In's 180-day
    # minimum, and honest that this database cannot enforce it.
    for code in ("access_logs", "application_logs"):
        assert schedule[code]["floor_days"] == DAYS_1_YEAR
        assert schedule[code]["storage"] == "external"
        assert "180" in schedule[code]["basis"] or "CERT-In" in schedule[code]["basis"]
        assert "LOG_RETENTION_DAYS" in schedule[code]["notes"]

    assert all(row["configured_retention_days"] >= row["effective_floor_days"]
               for row in schedule.values())


def test_consent_manager_artefacts_carry_the_seven_year_floor(db):
    """R3-10 declared the requirement in app/models/artefacts.py rather than
    building a competing retention mechanism; R1-10 owns the mechanism. This is
    the reconciliation that stops the two drifting: every table that module
    declares as a Consent Manager record must actually appear in the retention
    register at a 7-year floor."""
    from app.models.artefacts import RETENTION_FLOOR_CLASSES
    from app.services.retention import DAYS_7_YEARS, RETENTION_CLASSES

    seven_year = {c.code for c in RETENTION_CLASSES if c.floor_days == DAYS_7_YEARS}
    missing = set(RETENTION_FLOOR_CLASSES) - seven_year
    assert not missing, (
        "app/models/artefacts.py::RETENTION_FLOOR_CLASSES declares these as 7-year Consent "
        f"Manager records, but app/services/retention.py has no 7-year class for them: {missing}"
    )

    schedule = {row["record_class"]: row for row in get_schedule(db)}
    for code in RETENTION_FLOOR_CLASSES:
        row = schedule[code]
        assert row["effective_floor_days"] == DAYS_7_YEARS, code
        assert row["configured_retention_days"] >= DAYS_7_YEARS, code
        # These two are the parent of, respectively, a signed event chain and
        # the chain itself - deleting either breaks what it exists to prove.
        assert row["deletable"] is False, code
        assert "First Schedule Part B" in row["basis"], code

    # And the floor is enforced, not just declared.
    for code in RETENTION_FLOOR_CLASSES:
        with pytest.raises(RetentionFloorViolation):
            enforce_retention(
                db, code, cutoff=datetime.now(timezone.utc) - timedelta(days=800), dry_run=True
            )


def test_cm03_sharing_and_notice_records_carry_the_seven_year_floor(db):
    """CM-03: DPDP Rules 2025 First Schedule Part B 3/4(c) require a Consent
    Manager to retain records of consents, notices AND sharing for at least
    7 years. `consent_artefacts`/`consent_artefact_events` already carry that
    floor (R3-10, tested just above); `data_sharing_events` sat at the
    general 1-year floor and `notices`/`notice_versions` had no retention
    class at all. This is the fix, checked the same way the artefact classes
    are: declared at 7 years, unconditionally (not merely while the
    consent_manager_records overlay happens to be active), and enforced."""
    cm03_classes = ("data_sharing_events", "notices", "notice_versions")

    classes_by_code = {c.code: c for c in retention_service.RETENTION_CLASSES}
    for code in cm03_classes:
        assert classes_by_code[code].floor_days == DAYS_7_YEARS, code

    # Unconditional: still 7 years even with the overlay switched off, and
    # none of the three are covered BY the overlay (that would make the
    # floor conditional on it, exactly what CM-03 says not to do).
    overlay = classes_by_code["consent_manager_records"]
    for code in cm03_classes:
        assert code not in overlay.covers, code

    schedule = {row["record_class"]: row for row in get_schedule(db)}
    for code in cm03_classes:
        row = schedule[code]
        assert row["effective_floor_days"] == DAYS_7_YEARS, code
        assert row["configured_retention_days"] >= DAYS_7_YEARS, code
        assert "First Schedule Part B" in row["basis"], code

    # notices/notice_versions are parent/child evidentiary content, retained
    # like consent_artefacts/consent_artefact_events; data_sharing_events has
    # no such child and stays deletable, unlike the artefact classes.
    assert schedule["notices"]["deletable"] is False
    assert schedule["notice_versions"]["deletable"] is False
    assert schedule["data_sharing_events"]["deletable"] is True

    # Enforced, not just declared: a deletion attempt inside the 7-year floor
    # is refused, and the refusal names the floor.
    for code in cm03_classes:
        with pytest.raises(RetentionFloorViolation) as exc:
            enforce_retention(
                db, code, cutoff=datetime.now(timezone.utc) - timedelta(days=800), dry_run=True,
            )
        assert "2555" in str(exc.value) or "7" in str(exc.value)

    # And a WET delete is refused outright for the two non-deletable classes,
    # however old the cutoff - they are never destroyed by this engine.
    for code in ("notices", "notice_versions"):
        with pytest.raises(RetentionFloorViolation) as exc:
            enforce_retention(
                db, code, cutoff=datetime.now(timezone.utc) - timedelta(days=4000), dry_run=False,
            )
        assert "never deleted" in str(exc.value)


def test_cm03_a_wet_delete_of_sharing_events_past_the_floor_is_permitted(db):
    """`data_sharing_events` is deletable=True (unlike the artefact classes),
    so - unlike notices/notice_versions above - a wet run well past the new
    7-year floor must actually be allowed to proceed, proving the class was
    raised to 7 years rather than accidentally made undeletable too."""
    from app.core.encryption import hmac_digest
    from app.models.entities import Customer, DataSharingEvent, Processor, PurposeVersion

    tenant_id = resolve_tenant_id(db, "CM03_SHARING_TEST")
    customer = Customer(
        external_id="CM03-SHARE-1", external_id_search=hmac_digest("CM03-SHARE-1"),
        name="CM03 Fixture", source_app="CM03_SHARING_TEST", tenant_id=tenant_id,
    )
    # is_active=False: this Purpose exists only to give DataSharingEvent a
    # valid purpose_id FK and must not surface in /portal/overview's
    # unscoped `Purpose.is_active.is_(True)` listing for some OTHER
    # customer's session later in the test run. It still needs a real
    # current PurposeVersion regardless - a Purpose with none is a state
    # this product does not support (services/consent.py::
    # get_current_purpose_version raises a 500 for it), so leaving it
    # versionless would be its own latent bug even though it is inactive.
    purpose = Purpose(name="CM03 sharing purpose", code="cm03_sharing_purpose",
                       legal_basis="CONSENT", is_active=False)
    processor = Processor(name="CM03 Processor")
    db.add_all([customer, purpose, processor])
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[], processing_activity_ids=[],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.flush()
    old_event = DataSharingEvent(
        tenant_id=tenant_id, customer_id=customer.id, processor_id=processor.id,
        purpose_id=purpose.id, event_type="SENT", signature="x",
        occurred_at=datetime.now(timezone.utc) - timedelta(days=DAYS_7_YEARS + 30),
    )
    recent_event = DataSharingEvent(
        tenant_id=tenant_id, customer_id=customer.id, processor_id=processor.id,
        purpose_id=purpose.id, event_type="SENT", signature="x",
        occurred_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db.add_all([old_event, recent_event])
    db.commit()
    old_id, recent_id = old_event.id, recent_event.id

    result = enforce_retention(
        db, "data_sharing_events", dry_run=False, actor_username="dpo",
        cutoff=datetime.now(timezone.utc) - timedelta(days=DAYS_7_YEARS),
        reason="CM-03 test: retention pass past the 7-year floor",
    )
    assert result["rows_deleted"] >= 1
    assert (
        db.query(DataSharingEvent.id).filter(DataSharingEvent.id == old_id).first() is None
    ), "past the 7-year floor: removable"
    assert (
        db.query(DataSharingEvent.id).filter(DataSharingEvent.id == recent_id).first() is not None
    ), "inside the 7-year floor: must survive"

    scan = retention_scan(db)
    assert scan["deleted_before_floor"] == 0
    assert scan["compliant"] is True


def test_cm03_the_scan_catches_a_sharing_event_deletion_inside_the_floor(db):
    """The negative case CM-03 asks to be proven, not assumed: a deletion
    that bypassed enforce_retention and used a cutoff inside the (new,
    7-year) data_sharing_events floor must be caught and named by the scan -
    mirroring test_the_scan_actually_catches_a_deletion_inside_the_floor for
    consent_decision_logs, at the CM-03 floor instead of the 1-year one."""
    now = datetime.now(timezone.utc)
    bad = RetentionAction(
        record_class="data_sharing_events", action="DELETE",
        cutoff=now - timedelta(days=400),   # well inside the 7-year (2555-day) floor
        floor_days_at_execution=DAYS_7_YEARS,
        retention_days_at_execution=DAYS_7_YEARS,
        rows_affected=3, dry_run=False, actor_username="rogue-script",
        reason="simulates a deletion that bypassed enforce_retention",
        executed_at=now,
    )
    db.add(bad)
    db.commit()
    try:
        scan = retention_scan(db)
        assert scan["deleted_before_floor"] == 3
        assert scan["compliant"] is False
        violation = next(v for v in scan["violations"] if v.get("retention_action_id") == bad.id)
        assert violation["kind"] == "DELETED_BEFORE_FLOOR"
        assert violation["rows_affected"] == 3
        assert violation["record_class"] == "data_sharing_events"
    finally:
        db.delete(bad)
        db.commit()

    assert retention_scan(db)["compliant"] is True


def test_resync_schedule_floors_raises_a_stale_seed_and_nothing_else(db):
    """The migration that first seeded `retention_schedules` wrote
    data_sharing_events at the 1-year floor that was correct then; CM-03
    raised the floor in code without touching that row (retention_scan's
    whole job is to catch exactly that kind of drift, so nothing may
    silently fix it on read - see resync_schedule_floors's own docstring).
    `resync_schedule_floors` is the explicit, audited, seed.py-driven fix:
    it must raise a below-floor row and never touch one that already meets
    its floor."""
    from app.services.retention import resync_schedule_floors

    ensure_schedule_rows(db)
    row = db.query(RetentionSchedule).filter(
        RetentionSchedule.record_class == "consent_decision_logs"
    ).one()
    untouched_before = row.retention_days

    stale = db.query(RetentionSchedule).filter(
        RetentionSchedule.record_class == "data_sharing_events"
    ).one()
    stale.retention_days = DAYS_1_YEAR
    db.commit()

    updated = resync_schedule_floors(db, actor_username="test-resync")

    assert "data_sharing_events" in {r.record_class for r in updated}
    db.refresh(stale)
    assert stale.retention_days == DAYS_7_YEARS

    db.refresh(row)
    assert row.retention_days == untouched_before, "a row already at its floor must not move"

    event = (
        db.query(AuditLog)
        .filter(AuditLog.event == "RETENTION_SCHEDULE_UPDATED")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert event is not None
    assert "data_sharing_events" in event.reason


def test_the_scan_survives_a_class_whose_table_is_not_migrated_yet(db, monkeypatch):
    """Models in this platform land across several modules, each with its own
    migration, so a database legitimately sitting at an older revision is a
    state the scan has to report rather than crash on - and it must not report
    a missing table as a floor violation."""
    import app.services.retention as retention_module

    real = retention_module._table_exists

    def _pretend_missing(db_, table_name):
        return False if table_name == "consent_artefacts" else real(db_, table_name)

    monkeypatch.setattr(retention_module, "_table_exists", _pretend_missing)
    scan = retention_scan(db, audit=False)
    row = next(c for c in scan["classes"] if c["record_class"] == "consent_artefacts")
    assert row["rows_total"] is None
    assert "not present on this database" in row["enforcement"]
    assert scan["compliant"] is True, "a missing table is not a retention violation"

    with pytest.raises(RetentionFloorViolation) as exc:
        enforce_retention(db, "consent_artefacts",
                          cutoff=datetime.now(timezone.utc) - timedelta(days=4000), dry_run=True)
    assert "not present on this database" in str(exc.value)


def test_the_backup_policy_is_referenced_not_duplicated():
    """R3-12 already wrote the backup policy and the residency flags; R1-10
    must build on them rather than state a second, conflicting policy."""
    from pathlib import Path

    from app.core.config import get_settings

    backend_dir = Path(__file__).resolve().parents[1]
    policy = (backend_dir / "docs" / "compliance" / "BACKUP_POLICY.md").read_text()
    assert "BACKUP_RETENTION_DAYS" in policy
    assert "DATA_RESIDENCY_ASSERT_INDIA_ONLY" in policy

    settings = get_settings()
    for flag in (
        "BACKUP_ENCRYPTION_ENABLED", "BACKUP_RETENTION_DAYS",
        "DATA_RESIDENCY_PRIMARY_DB_REGION", "DATA_RESIDENCY_BACKUP_REGION",
        "DATA_RESIDENCY_ASSERT_INDIA_ONLY", "LOG_RETENTION_DAYS",
    ):
        assert hasattr(settings, flag), flag

    schedule_doc = backend_dir / "docs" / "compliance" / "RETENTION_SCHEDULE.md"
    assert schedule_doc.exists()
    text = schedule_doc.read_text()
    assert "BACKUP_POLICY.md" in text, "the retention schedule must point at the existing policy"


def test_retention_cannot_be_set_below_the_floor(db):
    ensure_schedule_rows(db)
    floor = effective_floor_days(db, "audit_logs")
    with pytest.raises(RetentionFloorViolation) as exc:
        set_retention_days(db, "audit_logs", floor - 1)
    assert str(floor) in str(exc.value)

    row = db.query(RetentionSchedule).filter(RetentionSchedule.record_class == "audit_logs").one()
    assert row.retention_days >= floor, "the refused write must not have landed"


def test_retention_can_be_raised_above_the_floor(db):
    ensure_schedule_rows(db)
    floor = effective_floor_days(db, "notifications")
    updated = set_retention_days(db, "notifications", floor + 30, actor_username="dpo")
    assert updated.retention_days == floor + 30
    assert updated.updated_by == "dpo"
    # Put it back so later tests see the seeded value.
    set_retention_days(db, "notifications", floor)


def test_unknown_record_class_is_rejected(db):
    with pytest.raises(KeyError):
        set_retention_days(db, "not_a_real_class", 400)
    with pytest.raises(KeyError):
        enforce_retention(db, "not_a_real_class")


# --------------------------------------------------------------------------- #
# Enforcement
# --------------------------------------------------------------------------- #
def test_enforcement_refuses_a_cutoff_inside_the_floor(db):
    inside = datetime.now(timezone.utc) - timedelta(days=10)
    with pytest.raises(RetentionFloorViolation) as exc:
        enforce_retention(db, "consent_decision_logs", cutoff=inside, dry_run=True)
    assert "floor" in str(exc.value).lower()
    assert not (
        db.query(RetentionAction)
        .filter(RetentionAction.record_class == "consent_decision_logs",
                RetentionAction.cutoff == inside)
        .count()
    ), "a refused action must not leave a ledger row"


def _decision_log_exists(db, row_id: int) -> bool:
    """Query by id rather than Session.get: a wet run detaches the instances
    it removed, and Session.get would try to refresh the detached object
    instead of answering the question."""
    return (
        db.query(ConsentDecisionLog.id).filter(ConsentDecisionLog.id == row_id).first() is not None
    )


def test_dry_run_counts_without_deleting(db):
    old_id = _old_decision_log(db, days_old=400).id
    before = db.query(ConsentDecisionLog).count()

    result = enforce_retention(db, "consent_decision_logs", dry_run=True, actor_username="dpo")
    assert result["dry_run"] is True
    assert result["matched"] >= 1
    assert result["rows_deleted"] == 0
    assert db.query(ConsentDecisionLog).count() == before
    assert _decision_log_exists(db, old_id)

    action = db.get(RetentionAction, result["retention_action_id"])
    assert action.dry_run is True
    assert action.floor_days_at_execution == effective_floor_days(db, "consent_decision_logs")


def test_a_wet_run_deletes_only_past_the_floor_and_the_scan_stays_clean(db):
    old_id = _old_decision_log(db, days_old=500).id
    recent_id = _old_decision_log(db, days_old=5).id

    result = enforce_retention(
        db, "consent_decision_logs", dry_run=False, actor_username="dpo",
        reason="R1-10 test: scheduled retention pass",
    )
    assert result["rows_deleted"] >= 1
    assert not _decision_log_exists(db, old_id), "past the floor: removable"
    assert _decision_log_exists(db, recent_id), "inside the floor: must survive"

    scan = retention_scan(db)
    assert scan["deleted_before_floor"] == 0
    assert scan["violations"] == []
    assert scan["compliant"] is True
    row = next(c for c in scan["classes"] if c["record_class"] == "consent_decision_logs")
    assert row["rows_deleted_before_floor"] == 0
    assert row["retention_actions"] >= 1


def test_the_scan_actually_catches_a_deletion_inside_the_floor(db):
    """The negative case. Without this, "zero deleted before their floor"
    could just be a constant."""
    now = datetime.now(timezone.utc)
    bad = RetentionAction(
        record_class="consent_decision_logs", action="DELETE",
        cutoff=now - timedelta(days=30),          # well inside the 365-day floor
        floor_days_at_execution=DAYS_1_YEAR,
        retention_days_at_execution=DAYS_1_YEAR,
        rows_affected=7, dry_run=False, actor_username="rogue-script",
        reason="simulates a deletion that bypassed enforce_retention",
        executed_at=now,
    )
    db.add(bad)
    db.commit()
    try:
        scan = retention_scan(db)
        assert scan["deleted_before_floor"] == 7
        assert scan["compliant"] is False
        violation = next(
            v for v in scan["violations"]
            if v.get("retention_action_id") == bad.id
        )
        assert violation["kind"] == "DELETED_BEFORE_FLOOR"
        assert violation["rows_affected"] == 7
    finally:
        db.delete(bad)
        db.commit()

    assert retention_scan(db)["compliant"] is True


def test_the_audit_ledger_is_never_deletable(db):
    with pytest.raises(RetentionFloorViolation) as exc:
        enforce_retention(
            db, "audit_logs", cutoff=datetime.now(timezone.utc) - timedelta(days=4000),
            dry_run=False,
        )
    assert "never deleted" in str(exc.value)
    row = next(c for c in retention_scan(db)["classes"] if c["record_class"] == "audit_logs")
    assert row["deletable"] is False


def test_consent_records_are_not_deleted_because_their_children_are_retained(db):
    for record_class in ("consents", "consent_evidence"):
        with pytest.raises(RetentionFloorViolation) as exc:
            enforce_retention(
                db, record_class, cutoff=datetime.now(timezone.utc) - timedelta(days=4000),
                dry_run=False,
            )
        assert "never deleted" in str(exc.value)


def test_a_class_with_no_table_cannot_be_enforced_from_here(db):
    with pytest.raises(RetentionFloorViolation) as exc:
        enforce_retention(
            db, "access_logs", cutoff=datetime.now(timezone.utc) - timedelta(days=4000),
            dry_run=True,
        )
    assert "no table" in str(exc.value)
    assert "LOG_RETENTION_DAYS" in str(exc.value)


def test_the_scan_reports_a_schedule_that_falls_below_a_raised_floor(db):
    """A floor raised after the fact must surface as a violation rather than
    silently make an old configuration look compliant."""
    ensure_schedule_rows(db)
    row = (
        db.query(RetentionSchedule)
        .filter(RetentionSchedule.record_class == "data_sharing_events")
        .one()
    )
    original = row.retention_days
    # Write below the floor directly, bypassing set_retention_days - exactly
    # the state the scan exists to detect.
    row.retention_days = 30
    db.commit()
    try:
        scan = retention_scan(db)
        assert scan["compliant"] is False
        assert any(
            v["kind"] == "SCHEDULE_BELOW_FLOOR" and v["record_class"] == "data_sharing_events"
            for v in scan["violations"]
        )
    finally:
        row.retention_days = original
        db.commit()


# --------------------------------------------------------------------------- #
# Evidence pack
# --------------------------------------------------------------------------- #
def _seed_ledger_row(db):
    """The pack's ledger extract must have something to extract; seed one row
    rather than relying on another test having written one."""
    from app.services.audit import log_audit

    log_audit(db, "CONSENT_VIEWED", actor_username="retention-test",
              source_app="RETENTION_TEST", reason="evidence pack fixture")


def test_evidence_pack_for_a_chosen_period(db):
    _seed_ledger_row(db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    pack = build_evidence_pack(
        db, period_start=start, period_end=end, generated_by="auditor",
    )

    assert pack["pack_type"] == "DPDP_REGULATOR_EVIDENCE_PACK"
    assert pack["period_start"] == start
    assert pack["period_end"] == end
    assert set(pack["sections"]) >= {
        "ledger_extract", "notice_versions", "request_logs",
        "breach_register", "retention_actions", "integrity_proofs",
    }

    ledger = pack["sections"]["ledger_extract"]
    assert ledger["status"] == "OK"
    assert ledger["total_in_period"] > 0
    assert all("entry_hash" in row for row in ledger["records"])

    retention = pack["sections"]["retention_actions"]
    assert retention["status"] == "OK"
    assert "scan" in retention
    assert retention["scan"]["deleted_before_floor"] == 0


def test_the_breach_register_section_reports_the_real_register(db):
    """R3-08 exists now, so the pack must show the register rather than keep
    reporting it unavailable - which would hide from a regulator the very
    filings they came to read."""
    _seed_ledger_row(db)
    end = datetime.now(timezone.utc)
    pack = build_evidence_pack(db, period_start=end - timedelta(days=30), period_end=end)

    breach = pack["sections"]["breach_register"]
    assert breach["status"] == "OK"
    assert isinstance(breach["records"], list)
    assert breach["count"] == len(breach["records"])
    assert "s.8(6)" in breach["basis"]
    assert "breach_register" not in pack["unavailable_sections"]

    # Whatever the register holds, the pack must reproduce it exactly.
    from app.services.breach import breach_register

    assert breach["records"] == breach_register(
        db, source_app=None, period_start=pack["period_start"], period_end=pack["period_end"]
    )


def test_a_breach_register_that_cannot_be_read_degrades_instead_of_reporting_nil(db, monkeypatch):
    """The honesty property that mattered before R3-08 landed still has to
    hold: a register this pack cannot reach must never be rendered as an empty
    list, because an empty list is now a positive claim that no breach was
    detected in the period."""
    import builtins

    real_import = builtins.__import__

    def _fail_on_breach(name, *args, **kwargs):
        if name == "app.services.breach":
            raise ImportError("simulated: breach register unreachable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_on_breach)
    end = datetime.now(timezone.utc)
    pack = build_evidence_pack(
        db, period_start=end - timedelta(days=30), period_end=end, audit=False
    )
    monkeypatch.undo()

    breach = pack["sections"]["breach_register"]
    assert breach["status"] == "UNAVAILABLE"
    assert breach["records"] is None, "must not be an empty list"
    assert "MUST NOT be read as an assertion that there were none" in breach["reason"]
    assert "breach_register" in pack["unavailable_sections"]
    assert "NOT a finding of nil" in pack["completeness_note"]


def test_a_breach_recorded_in_the_period_appears_in_the_pack(db):
    """The section is only worth anything if a real breach actually shows up
    in it, with its clocks and its filings."""
    from app.models.breach import Breach

    _seed_ledger_row(db)
    now = datetime.now(timezone.utc)
    breach = Breach(
        breach_ref="BR-EVIDENCE-PACK-001",
        source_app="RETENTION_TEST",
        title="Evidence pack fixture breach",
        status="DETECTED",   # see app/models/breach.py::BREACH_STATUSES
        severity="HIGH",
        occurred_at=now - timedelta(days=3),
        detected_at=now - timedelta(days=2),
        aware_at=now - timedelta(days=2),
        affected_count=42,
    )
    db.add(breach)
    db.commit()
    try:
        pack = build_evidence_pack(
            db, period_start=now - timedelta(days=30), period_end=now, audit=False
        )
        section = pack["sections"]["breach_register"]
        row = next(r for r in section["records"] if r["breach_ref"] == "BR-EVIDENCE-PACK-001")
        assert row["status"] == "DETECTED"
        assert row["severity"] == "HIGH"
        assert row["affected_principals"] == 42
        assert "clocks" in row and "filings" in row
    finally:
        db.delete(breach)
        db.commit()


def test_the_request_log_declares_its_own_coverage(db):
    """The request log covers every register this build actually keeps, and
    says so from the registers themselves rather than from a fixed sentence.

    This assertion used to read `status == "PARTIAL"` and
    `"rights-request table" in coverage` - the pack was announcing that no
    rights-request table existed. Both registers now exist, so the section is
    complete and the prose has to reflect that.
    """
    end = datetime.now(timezone.utc)
    pack = build_evidence_pack(db, period_start=end - timedelta(days=30), period_end=end)
    requests = pack["sections"]["request_logs"]

    assert set(requests["records"]) == {
        "rights_events", "objections", "grievances", "rights_requests"
    }
    assert requests["missing_sources"] == []
    assert requests["status"] == "OK"
    assert "request_logs" not in pack["partial_sections"]
    # The coverage statement names what it actually covers.
    for phrase in ("rights-request register", "grievance register", "objection register"):
        assert phrase in requests["coverage"], phrase
    # ...and no longer claims either register is absent.
    assert "no dedicated" not in requests["coverage"].lower()


def test_the_request_log_reports_a_source_it_could_not_read_rather_than_an_empty_list(db, monkeypatch):
    """Honest degradation still works in the other direction: a register that
    cannot be read is NAMED, and is given no record list at all - because an
    empty list in a regulator pack asserts "there were none"."""
    from app.services import evidence_pack as pack_module

    monkeypatch.setattr(pack_module, "_grievance_records", lambda *a, **k: None)
    end = datetime.now(timezone.utc)
    pack = build_evidence_pack(
        db, period_start=end - timedelta(days=30), period_end=end, audit=False
    )
    requests = pack["sections"]["request_logs"]

    assert requests["status"] == "PARTIAL"
    assert "request_logs" in pack["partial_sections"]
    assert "grievances" not in requests["records"], (
        "an unreadable register must carry no list, not an empty one"
    )
    assert any("grievance" in m for m in requests["missing_sources"])
    assert "could not be read" in requests["coverage"]
    assert "not a finding that no such request exists" in requests["coverage"]


def test_the_request_log_reports_records_from_both_registers(db):
    """The bug this replaces: the section queried neither register, so a pack
    handed to a regulator omitted every grievance and every rights request
    while still calling itself the data-principal request log."""
    from app.core.encryption import hmac_digest
    from app.models.entities import Customer
    from app.models.grievance import Grievance
    from app.models.rights import RightsRequest
    from app.services import grievance as grievance_service
    from app.services import rights_requests as rights_service

    now = datetime.now(timezone.utc)
    customer = Customer(
        external_id="EPACK-REQ-1", external_id_search=hmac_digest("EPACK-REQ-1"),
        name="Evidence Pack Principal", email="epack-req-1@example.com",
        email_search=hmac_digest("epack-req-1@example.com"), source_app="EPACK_REQ",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    grievance, _ = grievance_service.create_grievance(
        db, customer=customer, category="ACCESS_REQUEST",
        description="You never answered my access request.",
        source_app="EPACK_REQ", actor_username="principal",
    )
    db.commit()
    request, _ = rights_service.create_request(
        db, customer=customer, request_type="ACCESS",
        request_detail="Send me my data.", source_app="EPACK_REQ",
        actor_username="principal",
    )

    pack = build_evidence_pack(
        db, period_start=now - timedelta(days=1), period_end=now + timedelta(days=1),
        audit=False,
    )
    section = pack["sections"]["request_logs"]

    refs = {g["reference_no"] for g in section["records"]["grievances"]}
    assert grievance.reference_no in refs
    rrefs = {r["reference_no"] for r in section["records"]["rights_requests"]}
    assert request.reference_no in rrefs
    assert section["counts"]["grievances"] >= 1
    assert section["counts"]["rights_requests"] >= 1

    # The narrative never leaves either register through this pack.
    blob = json.dumps(section, default=str)
    assert "You never answered my access request." not in blob
    assert "Send me my data." not in blob

    db.query(RightsRequest).filter(RightsRequest.customer_id == customer.id).delete()
    db.query(Grievance).filter(Grievance.customer_id == customer.id).delete()
    db.commit()


# --------------------------------------------------------------------------- #
#  The staleness tripwire
#
#  Five times now this codebase has shipped prose that was TRUE WHEN WRITTEN
#  and silently became false: a script name that was never created, a
#  "not yet wired" docstring on a wired function, the breach register still
#  reporting UNAVAILABLE after it was built, a CI drift check that only ran
#  `pip show alembic`, and this section announcing that no rights-request
#  table existed after two registers had landed.
#
#  A placeholder has an expiry date and nothing in a codebase tracks when it
#  passes - so the fix is not to write more careful placeholders, it is to
#  make the filesystem itself the trigger. The compliance dashboard's
#  `_MODULE_WATCH` (tests/test_kpi_dashboard.py) does this for KPIs; this is
#  the evidence pack's equivalent, and it is deliberately stronger: a section
#  that merely stops saying UNAVAILABLE while still not querying the register
#  is exactly the failure that happened here, so status alone is not enough.
# --------------------------------------------------------------------------- #

#: module that must exist -> (pack section, the records key that proves the
#: section actually queried it, a word its coverage prose must now name)
_SECTION_SOURCE_WATCH = {
    "app/models/rights.py": ("request_logs", "rights_requests", "rights-request register"),
    "app/models/grievance.py": ("request_logs", "grievances", "grievance register"),
    "app/services/breach.py": ("breach_register", None, None),
    "app/services/retention.py": ("retention_actions", None, None),
}


def test_a_section_stops_claiming_an_absence_the_moment_its_register_lands(db):
    """When a watched module appears on disk, the section that is meant to
    report it must (a) not be UNAVAILABLE, (b) actually carry that register's
    records key, and (c) name it in its own coverage prose.

    (b) is the one that matters. The bug this test exists for had the section
    sitting at PARTIAL - not UNAVAILABLE - while querying neither register, so
    a status-only check would have passed it.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    end = datetime.now(timezone.utc)
    pack = build_evidence_pack(
        db, period_start=end - timedelta(days=30), period_end=end, audit=False
    )

    failures = []
    for module, (section_name, records_key, coverage_word) in _SECTION_SOURCE_WATCH.items():
        if not (backend / module).exists():
            continue
        section = pack["sections"].get(section_name)
        if section is None:
            failures.append(f"{module} exists but the pack has no '{section_name}' section")
            continue
        if section["status"] == "UNAVAILABLE":
            failures.append(
                f"{module} exists, so '{section_name}' can be produced, but the pack still "
                f"reports it UNAVAILABLE: {section.get('reason')}"
            )
            continue
        if records_key is not None:
            records = section.get("records") or {}
            if records_key not in records:
                failures.append(
                    f"{module} exists but '{section_name}' carries no '{records_key}' records "
                    f"key - the section is not actually querying that register, which is "
                    f"exactly the failure this test exists for (it has "
                    f"{sorted(records)})"
                )
        if coverage_word is not None:
            prose = f"{section.get('coverage', '')} {section.get('basis', '')}"
            if coverage_word not in prose:
                failures.append(
                    f"{module} exists but '{section_name}' coverage prose never mentions "
                    f"{coverage_word!r}; re-derive it from what the section now covers"
                )

    assert not failures, (
        "A register this evidence pack is meant to report has landed, but the pack has not "
        "caught up. A regulator pack that omits a register it could read - or that keeps "
        "announcing an absence that has been filled - is the exact misrepresentation "
        "app/services/evidence_pack.py's module docstring forbids in both directions:\n  "
        + "\n  ".join(failures)
    )


def test_the_section_watch_list_is_not_stale(db):
    """Keeps the tripwire itself honest: an entry naming a module that does not
    exist is either a typo or an aspiration, and either way it silently
    watches nothing."""
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    missing = [m for m in _SECTION_SOURCE_WATCH if not (backend / m).exists()]
    assert not missing, (
        "These watched modules do not exist, so their entries guard nothing. Fix the path, "
        f"or drop the entry: {missing}"
    )


def test_integrity_proofs_use_the_existing_hash_chain(db):
    from app.core.audit_chain import verify_chain

    # A chain head is only citable once the tenant has at least one audit row;
    # seed one rather than depending on another test having written it.
    _seed_ledger_row(db)
    end = datetime.now(timezone.utc)
    # audit=False: an audited export appends its own EVIDENCE_PACK_EXPORTED row
    # to the ledger, so the pack's own entry count would no longer match a
    # verify_chain run made after it.
    pack = build_evidence_pack(
        db, period_start=end - timedelta(days=30), period_end=end, audit=False
    )
    proofs = pack["sections"]["integrity_proofs"]

    expected = verify_chain(db)
    assert "verify_chain" in proofs["audit_chain"]["mechanism"]
    # The pack must report exactly what verify_chain reports - not a
    # second opinion, and not an assumption that the chain is intact
    # (tests/test_audit_chain.py deliberately tampers with a row to prove
    # detection works, and this test must survive running after it).
    assert proofs["audit_chain"]["entries_checked"] == expected["checked"]
    assert proofs["audit_chain"]["broken_links"] == expected["broken"]
    assert proofs["audit_chain"]["intact"] is (not expected["broken"])
    # Every tenant's chain head must be citable, and must be that tenant's
    # actual last row. `head_entry_hash` can legitimately be None for a legacy
    # row written before the hash chain existed (verify_chain skips those) -
    # what matters is that the pack names the right row.
    assert proofs["chain_heads"], "every tenant's chain head must be citable"
    for head in proofs["chain_heads"]:
        actual = (
            db.query(AuditLog)
            .filter(AuditLog.tenant_id == head["tenant_id"])
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert head["head_audit_log_id"] == actual.id
        assert head["head_entry_hash"] == actual.entry_hash
    assert any(h["head_entry_hash"] for h in proofs["chain_heads"])

    assert len(proofs["pack_manifest_hash"]) == 64
    assert proofs["pack_manifest_signature"]


def test_the_pack_manifest_hash_is_reproducible_and_tamper_evident(db):
    import hashlib
    import json

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    pack = build_evidence_pack(db, period_start=start, period_end=end, audit=False)

    sections = [
        pack["sections"][name] for name in
        ("ledger_extract", "notice_versions", "request_logs", "breach_register", "retention_actions")
    ]
    recomputed = hashlib.sha256(
        json.dumps(sections, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert recomputed == pack["sections"]["integrity_proofs"]["pack_manifest_hash"]

    sections[0]["records"].append({"id": 999999, "event": "FABRICATED"})
    tampered = hashlib.sha256(
        json.dumps(sections, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert tampered != recomputed


def test_the_pack_period_actually_filters(db):
    _seed_ledger_row(db)
    end = datetime.now(timezone.utc)
    wide = build_evidence_pack(db, period_start=end - timedelta(days=365), period_end=end, audit=False)
    empty = build_evidence_pack(
        db, period_start=end - timedelta(days=3650), period_end=end - timedelta(days=3600),
        audit=False,
    )
    assert wide["sections"]["ledger_extract"]["total_in_period"] > 0
    assert empty["sections"]["ledger_extract"]["total_in_period"] == 0
    assert empty["sections"]["ledger_extract"]["records"] == []
    # An empty window is now a real nil return from a register that exists -
    # an empty list, not a null. "No breach detected in this window" and "we
    # cannot report at all" are different statements, and the pack has to be
    # able to make the first one.
    assert empty["sections"]["breach_register"]["status"] == "OK"
    assert empty["sections"]["breach_register"]["records"] == []


def test_an_inverted_period_is_rejected(db):
    end = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        build_evidence_pack(db, period_start=end, period_end=end - timedelta(days=1))


def test_the_pack_export_is_itself_audited(db):
    end = datetime.now(timezone.utc)
    before = db.query(AuditLog).filter(AuditLog.event == "EVIDENCE_PACK_EXPORTED").count()
    build_evidence_pack(
        db, period_start=end - timedelta(days=7), period_end=end, generated_by="auditor",
    )
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event == "EVIDENCE_PACK_EXPORTED")
        .order_by(AuditLog.id.desc())
        .all()
    )
    assert len(rows) == before + 1
    from app.core.audit_chain import verify_chain

    assert rows[0].actor_username == "auditor"
    assert rows[0].details["audit_chain_intact"] is (not verify_chain(db)["broken"])
    # Nothing is unavailable now that R3-08's register exists.
    assert rows[0].details["unavailable_sections"] == []


def test_notice_versions_in_force_during_the_period_are_included(db):
    from app.models.entities import PurposeVersion

    purpose = Purpose(name="Notice purpose", code="retention_notice_p", legal_basis="CONSENT")
    db.add(purpose)
    db.flush()
    # A Purpose with no current version is not a state the product supports -
    # /portal/overview raises on it - so this fixture must not create one.
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[], processing_activity_ids=[],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.flush()
    notice = Notice(
        tenant_id=resolve_tenant_id(db, "RETENTION_TEST"), purpose_id=purpose.id,
        status="ACTIVE", current_version=1,
    )
    db.add(notice)
    db.flush()
    now = datetime.now(timezone.utc)
    version = NoticeVersion(
        notice_id=notice.id, version_number=1, title="Retention test notice",
        body="Body", content_hash="a" * 64, is_current=True,
        published_at=now - timedelta(days=2), effective_from=now - timedelta(days=2),
    )
    db.add(version)
    db.commit()

    pack = build_evidence_pack(
        db, period_start=now - timedelta(days=7), period_end=now, audit=False
    )
    included = pack["sections"]["notice_versions"]["records"]["notice_versions"]
    assert any(v["id"] == version.id and v["content_hash"] == "a" * 64 for v in included)


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
def test_retention_endpoints(db, retention_client, staff_token):
    schedule = retention_client.get("/retention/schedule", headers=_auth(staff_token))
    assert schedule.status_code == 200
    codes = {row["record_class"] for row in schedule.json()}
    assert "consent_manager_records" in codes

    scan = retention_client.get("/retention/scan", headers=_auth(staff_token))
    assert scan.status_code == 200
    assert scan.json()["deleted_before_floor"] == 0
    assert scan.json()["compliant"] is True

    below_floor = retention_client.put(
        "/retention/schedule/audit_logs", json={"retention_days": 10},
        headers=_auth(staff_token),
    )
    assert below_floor.status_code == 422
    assert "floor" in below_floor.json()["detail"].lower()

    refused = retention_client.post(
        "/retention/enforce",
        json={"record_class": "consent_decision_logs",
              "cutoff": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
              "dry_run": True},
        headers=_auth(staff_token),
    )
    assert refused.status_code == 422

    pack = retention_client.get(
        "/retention/evidence-pack",
        params={"period_start": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
                "period_end": datetime.now(timezone.utc).isoformat()},
        headers=_auth(staff_token),
    )
    assert pack.status_code == 200
    assert pack.json()["unavailable_sections"] == []
    assert pack.json()["sections"]["breach_register"]["status"] == "OK"


def test_retention_endpoints_require_a_credential(retention_client):
    assert retention_client.get("/retention/schedule").status_code in (401, 403)
    assert retention_client.get("/retention/scan").status_code in (401, 403)
    assert retention_client.get("/retention/evidence-pack").status_code in (401, 403)
    assert retention_client.post(
        "/retention/enforce", json={"record_class": "notifications"}
    ).status_code in (401, 403)


def test_the_retention_scan_job_reports_compliance(db):
    from app.jobs.retention_scan_job import run

    result = run(db)
    assert result["deleted_before_floor"] == 0
    assert result["violations"] == 0
    assert result["compliant"] is True
    assert result["classes_scanned"] == len(retention_service.RETENTION_CLASSES)
