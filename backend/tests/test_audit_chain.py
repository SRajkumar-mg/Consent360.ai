import importlib.util
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.core.audit_chain import compute_entry_hash, verify_chain
from app.core.database import SessionLocal
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id


def _load_hardening_migration():
    """Import alembic/versions/c5e6f7a8b9d0_harden_audit_ledger.py by file
    path (it is not part of any importable package - alembic/versions has no
    __init__.py) so its own `_canonical`/`_compute_entry_hash` can be called
    directly, with no database involved."""
    backend_dir = Path(__file__).resolve().parent.parent
    path = backend_dir / "alembic" / "versions" / "c5e6f7a8b9d0_harden_audit_ledger.py"
    spec = importlib.util.spec_from_file_location("migration_c5e6f7a8b9d0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_hash_chain_agrees_with_app_core_audit_chain():
    """docs/ARCHITECTURE.md: the append-only migration (c5e6f7a8b9d0) duplicates
    app.core.audit_chain's canonical-payload/hash algorithm byte-for-byte,
    deliberately, rather than importing application code from a migration -
    "the two copies must never diverge". Prove they still agree: feed the
    same synthetic row to both `app.core.audit_chain.compute_entry_hash`
    (which reads attributes off an AuditLog-like object) and the migration's
    own `_compute_entry_hash` (which reads the same fields off a plain
    dict, as it does when reading raw SQL rows) and assert identical
    output - including the UTC-normalisation branch each side documents
    (a TIMESTAMPTZ value round-tripping through psycopg2 in a non-UTC
    session timezone), by using a created_at with a non-UTC offset."""
    migration = _load_hardening_migration()

    fields = {
        "id": 4242,
        "event": "CONSENT_GRANTED",
        "actor_username": "test-actor",
        "actor_type": "PRINCIPAL",
        "tenant_id": 7,
        "customer_id": 99,
        "consent_id": 55,
        "old_status": "REQUESTED",
        "new_status": "GRANTED",
        "reason": "unit test parity check",
        # +05:30 (IST) - not UTC, to actually exercise the astimezone(utc)
        # normalisation both copies perform before hashing.
        "created_at": datetime(2026, 9, 4, 18, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    }
    prev_hash = "1" * 64

    app_side = compute_entry_hash(prev_hash, SimpleNamespace(**fields))
    migration_side = migration._compute_entry_hash(prev_hash, dict(fields))

    assert app_side == migration_side

    # Same again with created_at=None, to cover the "nothing to normalise"
    # branch identically on both sides.
    fields_no_created_at = {**fields, "created_at": None}
    app_side_none = compute_entry_hash(prev_hash, SimpleNamespace(**fields_no_created_at))
    migration_side_none = migration._compute_entry_hash(prev_hash, dict(fields_no_created_at))
    assert app_side_none == migration_side_none
    assert app_side_none != app_side


def test_log_audit_chains_hashes_per_tenant(db):
    first = log_audit(db, "DECISION_EVALUATED", source_app="CHAIN_TEST", reason="first")
    second = log_audit(db, "DECISION_EVALUATED", source_app="CHAIN_TEST", reason="second")
    assert second.prev_hash == first.entry_hash
    assert second.entry_hash == compute_entry_hash(first.entry_hash, second)


def test_verify_chain_detects_tamper(db):
    entry = log_audit(db, "DECISION_EVALUATED", source_app="TAMPER_TEST", reason="original")
    db.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_block_update"))
    db.execute(text("UPDATE audit_logs SET reason = 'tampered' WHERE id = :id"), {"id": entry.id})
    db.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_block_update"))
    db.commit()

    result = verify_chain(db)
    assert any(b["audit_log_id"] == entry.id for b in result["broken"])


def test_trigger_blocks_delete(db):
    entry = log_audit(db, "DECISION_EVALUATED", source_app="DELETE_TEST", reason="undeletable")
    import pytest
    from sqlalchemy.exc import DBAPIError, InternalError

    with pytest.raises((InternalError, DBAPIError)):
        db.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": entry.id})
        db.commit()
    db.rollback()


def test_trigger_blocks_update(db):
    """Companion to test_trigger_blocks_delete: the same trigger function
    guards UPDATE, proven directly (not just via the tamper-detection test,
    which disables the trigger to get its tampered row written)."""
    import pytest
    from sqlalchemy.exc import DBAPIError, InternalError

    entry = log_audit(db, "DECISION_EVALUATED", source_app="UPDATE_TEST", reason="immutable")

    with pytest.raises((InternalError, DBAPIError)):
        db.execute(text("UPDATE audit_logs SET reason = 'edited' WHERE id = :id"), {"id": entry.id})
        db.commit()
    db.rollback()


def test_log_audit_serialises_concurrent_writes_for_same_tenant(db):
    """Reproduces the race a reviewer found: two transactions writing audit
    rows for the SAME tenant must not both read the same prev_hash, or the
    second row forks the chain instead of extending it.

    This is deliberately NOT "start two threads from a shared barrier and
    hope the scheduler lets thread one run log_audit() first" - an earlier
    version of this test did exactly that and was itself racy: nothing
    guarantees which of two threads released from a barrier actually gets
    to its first line of Python first, and when writer_two happened to win
    that race it called log_audit() (default commit=True) to completion in
    under a millisecond, before writer_one had even started - so there was
    no overlap to observe and the test failed for a reason that had nothing
    to do with the fix. (Caught by literally running it and watching it
    fail nondeterministically for the wrong reason - see the report.)

    The fixed design makes the ORDERING deterministic while keeping the
    concurrency genuine: writer_one is started first and must complete a
    real log_audit(commit=False) call - inserting its row but not
    committing, so its transaction and the real pg_advisory_xact_lock
    log_audit acquired inside it stay open - and signals readiness via a
    threading.Event before writer_two is even started. Only then does
    writer_two run its own real log_audit() call, via a second, independent
    session/connection, while writer_one's transaction is verifiably still
    open. This is still two real concurrent database transactions (writer_two
    is genuinely blocked inside Postgres, not merely sequenced in Python) -
    only the *start* order is pinned down, not the concurrency itself.

    Without the per-tenant lock, writer_two would read the tenant's tail
    entry_hash before writer_one's row is visible (it isn't committed yet),
    stamp the same prev_hash writer_one just used, and fork the chain. With
    the lock, writer_two's log_audit() call must block until writer_one
    commits and releases it; this is asserted directly (writer_two's thread
    must still be alive after a wait comfortably longer than an uncontended
    call takes, and its call must have taken at least that long), not just
    inferred from the end state.
    """
    tenant_id = resolve_tenant_id(db, "RACE_TEST")
    db.commit()  # make the tenant row visible to the other sessions' connections

    results: dict = {}
    errors: list = []
    first_write_done = threading.Event()
    hold_release = threading.Event()
    # How long the main thread waits before checking that writer_two is
    # still blocked and releasing writer_one's hold. MIN_EXPECTED_ELAPSED
    # (asserted against writer_two's own measured elapsed time) is kept
    # comfortably below this to absorb thread-start/scheduling skew between
    # the main thread's clock and writer_two's own timer, while still being
    # far above the sub-millisecond cost of an uncontended log_audit() call.
    PRE_RELEASE_WAIT = 0.4
    MIN_EXPECTED_ELAPSED = 0.3

    def writer_one():
        session = SessionLocal()
        try:
            entry = log_audit(
                session, "DECISION_EVALUATED", source_app="RACE_TEST", reason="first", commit=False
            )
            results["first_id"] = entry.id
            results["first_hash"] = entry.entry_hash
            # Signal that the row is inserted (uncommitted) and the tenant's
            # advisory lock is held, then hold the transaction open - widening
            # the race window - until the main thread says to release it.
            first_write_done.set()
            hold_release.wait(timeout=5)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - surface to the main thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def writer_two():
        session = SessionLocal()
        try:
            assert first_write_done.wait(timeout=5), "writer_one never signalled readiness"
            started = time.monotonic()
            entry = log_audit(session, "DECISION_EVALUATED", source_app="RACE_TEST", reason="second")
            results["second_elapsed"] = time.monotonic() - started
            results["second_id"] = entry.id
            results["second_prev_hash"] = entry.prev_hash
            results["second_hash"] = entry.entry_hash
        except Exception as exc:  # noqa: BLE001 - surface to the main thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    t1 = threading.Thread(target=writer_one)
    t2 = threading.Thread(target=writer_two)
    t1.start()
    # Wait until writer_one has genuinely inserted its (uncommitted) row and
    # is holding the tenant's advisory lock before starting writer_two, so
    # the ordering is deterministic - only the concurrency (writer_two's
    # real log_audit() call overlapping writer_one's still-open transaction)
    # is left to actually race.
    assert first_write_done.wait(timeout=5), "writer_one did not complete its write in time"
    t2.start()

    # try/finally so that a failure in the assertion below still always
    # releases writer_one's hold and joins both threads - otherwise a
    # genuine regression here would leak writer_one's thread (blocked on
    # hold_release for up to 5s, holding its DB connection open) into the
    # next test, surfacing as a confusing unrelated teardown error instead
    # of the real assertion failure below.
    try:
        # Give writer_two a real chance to reach - and, without the fix, sail
        # straight through - the point where it would read the tenant's tail
        # hash while writer_one's transaction is still open.
        t2.join(timeout=PRE_RELEASE_WAIT)
        writer_two_still_blocked = t2.is_alive()
    finally:
        hold_release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert writer_two_still_blocked, (
        "writer_two's log_audit() returned while writer_one's transaction for "
        "the same tenant was still open - the per-tenant lock is not serialising writes"
    )
    assert not errors, f"writer thread(s) raised: {errors}"
    assert not t1.is_alive() and not t2.is_alive()

    # writer_two was genuinely blocked on the lock, not just slow to start.
    assert results["second_elapsed"] >= MIN_EXPECTED_ELAPSED

    # No fork: writer_two's row correctly chains onto writer_one's, not onto
    # whatever the tenant's tail was before writer_one's (uncommitted, at the
    # time) row existed.
    assert results["second_prev_hash"] == results["first_hash"]
    assert results["second_id"] > results["first_id"]

    # Authoritative check: recomputing the whole tenant's chain from the
    # database finds no broken links.
    result = verify_chain(db)
    broken_for_tenant = [b for b in result["broken"] if b["tenant_id"] == tenant_id]
    assert broken_for_tenant == []


def test_portal_grant_audit_row_has_opaque_principal_actor(db, client):
    """R1-02 finding: portal-driven writes must stamp a real actor_type/
    actor_id (not just a "principal:CUST-..." prefix buried in
    actor_username), the id must be the opaque external id (never a name or
    email), and no column on the row may leak the customer's actual name."""
    from sqlalchemy import inspect

    from app.core.security import create_context_token
    from app.models.entities import (
        AuditLog,
        ConsentContext,
        Customer,
        DataCategory,
        ProcessingActivity,
        Purpose,
        PurposeVersion,
    )

    category = DataCategory(name="cat-audit-actor", code="cat_audit_actor")
    activity = ProcessingActivity(name="act-audit-actor", code="act_audit_actor")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name="Audit Actor Purpose", code="audit_actor_purpose", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()

    secret_name = "Sanjay Confidential Kumar"
    customer = Customer(
        external_id="CUST-AUDIT-ACTOR-001", name=secret_name,
        email="sanjay-confidential@example.com", source_app="AUDIT_ACTOR_TEST",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    token = create_context_token(customer.id, "AUDIT_ACTOR_TEST")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="AUDIT_ACTOR_TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/grant", headers={"X-Context-Token": token}, json={"purpose_code": purpose.code}
    )
    assert resp.status_code == 200

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event == "CONSENT_GRANTED", AuditLog.customer_id == customer.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.actor_type == "PRINCIPAL"
    assert row.actor_id is not None
    assert row.actor_id == customer.external_id
    assert row.actor_id != secret_name
    assert row.actor_id != customer.email

    for column in inspect(AuditLog).columns:
        value = getattr(row, column.name)
        assert secret_name not in str(value or ""), f"customer name leaked via column {column.name!r}"


def test_audit_export_includes_chain_fields_and_reverifies(db, client, staff_token):
    """R1-02 finding: the export must carry enough per-row data (entry_hash,
    prev_hash, tenant_id, actor_id, actor_type) that a recipient can
    reconstruct and re-verify the hash chain from the export alone, with no
    access to the database."""
    import io
    import json
    import zipfile
    from types import SimpleNamespace

    first = log_audit(db, "DECISION_EVALUATED", source_app="EXPORT_CHAIN_TEST", reason="first")
    second = log_audit(
        db, "DECISION_EVALUATED", source_app="EXPORT_CHAIN_TEST", reason="second",
        actor_type="PRINCIPAL", actor_id="CUST-EXPORT-CHAIN-1",
    )

    resp = client.get("/audit/export", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    records = [json.loads(line) for line in zf.read("audit_log.ndjson").decode("utf-8").splitlines()]
    by_id = {r["id"]: r for r in records}
    r1, r2 = by_id[first.id], by_id[second.id]

    for record in (r1, r2):
        for field in ("entry_hash", "prev_hash", "tenant_id", "actor_type", "actor_id"):
            assert field in record

    assert r1["entry_hash"] == first.entry_hash
    assert r1["prev_hash"] == first.prev_hash
    assert r2["actor_type"] == "PRINCIPAL"
    assert r2["actor_id"] == "CUST-EXPORT-CHAIN-1"

    def _entry_from_export_record(record: dict) -> SimpleNamespace:
        return SimpleNamespace(
            id=record["id"],
            event=record["event"],
            actor_username=record["actor_username"],
            actor_type=record["actor_type"],
            tenant_id=record["tenant_id"],
            customer_id=record["customer_id"],
            consent_id=record["consent_id"],
            old_status=record["old_status"],
            new_status=record["new_status"],
            reason=record["reason"],
            created_at=datetime.fromisoformat(record["created_at"]),
        )

    # Re-verify using ONLY fields present in the export (as an external
    # recipient with the export file and this hashing algorithm would).
    assert compute_entry_hash(r1["prev_hash"], _entry_from_export_record(r1)) == r1["entry_hash"]
    assert compute_entry_hash(r1["entry_hash"], _entry_from_export_record(r2)) == r2["entry_hash"]
