"""R3-12 backup/restore drill: a REAL drill, not a document describing one.

NOT a pytest test (deliberately does not match pytest.ini's `test_*.py`
collection pattern - see below for why) - this is standalone, manually-run
drill tooling. It lives under tests/ rather than backend/scripts/ because it
is verification tooling, not an application script.

Steps, all executed against throwaway databases only (never
``consent_platform``, the shared dev database other engineers/agents are
using, and never ``consent_platform_test``, the pytest suite's own
throwaway database):

  1. Create ``consent_platform_restore_drill`` and run every Alembic
     migration against it - the same schema-provisioning path
     tests/conftest.py uses for the test suite, so the drill exercises the
     real, current schema (including the append-only audit trigger from
     ``c5e6f7a8b9d0_harden_audit_ledger.py``).
  2. Seed a small but real dataset directly through the ORM plus repeated
     calls to the actual ``app.services.audit.log_audit`` (the same
     function every code path uses, across three different tenants, so the
     hash chain this drill later verifies is genuine, not hand-faked).
     Deliberately does NOT drive the full consent grant/withdraw state
     machine (services/consent.py's request_consent/grant_consent/...) -
     that machinery is already covered by tests/test_*.py; this drill's job
     is to prove BACKUP/RESTORE mechanics and audit-chain durability, not
     consent business rules, so consents are seeded directly at a target
     status via the ORM.
  3. Record row counts per table and run ``app.core.audit_chain.verify_chain``
     - this is the pre-drill baseline.
  4. ``pg_dump -Fc`` (custom format) the drill database to a file, timed.
  5. DROP the drill database entirely - the simulated disaster.
  6. Recreate an empty database and ``pg_restore`` the dump into it, timed.
  7. Reconnect and verify: every table's row count matches step 3 exactly,
     and verify_chain() reports the SAME "checked" count and zero broken
     links over the restored data - a restore that silently drops rows or
     breaks the hash chain is a FAILED drill, and this script exits
     non-zero if that happens.

Requires ``pg_dump``/``pg_restore`` on PATH (or set PG_DUMP/PG_RESTORE to
their full paths) matching the running server's major version.

Usage (from backend/, with the project's venv active):

    python -m tests.restore_drill [--keep] [--backup-dir DIR]

``--keep`` leaves both the drill database and the restored-into database in
place afterwards for manual inspection (both are still dropped and
recreated at the START of the next run regardless). Writes a JSON result
file (default: a consent360-restore-drill/ directory under the OS temp dir,
never inside the repo unless --backup-dir is given explicitly) - this run's
timing/evidence record. See backend/docs/compliance/RESTORE_DRILL_RECORD.md
for a drill actually run this way, with its real recorded numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DRILL_DB = "consent_platform_restore_drill"
RESTORED_DB = "consent_platform_restore_drill_restored"
_DEFAULT_URL = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/consent_platform"

PG_DUMP = os.environ.get("PG_DUMP", "pg_dump")
PG_RESTORE = os.environ.get("PG_RESTORE", "pg_restore")


def _base_url() -> str:
    # Deliberately NOT settings.DATABASE_URL / .env: this script must never
    # accidentally point at the shared dev database. It only ever reads
    # DATABASE_URL from the environment (to pick up host/port/credentials)
    # and always overrides the DATABASE NAME itself below.
    return os.environ.get("DATABASE_URL") or _DEFAULT_URL


def _url_for(db_name: str) -> str:
    url = re.sub(r"/[^/]+$", f"/{db_name}", _base_url())
    assert url.rsplit("/", 1)[-1] == db_name
    return url


def _conn_kwargs():
    from sqlalchemy.engine import make_url

    u = make_url(_base_url())
    return {"host": u.host, "port": u.port, "user": u.username, "password": u.password}


def _admin_connect():
    import psycopg2

    conn = psycopg2.connect(dbname="postgres", **_conn_kwargs())
    conn.autocommit = True
    return conn


def _create_empty_database(name: str) -> None:
    from psycopg2 import sql

    conn = _admin_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        cur.close()
    finally:
        conn.close()


def _drop_database(name: str) -> None:
    from psycopg2 import sql

    conn = _admin_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        cur.close()
    finally:
        conn.close()


def _run_migrations(url: str) -> None:
    # alembic/env.py calls app.core.config.get_settings() itself and
    # OVERRIDES whatever sqlalchemy.url is set on the Config object with
    # settings.DATABASE_URL (see docs/ARCHITECTURE.md: "alembic/env.py overrides the
    # URL in alembic.ini with DATABASE_URL from .env") - so the only way to
    # actually point Alembic at the drill database is the same trick
    # tests/conftest.py uses: set the environment variable BEFORE the
    # (lru_cache'd) get_settings() is ever called in this process.
    os.environ["DATABASE_URL"] = url

    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _seed_sample_data(url: str) -> None:
    """Populate the drill database with a small, realistic-shaped dataset
    directly through the ORM, plus a genuine, multi-tenant hash-chained
    audit trail via the real log_audit() function. See module docstring for
    why this does not drive the full consent state machine."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        from app.core.encryption import hmac_digest
        from app.models.entities import (
            Consent,
            Customer,
            DataCategory,
            ProcessingActivity,
            Purpose,
            PurposeVersion,
        )
        from app.services.audit import log_audit
        from app.services.tenancy import resolve_tenant_id

        tenant_id = resolve_tenant_id(db, "RESTORE_DRILL")
        db.commit()

        category = DataCategory(name="Restore Drill Category", code="RESTORE_DRILL_CAT", description="drill", is_active=True)
        activity = ProcessingActivity(name="Restore Drill Activity", code="RESTORE_DRILL_ACT", description="drill", is_active=True)
        db.add_all([category, activity])
        db.flush()

        purpose = Purpose(
            name="Restore Drill Purpose", code="RESTORE_DRILL_PURPOSE", description="drill",
            legal_basis="CONSENT", requires_consent=True, is_active=True, tenant_id=tenant_id,
        )
        db.add(purpose)
        db.flush()
        version = PurposeVersion(
            purpose_id=purpose.id, version_number=1, is_current=True, name="Restore Drill Purpose v1",
            consent_text="Restore drill consent text",
            data_category_ids=[category.id], processing_activity_ids=[activity.id],
        )
        db.add(version)
        db.commit()
        db.refresh(purpose)

        customers = []
        for i in range(25):
            c = Customer(
                external_id=f"RESTORE-DRILL-{i:03d}", name=f"Restore Drill Customer {i}",
                email=f"restore-drill-{i}@example.com",
                email_search=hmac_digest(f"restore-drill-{i}@example.com"),
                external_id_search=hmac_digest(f"RESTORE-DRILL-{i:03d}"),
                source_app="RESTORE_DRILL", tenant_id=tenant_id,
            )
            db.add(c)
            customers.append(c)
        db.commit()
        for c in customers:
            db.refresh(c)

        now = datetime.now(timezone.utc)
        for i, c in enumerate(customers):
            status = "GRANTED" if i % 3 == 0 else ("WITHDRAWN" if i % 3 == 1 else "NOT_REQUESTED")
            consent = Consent(
                tenant_id=tenant_id, customer_id=c.id, purpose_id=purpose.id, purpose_version_id=version.id,
                data_category_id=category.id, processing_activity_id=activity.id,
                consent_version=1, status=status, source_app="RESTORE_DRILL",
                actor_username="restore-drill-script",
                granted_at=now if status == "GRANTED" else None,
                withdrawn_at=now if status == "WITHDRAWN" else None,
            )
            db.add(consent)
        db.commit()

        # Genuine, hash-chained audit trail across three tenants (the same
        # function every real code path calls - see app/core/audit_chain.py
        # and app/services/audit.py's per-tenant advisory-lock chaining).
        for tenant_code in ("RESTORE_DRILL", "RESTORE_DRILL_TENANT_B", "RESTORE_DRILL_TENANT_C"):
            for n in range(12):
                log_audit(
                    db, "CONSENT_GRANTED" if n % 2 == 0 else "CONSENT_WITHDRAWN",
                    actor_username="restore-drill-script", actor_type="SYSTEM", source_app=tenant_code,
                    reason=f"Restore drill seed event {n} for {tenant_code}",
                )
    finally:
        db.close()
        engine.dispose()


