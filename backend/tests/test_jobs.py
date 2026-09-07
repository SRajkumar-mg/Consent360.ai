from datetime import datetime, timedelta, timezone

from app.jobs.audit_chain_job import run as audit_chain_run
from app.jobs.context_token_cleanup_job import run as context_token_cleanup_run
from app.jobs.erasure_executor_job import run as erasure_executor_run
from app.jobs.erasure_retention_scan_job import run as erasure_retention_scan_run
from app.jobs.expire_consents_job import run as expire_consents_run
from app.jobs.inactivity_scan_job import run as inactivity_scan_run
from app.jobs.kpi_rollup_job import run as kpi_rollup_run
from app.jobs.pre_erasure_notice_job import run as pre_erasure_notice_run
from app.jobs.retention_scan_job import run as retention_scan_run
from app.jobs.scheduler import job_runner
from app.models.entities import (
    Consent,
    ConsentContext,
    Customer,
    DataCategory,
    KpiSnapshot,
    Purpose,
    PurposeVersion,
    ProcessingActivity,
    SchedulerRun,
)
from app.services import consent as consent_service
from app.services.tenancy import platform_tenant_id


def _make_purpose(db, code):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, requires_consent=True, retention_period_days=30)
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()
    return purpose, category, activity


def test_job_runner_records_scheduler_run(db):
    run = job_runner.run("unit_test_job", lambda session: {"ok": 1})
    assert run.status == "SUCCESS"
    assert run.counts == {"ok": 1}
    stored = db.get(SchedulerRun, run.id)
    assert stored is not None
    assert stored.finished_at is not None
    assert stored.tenant_id is not None
    assert stored.tenant_id == platform_tenant_id(db)


def test_job_runner_records_failure():
    def boom(session):
        raise ValueError("kaboom")

    run = job_runner.run("unit_test_failing_job", boom)
    assert run.status == "FAILED"
    assert "kaboom" in (run.error or "")


def test_expire_consents_job_marks_overdue_consent_expired(db):
    purpose, category, activity = _make_purpose(db, "t1_expire")
    customer = Customer(external_id="CUST-T1-001", name="T1 Customer", source_app="TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="TEST")
    consent_service.grant_consent(db, consent, expires_in_days=1, source_app="TEST")
    consent.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    counts = expire_consents_run(db)
    assert counts["expired"] >= 1
    db.refresh(consent)
    assert consent.status == "EXPIRED"


def test_kpi_rollup_job_writes_snapshot(db):
    counts = kpi_rollup_run(db)
    assert "snapshot_id" in counts
    assert "total_customers" in counts
    snapshot = db.get(KpiSnapshot, counts["snapshot_id"])
    assert snapshot.tenant_id is not None
    assert snapshot.tenant_id == platform_tenant_id(db)


def test_audit_chain_job_runs_and_records_tenant(db):
    """The daily chain-verification job (registered in scheduler.py) just
    wraps verify_chain(); prove it runs cleanly via the same job_runner path
    every other job uses, and records a non-NULL tenant on its own
    scheduler_runs row like every other job.

    Deliberately does not assert `broken == []`: the `db` fixture shares one
    database across the whole pytest session with no per-test rollback, and
    test_audit_chain.py's tamper-detection test leaves a permanently broken
    row behind on purpose, in its own tenant, to prove detection works."""
    run = job_runner.run("audit_chain_verify", audit_chain_run)
    assert run.status == "SUCCESS"
    assert isinstance(run.counts.get("broken"), list)
    assert run.tenant_id is not None
    assert run.tenant_id == platform_tenant_id(db)


def test_retention_scan_job_runs_via_job_runner(db):
    """R1-10/S-05's daily read-only re-check (app/services/retention.py::
    retention_scan) is exercised directly and thoroughly by
    tests/test_retention_and_evidence_pack.py; this proves the thin
    `app/jobs/retention_scan_job.py` wrapper that scheduler.py actually
    registers survives a real trip through JobRunner - the same integration
    point every other scheduled job in this file is proven through - rather
    than only ever being called as a bare function."""
    run = job_runner.run("retention_scan", retention_scan_run)
    assert run.status == "SUCCESS"
    assert isinstance(run.counts.get("classes_scanned"), int)
    assert isinstance(run.counts.get("compliant"), bool)
    assert run.tenant_id is not None
    assert run.tenant_id == platform_tenant_id(db)


def test_erasure_retention_scan_job_runs_via_job_runner(db):
    """R1-06/G-01's retention-clock scan (app/services/erasure.py::
    erasure_retention_scan) is exercised directly by tests/test_erasure_
    engine.py; this proves the scheduled `erasure_retention_scan_job`
    wrapper runs cleanly through the same JobRunner path as every other job."""
    run = job_runner.run("erasure_retention_scan", erasure_retention_scan_run)
    assert run.status == "SUCCESS"
    assert isinstance(run.counts.get("policies"), int)
    assert isinstance(run.counts.get("candidates"), int)
    assert isinstance(run.counts.get("jobs_proposed"), int)
    assert run.tenant_id is not None


def test_inactivity_scan_job_runs_via_job_runner(db):
    """R1-06/G-02's Third Schedule inactivity clock (app/services/erasure.py
    ::inactivity_scan) is exercised directly by tests/test_erasure_engine.py;
    this proves the scheduled `inactivity_scan_job` wrapper runs cleanly
    through JobRunner and reports K-31 (`inactivity_clock_breaches`)."""
    run = job_runner.run("inactivity_scan", inactivity_scan_run)
    assert run.status == "SUCCESS"
    assert isinstance(run.counts.get("policies"), int)
    assert isinstance(run.counts.get("jobs_proposed"), int)
    assert isinstance(run.counts.get("inactivity_clock_breaches"), int)
    assert run.tenant_id is not None


def test_pre_erasure_notice_job_runs_via_job_runner(db):
    """R1-06/G-03's R.8(2) notice sweep (app/services/erasure.py::
    send_due_pre_erasure_notices) is exercised directly by tests/test_
    erasure_engine.py; this proves the scheduled `pre_erasure_notice_job`
    wrapper runs cleanly through JobRunner with no candidates pending."""
    run = job_runner.run("pre_erasure_notices", pre_erasure_notice_run)
    assert run.status == "SUCCESS"
    assert isinstance(run.counts.get("candidates"), int)
    assert isinstance(run.counts.get("notices_sent"), int)
    assert isinstance(run.counts.get("notices_failed"), int)
    assert run.tenant_id is not None


def test_erasure_executor_job_runs_via_job_runner(db):
    """R1-06/G-01/G-04's destructive sweep (app/services/erasure.py::
    execute_due_erasure_jobs) is exercised directly and extensively by
    tests/test_erasure_engine.py; this proves the scheduled
    `erasure_executor_job` wrapper - the one background job in this
    platform that can destroy personal data - runs cleanly through
    JobRunner with no jobs due."""
    run = job_runner.run("erasure_executor", erasure_executor_run)
    assert run.status == "SUCCESS"
    assert isinstance(run.counts.get("candidates"), int)
    assert isinstance(run.counts.get("executed"), int)
    assert isinstance(run.counts.get("blocked"), int)
    assert isinstance(run.counts.get("failed"), int)
    assert run.tenant_id is not None


def test_dashboard_no_longer_expires_consents_inline(db, client, staff_token):
    purpose, category, activity = _make_purpose(db, "t1_dash")
    customer = Customer(external_id="CUST-T1-DASH", name="T1 Dash Customer", source_app="TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="TEST")
    consent_service.grant_consent(db, consent, expires_in_days=1, source_app="TEST")
    consent.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    resp = client.get("/dashboard", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200
    db.refresh(consent)
    assert consent.status == "GRANTED", "dashboard must not call expire_consents inline anymore"


def test_context_status_survives_token_hashing_by_cleanup_job(db, client, staff_token):
    """`context_token_cleanup_job` hashes the stored token of consumed/expired
    contexts at rest. `GET /consent/context/status/{token}` is part of the
    published SDK surface and only ever receives the original raw token, so
    it must still find the (now-hashed) row and report the same status."""
    customer = Customer(external_id="CUST-T1-CTXHASH", name="T1 Context Customer", source_app="TEST")
    db.add(customer)
    db.flush()

    raw_token = "raw-context-token-for-hashing-test"
    context = ConsentContext(
        customer_id=customer.id,
        token=raw_token,
        source_app="TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        consumed_at=datetime.now(timezone.utc),
        is_active=False,
    )
    db.add(context)
    db.commit()

    counts = context_token_cleanup_run(db)
    assert counts["hashed"] >= 1

    db.refresh(context)
    assert context.token != raw_token
    assert context.token.startswith("sha256:")

    # The raw token must no longer be findable by a plain equality lookup.
    assert db.query(ConsentContext).filter(ConsentContext.token == raw_token).first() is None

    resp = client.get(
        f"/consent/context/status/{raw_token}", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "CONSUMED"


def test_job_runner_finalisation_survives_non_serialisable_counts(db, monkeypatch):
    """If the finally-block's own commit/refresh raises (here: a job handed
    back counts that are not JSON-serialisable), the session must still be
    closed and the row must not be left at status=RUNNING."""
    import app.jobs.scheduler as scheduler_module

    close_calls = {"count": 0}
    real_session_local = scheduler_module.SessionLocal

    def spying_session_local(*args, **kwargs):
        session = real_session_local(*args, **kwargs)
        original_close = session.close

        def spy_close():
            close_calls["count"] += 1
            return original_close()

        session.close = spy_close
        return session

    monkeypatch.setattr(scheduler_module, "SessionLocal", spying_session_local)

    def bad_job(session):
        return {"bad": object()}

    run = job_runner.run("unit_test_non_serialisable_counts_job", bad_job)

    assert close_calls["count"] == 1, "JobRunner.run must close its session even when finalisation fails"
    assert run.status != "RUNNING"
    assert run.status == "ERROR"
    assert run.error  # the finalisation failure message was recorded

    stored = db.get(SchedulerRun, run.id)
    assert stored is not None
    assert stored.status == "ERROR"
    assert stored.status != "RUNNING"
    assert stored.error