def _table_counts(url: str) -> dict:
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            tables = [
                r[0] for r in conn.execute(sa_text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
                )).fetchall()
            ]
            counts = {}
            for t in tables:
                counts[t] = conn.execute(sa_text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
            return counts
    finally:
        engine.dispose()


def _verify_chain(url: str) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.audit_chain import verify_chain

    engine = create_engine(url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        return verify_chain(db)
    finally:
        db.close()
        engine.dispose()


def _pg_dump(url: str, out_path: Path) -> float:
    from sqlalchemy.engine import make_url

    u = make_url(url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    cmd = [
        PG_DUMP, "-h", str(u.host), "-p", str(u.port), "-U", str(u.username),
        "-Fc", "-f", str(out_path), u.database,
    ]
    start = time.monotonic()
    subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
    return time.monotonic() - start


def _pg_restore(dump_path: Path, url: str) -> float:
    from sqlalchemy.engine import make_url

    u = make_url(url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    cmd = [
        PG_RESTORE, "-h", str(u.host), "-p", str(u.port), "-U", str(u.username),
        "-d", u.database, "--no-owner", "--no-privileges", str(dump_path),
    ]
    start = time.monotonic()
    subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)
    return time.monotonic() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Do not drop the drill/restored databases at the end")
    parser.add_argument(
        "--backup-dir", default=None,
        help="Directory to write the .dump and result JSON into (default: an OS-temp-dir location, never inside the repo)",
    )
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir) if args.backup_dir else Path(tempfile.gettempdir()) / "consent360-restore-drill"
    backup_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = backup_dir / f"restore_drill_{run_id}.dump"

    result: dict = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "drill_database": DRILL_DB,
        "restored_database": RESTORED_DB,
    }

    print(f"[1/7] Creating throwaway database {DRILL_DB!r} and running migrations...")
    drill_url = _url_for(DRILL_DB)
    _create_empty_database(DRILL_DB)
    t0 = time.monotonic()
    _run_migrations(drill_url)
    result["migration_seconds"] = round(time.monotonic() - t0, 3)

    print("[2/7] Seeding sample data (customers, consents, multi-tenant audit trail)...")
    _seed_sample_data(drill_url)

    print("[3/7] Recording pre-backup baseline (row counts + audit chain verification)...")
    baseline_counts = _table_counts(drill_url)
    baseline_chain = _verify_chain(drill_url)
    result["baseline_row_counts"] = baseline_counts
    result["baseline_chain_verification"] = baseline_chain
    print(f"      audit chain: checked={baseline_chain['checked']} broken={len(baseline_chain['broken'])} tenants={baseline_chain['tenants']}")
    if baseline_chain["broken"]:
        print("FAIL: baseline audit chain is already broken before any backup was taken - aborting drill.")
        return 1

    print(f"[4/7] pg_dump -Fc  {DRILL_DB} -> {dump_path} ...")
    backup_seconds = _pg_dump(drill_url, dump_path)
    result["backup_seconds"] = round(backup_seconds, 3)
    result["backup_file_bytes"] = dump_path.stat().st_size
    print(f"      done in {backup_seconds:.2f}s, {result['backup_file_bytes']:,} bytes")

    print(f"[5/7] Simulating disaster: DROP DATABASE {DRILL_DB} ...")
    disaster_at = datetime.now(timezone.utc)
    _drop_database(DRILL_DB)
    result["disaster_at"] = disaster_at.isoformat()

    print(f"[6/7] Restoring into fresh database {RESTORED_DB} ...")
    restored_url = _url_for(RESTORED_DB)
    _create_empty_database(RESTORED_DB)
    restore_seconds = _pg_restore(dump_path, restored_url)
    result["restore_seconds"] = round(restore_seconds, 3)
    restore_done_at = datetime.now(timezone.utc)
    rto_seconds = (restore_done_at - disaster_at).total_seconds()
    result["measured_rto_seconds"] = round(rto_seconds, 3)
    print(f"      done in {restore_seconds:.2f}s (disaster-to-restored wall clock: {rto_seconds:.2f}s)")

    print("[7/7] Verifying restored data: row counts + audit chain re-verification...")
    restored_counts = _table_counts(restored_url)
    restored_chain = _verify_chain(restored_url)
    result["restored_row_counts"] = restored_counts
    result["restored_chain_verification"] = restored_chain

    counts_match = restored_counts == baseline_counts
    chain_matches = (
        restored_chain["checked"] == baseline_chain["checked"]
        and restored_chain["tenants"] == baseline_chain["tenants"]
        and not restored_chain["broken"]
    )
    result["row_counts_match"] = counts_match
    result["chain_intact_after_restore"] = chain_matches
    result["drill_passed"] = bool(counts_match and chain_matches)

    if not counts_match:
        mismatches = {
            t: (baseline_counts.get(t), restored_counts.get(t))
            for t in set(baseline_counts) | set(restored_counts)
            if baseline_counts.get(t) != restored_counts.get(t)
        }
        result["row_count_mismatches"] = mismatches
        print(f"FAIL: row counts differ after restore: {mismatches}")
    if not chain_matches:
        print(f"FAIL: audit chain does not match after restore: baseline={baseline_chain} restored={restored_chain}")

    result["finished_at"] = datetime.now(timezone.utc).isoformat()

    result_path = backup_dir / f"restore_drill_result_{run_id}.json"
    result_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nResult written to {result_path}")
    print(f"DRILL {'PASSED' if result['drill_passed'] else 'FAILED'}")
    print(f"  migration:        {result['migration_seconds']}s")
    print(f"  backup (RPO op):  {result['backup_seconds']}s  ({result['backup_file_bytes']:,} bytes)")
    print(f"  restore:          {result['restore_seconds']}s")
    print(f"  measured RTO:     {result['measured_rto_seconds']}s (disaster to restored+verifiable)")

    if not args.keep:
        print("\nCleaning up drill databases...")
        _drop_database(DRILL_DB)
        _drop_database(RESTORED_DB)
    else:
        print(f"\n--keep passed: leaving {RESTORED_DB!r} in place for inspection ({DRILL_DB!r} was already dropped in step 5 - that is the simulated disaster).")

    return 0 if result["drill_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
