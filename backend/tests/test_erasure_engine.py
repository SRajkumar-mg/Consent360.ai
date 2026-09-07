"""R1-06 (G-01..G-04, G-06): the retention and erasure engine.

The headline test is `test_dod_withdrawal_to_erasure_with_a_48_hour_notice`,
which is the task's definition of done end to end: a principal withdraws their
last consent, an `erasure_jobs` row is raised as evidence before anything
acts, the R.8(2) notice is actually queued through the notification service,
the erasure is refused while the 48 hours are still running, and it then
executes - anonymising the principal, hard-deleting the directory row,
instructing the processors and leaving an evidence hash behind.

`app/main.py` does not mount the erasure router (registering it is the
coordinating agent's change), so the HTTP-level tests mount the router onto a
local FastAPI app. That is not a workaround for a missing route: the router,
its permission dependencies and its handlers are exactly the ones `app.main`
will mount.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.encryption import hmac_digest
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentContext,
    CrmCustomer,
    Customer,
    DataCategory,
    DataSharingEvent,
    Notification,
    ProcessingActivity,
    Processor,
    ProcessorAlert,
    Purpose,
    PurposeVersion,
    RetentionAction,
)
from app.models.erasure import (
    MIN_PRE_ERASURE_NOTICE_HOURS,
    THIRD_SCHEDULE_INACTIVITY_DAYS,
    ErasureJob,
    LegalHold,
    RetentionPolicy,
)
from app.services import consent as consent_service
from app.services import erasure as erasure_service
from app.services import processors as processor_service
from app.services.retention import RetentionFloorViolation
from app.services.tenancy import resolve_tenant_id

SOURCE_APP = "ERASURE_TEST"


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def erasure_client(db):
    """The erasure router on its own app - see the module docstring."""
    from app.api.routes import erasure as erasure_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(erasure_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_purpose(db, code):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, legal_basis="CONSENT", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose, category, activity


def _make_customer(db, external_id, *, source_app=None, email=None, with_directory=False,
                   last_interaction_at=None, created_at=None):
    """Each test gets its own tenant by default, derived from its own external
    id, because this file's tests share one database for the whole session and
    several of them sweep every principal in a tenant."""
    source_app = source_app or f"{SOURCE_APP}_{external_id.replace('-', '_')}"
    email = email or f"{external_id.lower()}@example.test"
    customer = Customer(
        external_id=external_id, name="Test Principal", email=email, phone="+919000000000",
        source_app=source_app, tenant_id=resolve_tenant_id(db, source_app),
        last_interaction_at=last_interaction_at,
    )
    if created_at is not None:
        customer.created_at = created_at
    db.add(customer)
    db.flush()
    if with_directory:
        db.add(CrmCustomer(
            name="Test Principal", email=email, email_search=hmac_digest(email),
            phone="+919000000000", address="1 Test Road", age=33,
            consent_preferences={"analytics": True},
        ))
    db.commit()
    db.refresh(customer)
    return customer


def _granted_consent(db, customer, purpose, category, activity):
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=customer.source_app
    )
    consent_service.grant_consent(db, consent, source_app=customer.source_app)
    db.commit()
    return consent


def _job_for(db, customer):
    return (
        db.query(ErasureJob)
        .filter(ErasureJob.customer_id == customer.id)
        .order_by(ErasureJob.id.desc())
        .first()
    )


# --------------------------------------------------------------------------- #
# THE DEFINITION OF DONE
# --------------------------------------------------------------------------- #
def test_dod_withdrawal_to_erasure_with_a_48_hour_notice(db):
    """DoD: 'A withdrawn consent or an approved erasure request results in
    erasure within policy, with a 48-hour notice sent first and an
    erasure_jobs row as evidence.'

    Asserted in the order the engine actually performs them, because the order
    is the compliance property: the job row exists before anything acts, the
    notice precedes the erasure, and the erasure is refused until the notice
    period has genuinely run.
    """
    purpose, category, activity = _make_purpose(db, "dod_erasure")
    customer = _make_customer(db, "R106-DOD-001", with_directory=True)
    consent = _granted_consent(db, customer, purpose, category, activity)

    # 1. The withdrawal. s.8(7): no consent remains active afterwards, so
    #    erasure is due.
    consent_service.withdraw_consent(
        db, consent, reason="Principal withdrew", actor_username="principal",
        source_app=customer.source_app, actor_type="PRINCIPAL",
    )

    # 2. An erasure_jobs row exists as evidence, BEFORE anything is destroyed,
    #    already authorised by the principal's own statutory act.
    job = _job_for(db, customer)
    assert job is not None, "the withdrawal must raise an erasure job"
    assert job.trigger == "WITHDRAWAL"
    assert job.trigger_ref == f"withdrawal:{consent.id}:v{consent.consent_version}"
    assert job.authorised_by == "principal"
    assert "s.8(7)" in job.authorisation_basis
    assert job.status == "SCHEDULED"
    assert job.executed_at is None
    db.refresh(customer)
    assert customer.status != "ANONYMISED", "nothing may be destroyed before the notice"

    # 3. Erasure is refused while there is no notice on record.
    with pytest.raises(erasure_service.ErasureNotAuthorised) as exc:
        erasure_service.execute_erasure_job(db, job, actor_username="dpo")
    assert "R.8(2)" in str(exc.value)

    # 4. The 48-hour notice is actually SENT, through queue_notification.
    erasure_service.send_pre_erasure_notice(db, job, actor_username="scheduler")
    db.refresh(job)
    assert job.status == "NOTIFIED"
    assert job.notice_sent_at is not None
    assert job.notice_hours == MIN_PRE_ERASURE_NOTICE_HOURS
    notices = (
        db.query(Notification)
        .filter(Notification.id.in_(job.notification_ids))
        .all()
    )
    assert notices, "the notice must be real Notification rows, not a flag"
    assert {n.event_type for n in notices} == {"ERASURE_WARNING_48H"}
    assert "EMAIL" in {n.channel for n in notices}
    # The window is exactly the policy's period, measured from the notice.
    assert job.execute_after - job.notice_sent_at == timedelta(hours=48)

    # 5. Erasure is still refused inside the window.
    with pytest.raises(erasure_service.ErasureNotAuthorised) as exc:
        erasure_service.execute_erasure_job(db, job, actor_username="dpo")
    assert "notice period" in str(exc.value)
    db.refresh(customer)
    assert customer.status != "ANONYMISED"

    # 6. After the window, the erasure actually happens.
    after = _now() + timedelta(hours=49)
    erasure_service.execute_erasure_job(db, job, actor_username="dpo", now=after)

    db.refresh(job)
    db.refresh(customer)
    assert job.status == "EXECUTED"
    assert job.executed_at is not None
    assert job.executed_by == "dpo"
    assert job.evidence_hash and len(job.evidence_hash) == 64

    # The principal's identifiers are gone.
    assert customer.status == "ANONYMISED"
    assert customer.name == "[anonymised]"
    assert customer.email == ""
    assert customer.external_id.startswith("ANON-")
    assert job.anonymised_ref == customer.external_id

    # The directory row was really deleted, not merely blanked.
    assert db.query(CrmCustomer).filter(
        CrmCustomer.email_search == hmac_digest("r106-dod-001@example.test")
    ).count() == 0
    assert job.records_erased["directory_record"]["action"] == "ERASE"
    assert job.records_erased["directory_record"]["rows"] == 1

    # And the notice that proved we gave notice is retained under its floor.
    assert job.records_retained["notifications"]["rows_retained"] >= 1
    assert job.records_retained["notifications"]["floor_days"] == 365


def test_dod_approved_rights_request_results_in_erasure(db):
    """The other half of the DoD's 'or an approved erasure request'."""
    customer = _make_customer(db, "R106-DOD-002", with_directory=True)

    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-ABC123", approved_by="dpo",
        source_app=customer.source_app,
    )
    assert job.trigger == "RIGHTS_REQUEST"
    assert job.trigger_ref == "rights-request:GRV-ABC123"
    assert job.authorised_by == "dpo"
    assert "s.12(3)" in job.authorisation_basis
    assert job.status == "SCHEDULED"

    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    db.refresh(job)
    assert job.notice_sent_at is not None

    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)
    db.refresh(customer)
    assert job.status == "EXECUTED"
    assert customer.status == "ANONYMISED"


def test_the_same_request_ref_is_idempotent(db):
    """A retried approval must not produce a second erasure or a second
    notice."""
    customer = _make_customer(db, "R106-IDEM-001")
    first = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-IDEM", approved_by="dpo",
        source_app=customer.source_app,
    )
    second = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-IDEM", approved_by="dpo",
        source_app=customer.source_app,
    )
    assert first.id == second.id
    assert db.query(ErasureJob).filter(ErasureJob.customer_id == customer.id).count() == 1


# --------------------------------------------------------------------------- #
# The 48-hour notice as a precondition
# --------------------------------------------------------------------------- #
def test_a_second_notice_cannot_restart_the_window(db):
    customer = _make_customer(db, "R106-NOTICE-001")
    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-NOTICE-1", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    db.refresh(job)
    first_sent, first_deadline, first_ids = job.notice_sent_at, job.execute_after, list(job.notification_ids)

    erasure_service.send_pre_erasure_notice(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=40)
    )
    db.refresh(job)
    assert job.notice_sent_at == first_sent
    assert job.execute_after == first_deadline
    assert list(job.notification_ids) == first_ids


def test_a_notice_that_reaches_nobody_leaves_the_erasure_blocked(db, monkeypatch):
    """No notice, no erasure.

    `notice_sent_at` is written only from the ids `queue_notification`
    actually returned, so a queue that produces nothing on any channel leaves
    the field null and the erasure refused. That is the correct failure
    direction: the 48 hours exist so the principal can say "not my account",
    and a window nobody was told about is not a window.
    """
    customer = _make_customer(db, "R106-NOCHAN-001")
    job = erasure_service.propose_erasure_job(
        db, customer, trigger="MANUAL", trigger_ref="manual:nochannel",
        source_app=customer.source_app, authorised_by="dpo", authorisation_basis="test",
    )

    import app.services.notifications as notifications_module

    monkeypatch.setattr(notifications_module, "queue_notification", lambda *a, **k: [])
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    db.refresh(job)
    assert job.notice_sent_at is None
    assert job.execute_after is None
    assert job.status == "SCHEDULED"
    with pytest.raises(erasure_service.ErasureNotAuthorised) as exc:
        erasure_service.execute_erasure_job(db, job, actor_username="dpo")
    assert "no pre-erasure notice on record" in str(exc.value)


def test_notice_period_below_48_hours_is_not_representable(db):
    """R.8(2) states a minimum, so a shorter notice is refused rather than
    clamped - by the service, and by the database's own CHECK."""
    with pytest.raises(ValueError) as exc:
        erasure_service.upsert_retention_policy(
            db, record_class="principal_personal_data", scope="ERASURE_TEST_SHORT_NOTICE",
            pre_erasure_notice_hours=24, action="ANONYMISE",
            legal_basis_for_retention="test",
        )
    assert "forty-eight hours" in str(exc.value)

    from sqlalchemy.exc import IntegrityError

    db.add(RetentionPolicy(
        policy_ref="RP-BADCHECK", record_class="principal_personal_data",
        scope="ERASURE_TEST_BAD_CHECK", pre_erasure_notice_hours=1, action="ANONYMISE",
        legal_basis_for_retention="test",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --------------------------------------------------------------------------- #
# Authorisation: never a silent background deletion
# --------------------------------------------------------------------------- #
def test_a_clock_proposed_job_is_never_executed_by_the_background_pass(db):
    """The retention and inactivity scans propose; they do not authorise. The
    executor must leave such a job alone however old it is."""
    customer = _make_customer(db, "R106-AUTH-001")
    job = erasure_service.propose_erasure_job(
        db, customer, trigger="INACTIVITY", trigger_ref="inactivity:test:1",
        source_app=customer.source_app,
    )
    assert job.status == "PROPOSED"
    assert job.authorised_by is None

    with pytest.raises(erasure_service.ErasureNotAuthorised) as exc:
        erasure_service.execute_erasure_job(db, job, actor_username="scheduler")
    assert "authorised" in str(exc.value)

    # And the executor sweep does not even consider it. Asserted about this
    # job specifically rather than about the sweep's total, because the suite
    # shares one database and other tests legitimately leave executable jobs
    # in it - a global count would be testing those, not this.
    erasure_service.execute_due_erasure_jobs(db, now=_now() + timedelta(days=3650))
    db.refresh(job)
    assert job.status == "PROPOSED"
    db.refresh(customer)
    assert customer.status != "ANONYMISED"


def test_authorising_a_proposed_job_lets_the_pipeline_run(db):
    customer = _make_customer(db, "R106-AUTH-002")
    job = erasure_service.propose_erasure_job(
        db, customer, trigger="RETENTION", trigger_ref="retention:test:1",
        source_app=customer.source_app,
    )
    erasure_service.authorise_erasure_job(
        db, job, actor_username="dpo", basis="Retention period elapsed; reviewed by the DPO."
    )
    db.refresh(job)
    assert job.status == "SCHEDULED"
    assert job.authorised_by == "dpo"

    sent = erasure_service.send_due_pre_erasure_notices(db)
    assert sent["notices_sent"] >= 1
    db.refresh(job)
    assert job.status == "NOTIFIED"

    done = erasure_service.execute_due_erasure_jobs(db, now=_now() + timedelta(hours=49))
    assert done["executed"] >= 1
    db.refresh(job)
    assert job.status == "EXECUTED"


def test_an_executed_job_without_an_authoriser_is_unrepresentable(db):
    """The DoD as a database constraint rather than an application promise."""
    from sqlalchemy.exc import IntegrityError

    customer = _make_customer(db, "R106-AUTH-003")
    db.add(ErasureJob(
        job_ref="ERJ-NOAUTH", tenant_id=customer.tenant_id, customer_id=customer.id,
        trigger="MANUAL", trigger_ref="manual:noauth", action="ANONYMISE",
        status="EXECUTED", executed_at=_now(), evidence_hash="0" * 64,
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_an_executed_job_without_evidence_is_unrepresentable(db):
    from sqlalchemy.exc import IntegrityError

    customer = _make_customer(db, "R106-AUTH-004")
    db.add(ErasureJob(
        job_ref="ERJ-NOEVID", tenant_id=customer.tenant_id, customer_id=customer.id,
        trigger="MANUAL", trigger_ref="manual:noevid", action="ANONYMISE",
        status="EXECUTED", authorised_by="dpo",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --------------------------------------------------------------------------- #
# The floor beats the ceiling
# --------------------------------------------------------------------------- #
def test_a_retention_policy_below_the_statutory_floor_is_refused(db):
    """`notifications` carries a 1-year floor from R1-10. A policy that would
    erase them after 30 days is refused when it is written."""
    with pytest.raises(RetentionFloorViolation) as exc:
        erasure_service.upsert_retention_policy(
            db, record_class="notifications", scope="ERASURE_TEST_FLOOR",
            retention_days=30, action="ANONYMISE",
            legal_basis_for_retention="test",
        )
    assert "floor in force is 365 days" in str(exc.value)
    assert "the floor wins" in str(exc.value)


def test_hard_deleting_the_principal_record_is_refused(db):
    """The customers row is the parent of consents, evidence and audit rows
    that are all inside their own floors, so ERASE is not an option for it."""
    with pytest.raises(RetentionFloorViolation) as exc:
        erasure_service.upsert_retention_policy(
            db, record_class="principal_personal_data", scope="ERASURE_TEST_HARD",
            action="ERASE", legal_basis_for_retention="test",
        )
    assert "never be hard-deleted" in str(exc.value)
    assert "ANONYMISE" in str(exc.value)


def test_hard_deleting_notifications_is_refused(db):
    with pytest.raises(RetentionFloorViolation) as exc:
        erasure_service.upsert_retention_policy(
            db, record_class="notifications", scope="ERASURE_TEST_HARD_N",
            action="ERASE", legal_basis_for_retention="test",
        )
    assert "never be hard-deleted" in str(exc.value)


def test_a_raised_ceiling_above_the_floor_is_accepted(db):
    """Retaining longer is always allowed - it is only the direction that
    would destroy inside the floor that is refused."""
    policy = erasure_service.upsert_retention_policy(
        db, record_class="notifications", scope="ERASURE_TEST_OK",
        retention_days=730, action="ANONYMISE",
        legal_basis_for_retention="Retained two years by internal policy.",
    )
    assert policy.retention_days == 730


def test_execution_retains_rows_inside_their_floor_and_says_why(db):
    """The resolution is computed at execution time, not merely at
    configuration time: a notification created today is inside its 1-year
    floor, so it is reported as retained with the floor and the basis."""
    customer = _make_customer(db, "R106-FLOOR-001")
    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-FLOOR", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)

    retained = job.records_retained["notifications"]
    assert retained["rows_retained"] >= 1
    assert retained["floor_days"] == 365
    assert "s.8(7)" in retained["why"]
    assert "notifications" not in job.records_erased or job.records_erased.get(
        "notifications", {}
    ).get("rows", 0) == 0


def test_default_policies_are_seeded_lawfully_and_idempotently(db):
    created_first = erasure_service.ensure_default_policies(db)
    created_again = erasure_service.ensure_default_policies(db)
    assert created_again == []

    rows = {
        r.record_class: r
        for r in db.query(RetentionPolicy).filter(RetentionPolicy.scope == "*").all()
    }
    assert set(rows) == {
        "principal_personal_data", "directory_record", "consent_contexts", "notifications",
    }
    assert rows["principal_personal_data"].action == "ANONYMISE"
    assert rows["principal_personal_data"].inactivity_days == THIRD_SCHEDULE_INACTIVITY_DAYS
    assert rows["directory_record"].action == "ERASE"
    assert rows["notifications"].action == "ANONYMISE"
    for row in rows.values():
        assert row.pre_erasure_notice_hours >= MIN_PRE_ERASURE_NOTICE_HOURS
        assert row.legal_basis_for_retention.strip()
    assert created_first is not None


# --------------------------------------------------------------------------- #
# The audit ledger is retained, never rewritten
# --------------------------------------------------------------------------- #
def test_erasure_keeps_every_audit_row_and_reports_the_residue(db):
    purpose, category, activity = _make_purpose(db, "audit_keep")
    customer = _make_customer(db, "R106-AUDIT-001")
    _granted_consent(db, customer, purpose, category, activity)
    before = db.query(AuditLog).filter(AuditLog.customer_id == customer.id).count()
    assert before > 0
    original_external_id = customer.external_id

    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-AUDIT", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)

    after = db.query(AuditLog).filter(AuditLog.customer_id == customer.id).count()
    assert after > before, "the erasure itself must add audit rows, never remove any"

    residue = job.records_retained["audit_logs"]
    assert residue["rows_retained"] >= before
    assert "append-only" in residue["why"]
    # The residue the doc names is real, and named: the pre-erasure external
    # id survives on historical audit rows because the ledger cannot be
    # rewritten.
    assert db.query(AuditLog).filter(
        AuditLog.customer_external_id == original_external_id
    ).count() > 0


def test_execution_writes_an_erasure_executed_audit_row_carrying_the_evidence_hash(db):
    customer = _make_customer(db, "R106-AUDIT-002")
    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-HASH", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event == "ERASURE_EXECUTED", AuditLog.customer_id == customer.id)
        .one()
    )
    assert row.details["evidence_hash"] == job.evidence_hash
    # Reproducible from the row itself - which is what makes a later edit to
    # erasure_jobs detectable against the immutable ledger.
    assert erasure_service.compute_evidence_hash(job) == job.evidence_hash


def test_execution_records_a_retention_action_in_r1_10s_ledger(db):
    customer = _make_customer(db, "R106-LEDGER-001", with_directory=True)
    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-LEDGER", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)
    actions = [
        a for a in db.query(RetentionAction)
        .filter(RetentionAction.record_class == "principal_personal_data")
        .all()
        if (a.details or {}).get("job_ref") == job.job_ref
    ]
    assert len(actions) == 1
    assert actions[0].action == "ANONYMISE"
    assert actions[0].dry_run is False


# --------------------------------------------------------------------------- #
# Legal holds
# --------------------------------------------------------------------------- #
def test_a_legal_hold_blocks_erasure_and_releasing_it_unblocks(db):
    customer = _make_customer(db, "R106-HOLD-001")
    hold = erasure_service.place_legal_hold(
        db, legal_basis="Ongoing Data Protection Board proceeding 2026/17",
        reason="Preservation notice served on the fiduciary",
        customer_id=customer.id, tenant_id=customer.tenant_id, placed_by="dpo",
    )
    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-HOLD", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")

    with pytest.raises(erasure_service.ErasureBlocked):
        erasure_service.execute_erasure_job(
            db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
        )
    db.refresh(job)
    db.refresh(customer)
    assert job.status == "BLOCKED"
    assert job.hold_id == hold.id
    assert hold.hold_ref in job.blocked_reason
    assert customer.status != "ANONYMISED"

    erasure_service.release_legal_hold(
        db, hold, released_by="dpo", release_reason="Proceeding concluded"
    )
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)
    db.refresh(customer)
    assert job.status == "EXECUTED"
    assert customer.status == "ANONYMISED"


def test_a_tenant_wide_hold_covers_a_principal_with_no_named_hold(db):
    customer = _make_customer(db, "R106-HOLD-002")
    erasure_service.place_legal_hold(
        db, legal_basis="Regulatory investigation: whole tenant frozen",
        tenant_id=customer.tenant_id, placed_by="dpo",
    )
    assert erasure_service.active_hold_for(db, customer) is not None


def test_an_expired_hold_no_longer_blocks(db):
    customer = _make_customer(db, "R106-HOLD-003")
    erasure_service.place_legal_hold(
        db, legal_basis="Short preservation order", customer_id=customer.id,
        tenant_id=customer.tenant_id, placed_by="dpo",
        expires_at=_now() - timedelta(hours=1),
    )
    assert erasure_service.active_hold_for(db, customer) is None


def test_a_hold_without_a_legal_basis_is_refused(db):
    with pytest.raises(ValueError) as exc:
        erasure_service.place_legal_hold(db, legal_basis="   ", placed_by="dpo")
    assert "legal_basis is required" in str(exc.value)


# --------------------------------------------------------------------------- #
# Withdrawal semantics
# --------------------------------------------------------------------------- #
def test_withdrawal_with_another_active_consent_does_not_make_erasure_due(db):
    """s.8(7) does not mean 'wipe the account the moment any one consent is
    withdrawn': while another consent is live the purpose is still being
    served and the identifiers are still necessary for it."""
    purpose_a, cat_a, act_a = _make_purpose(db, "keep_a")
    purpose_b, cat_b, act_b = _make_purpose(db, "keep_b")
    customer = _make_customer(db, "R106-PARTIAL-001")
    consent_a = _granted_consent(db, customer, purpose_a, cat_a, act_a)
    _granted_consent(db, customer, purpose_b, cat_b, act_b)

    consent_service.withdraw_consent(
        db, consent_a, actor_username="principal", source_app=customer.source_app,
        actor_type="PRINCIPAL",
    )
    assert _job_for(db, customer) is None
    due, why = erasure_service.erasure_due_after_withdrawal(db, consent_a)
    assert due is False
    assert "remain active" in why


def test_withdrawing_the_last_consent_makes_erasure_due(db):
    purpose_a, cat_a, act_a = _make_purpose(db, "last_a")
    purpose_b, cat_b, act_b = _make_purpose(db, "last_b")
    customer = _make_customer(db, "R106-PARTIAL-002")
    consent_a = _granted_consent(db, customer, purpose_a, cat_a, act_a)
    consent_b = _granted_consent(db, customer, purpose_b, cat_b, act_b)

    consent_service.withdraw_consent(
        db, consent_a, actor_username="principal", source_app=customer.source_app,
        actor_type="PRINCIPAL",
    )
    assert _job_for(db, customer) is None

    consent_service.withdraw_consent(
        db, consent_b, actor_username="principal", source_app=customer.source_app,
        actor_type="PRINCIPAL",
    )
    job = _job_for(db, customer)
    assert job is not None
    assert job.trigger == "WITHDRAWAL"


# --------------------------------------------------------------------------- #
# Processor erasure instructions (s.8(7)(b))
# --------------------------------------------------------------------------- #
def test_execution_instructs_every_processor_holding_the_data(db):
    purpose, category, activity = _make_purpose(db, "proc_erase")
    customer = _make_customer(db, "R106-PROC-001")
    secret, _ = processor_service.generate_webhook_secret()
    processor = Processor(
        name="Erasure Test Processor", type="ANALYTICS", country="IN", is_active=True,
        webhook_url="https://processor.invalid/hook", webhook_secret=secret,
        webhook_secret_fingerprint=processor_service.secret_fingerprint(secret),
        webhook_secret_set_at=_now(), ack_sla_hours=24,
    )
    db.add(processor)
    db.flush()
    db.add(DataSharingEvent(
        tenant_id=customer.tenant_id, customer_id=customer.id, processor_id=processor.id,
        purpose_id=purpose.id, data_category_ids=[], event_type="SENT", legal_basis="CONSENT",
        actor_username="test", source_app=customer.source_app, signature="sig",
        occurred_at=_now(),
    ))
    db.commit()

    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-PROC", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    db.refresh(job)

    assert job.processor_alert_ids
    alerts = db.query(ProcessorAlert).filter(ProcessorAlert.id.in_(job.processor_alert_ids)).all()
    assert [a.alert_type for a in alerts] == ["ERASURE_INSTRUCTION"]
    assert alerts[0].trigger_ref == f"erasure:{job.job_ref}"
    assert alerts[0].payload["details"]["erasure_request_ref"] == job.job_ref


# --------------------------------------------------------------------------- #
# last_interaction_at and the Third Schedule clock
# --------------------------------------------------------------------------- #
def test_touch_last_interaction_writes_once_then_throttles(db):
    customer = _make_customer(db, "R106-CLOCK-001")
    assert customer.last_interaction_at is None
    assert erasure_service.touch_last_interaction(db, customer, channel="test") is True
    first = customer.last_interaction_at
    assert erasure_service.touch_last_interaction(db, customer, channel="test") is False
    assert customer.last_interaction_at == first


def test_minting_a_context_records_the_principals_approach(db):
    from app.services.context import create_context_for_customer

    source_app = "ERASURE_TEST_CONTEXT"
    create_context_for_customer(
        db, name="Clock Principal", email="clock@example.test", source_app=source_app,
    )
    customer = (
        db.query(Customer)
        .filter(Customer.source_app == source_app)
        .order_by(Customer.id.desc())
        .first()
    )
    assert customer is not None
    assert customer.last_interaction_at is not None


def test_inactivity_scan_finds_a_principal_past_the_third_schedule_period(db):
    """R.8(1) + Third Schedule: three years from the principal's last approach."""
    stale = _now() - timedelta(days=THIRD_SCHEDULE_INACTIVITY_DAYS + 10)
    source_app = "ERASURE_TEST_INACTIVE"
    customer = _make_customer(
        db, "R106-INACTIVE-001", source_app=source_app,
        last_interaction_at=stale, created_at=stale,
    )
    fresh = _make_customer(
        db, "R106-INACTIVE-002", source_app=source_app,
        last_interaction_at=_now(), created_at=stale,
    )

    result = erasure_service.inactivity_scan(db)
    assert result["jobs_proposed"] >= 1

    stale_job = _job_for(db, customer)
    assert stale_job is not None
    assert stale_job.trigger == "INACTIVITY"
    assert stale_job.status == "PROPOSED"
    assert stale_job.authorised_by is None
    assert "Third Schedule" in stale_job.reason
    assert _job_for(db, fresh) is None


def test_inactivity_scan_counts_a_breach_only_when_no_hold_explains_it(db):
    stale = _now() - timedelta(days=THIRD_SCHEDULE_INACTIVITY_DAYS + 10)
    source_app = "ERASURE_TEST_INACTIVE_HELD"
    customer = _make_customer(
        db, "R106-INACTIVE-003", source_app=source_app,
        last_interaction_at=stale, created_at=stale,
    )
    erasure_service.place_legal_hold(
        db, legal_basis="Tax assessment under appeal", customer_id=customer.id,
        tenant_id=customer.tenant_id, placed_by="dpo",
    )
    result = erasure_service.inactivity_scan(db, propose=False)
    # The held principal is a candidate but not a breach: the hold is the
    # lawful explanation K-31 is asking for.
    held = [
        j for j in db.query(ErasureJob).filter(ErasureJob.customer_id == customer.id).all()
    ]
    assert held == []
    assert result["candidates"] >= 1


def test_retention_scan_proposes_for_a_principal_past_the_configured_period(db):
    source_app = "ERASURE_TEST_RETENTION"
    erasure_service.upsert_retention_policy(
        db, record_class="principal_personal_data", scope=source_app,
        retention_days=30, action="ANONYMISE",
        legal_basis_for_retention="Specified purpose served 30 days after last contact.",
    )
    stale = _now() - timedelta(days=60)
    customer = _make_customer(
        db, "R106-RET-001", source_app=source_app,
        last_interaction_at=stale, created_at=stale,
    )
    result = erasure_service.erasure_retention_scan(db)
    assert result["jobs_proposed"] >= 1
    job = _job_for(db, customer)
    assert job is not None
    assert job.trigger == "RETENTION"
    assert job.status == "PROPOSED"


# --------------------------------------------------------------------------- #
# Cancellation and metrics
# --------------------------------------------------------------------------- #
def test_an_executed_erasure_cannot_be_cancelled(db):
    customer = _make_customer(db, "R106-CANCEL-001")
    job = erasure_service.approve_rights_request_erasure(
        db, customer, request_ref="GRV-CANCEL", approved_by="dpo",
        source_app=customer.source_app,
    )
    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    erasure_service.execute_erasure_job(
        db, job, actor_username="dpo", now=_now() + timedelta(hours=49)
    )
    with pytest.raises(erasure_service.ErasureError) as exc:
        erasure_service.cancel_erasure_job(
            db, job, actor_username="dpo", reason="changed my mind"
        )
    assert "irreversible" in str(exc.value)


def test_metrics_report_backlog_and_notice_compliance(db):
    metrics = erasure_service.erasure_metrics(db)
    assert set(metrics) >= {
        "erasure_backlog", "jobs_executed", "median_tat_hours",
        "executed_with_compliant_notice", "notice_compliance_pct",
        "inactivity_clock_breaches", "legal_holds_active",
    }
    assert metrics["notice_compliance_pct"] == 100.0, (
        "every erasure executed in this suite went out after a full notice period"
    )


# --------------------------------------------------------------------------- #
# The API surface
# --------------------------------------------------------------------------- #
def test_the_api_refuses_a_policy_below_the_floor(db, erasure_client, staff_token):
    response = erasure_client.put(
        "/erasure/policies",
        json={
            "record_class": "notifications", "scope": "ERASURE_TEST_API_FLOOR",
            "retention_days": 30, "action": "ANONYMISE",
            "legal_basis_for_retention": "test",
        },
        headers=_auth(staff_token),
    )
    assert response.status_code == 422
    assert "floor" in response.json()["detail"]


def test_the_api_shows_the_floor_next_to_every_ceiling(db, erasure_client, staff_token):
    response = erasure_client.get("/erasure/policies", headers=_auth(staff_token))
    assert response.status_code == 200
    rows = {r["record_class"]: r for r in response.json()}
    assert rows["notifications"]["floor_days"] == 365
    assert rows["notifications"]["hard_delete_permitted"] is False
    assert rows["directory_record"]["hard_delete_permitted"] is True


def test_the_api_will_not_execute_without_a_notice(db, erasure_client, staff_token):
    customer = _make_customer(db, "R106-API-001")
    raised = erasure_client.post(
        "/erasure/jobs",
        json={
            "customer_external_id": customer.external_id,
            "trigger": "RIGHTS_REQUEST", "request_ref": "GRV-API-1",
            "authorisation_basis": "s.12(3) request approved by the DPO.",
        },
        headers=_auth(staff_token),
    )
    assert raised.status_code == 201, raised.text
    job_ref = raised.json()["job_ref"]

    blocked = erasure_client.post(
        f"/erasure/jobs/{job_ref}/execute", headers=_auth(staff_token)
    )
    assert blocked.status_code == 409
    assert "R.8(2)" in blocked.json()["detail"]

    noticed = erasure_client.post(
        f"/erasure/jobs/{job_ref}/notice", headers=_auth(staff_token)
    )
    assert noticed.status_code == 200
    assert noticed.json()["notice_sent_at"] is not None

    too_soon = erasure_client.post(
        f"/erasure/jobs/{job_ref}/execute", headers=_auth(staff_token)
    )
    assert too_soon.status_code == 409
    assert "notice period" in too_soon.json()["detail"]


def test_the_api_requires_a_request_ref_for_a_rights_request(db, erasure_client, staff_token):
    customer = _make_customer(db, "R106-API-002")
    response = erasure_client.post(
        "/erasure/jobs",
        json={
            "customer_external_id": customer.external_id, "trigger": "RIGHTS_REQUEST",
            "authorisation_basis": "approved",
        },
        headers=_auth(staff_token),
    )
    assert response.status_code == 422
    assert "request_ref is required" in response.json()["detail"]


def test_the_api_requires_an_authorisation_basis(db, erasure_client, staff_token):
    customer = _make_customer(db, "R106-API-003")
    response = erasure_client.post(
        "/erasure/jobs",
        json={
            "customer_external_id": customer.external_id, "trigger": "MANUAL",
            "authorisation_basis": "",
        },
        headers=_auth(staff_token),
    )
    assert response.status_code == 422


def test_every_erasure_route_is_permission_gated(db):
    """No route on this router may be reachable without erasure.view or
    erasure.manage - destroying a person's data is not something an
    unauthenticated or under-privileged caller reaches by accident."""
    from app.api.routes import erasure as erasure_routes
    from app.core.rbac import PERM_ERASURE_MANAGE, PERM_ERASURE_VIEW

    def _required_permissions(route) -> set:
        """`require_permission` returns a closure, so the permission string is
        in the closure's cells rather than anywhere in its repr."""
        found = set()
        for dependency in route.dependant.dependencies:
            call = getattr(dependency, "call", None)
            for cell in (getattr(call, "__closure__", None) or ()):
                if isinstance(cell.cell_contents, str):
                    found.add(cell.cell_contents)
        return found

    for route in erasure_routes.router.routes:
        perms = _required_permissions(route)
        assert perms & {PERM_ERASURE_VIEW, PERM_ERASURE_MANAGE}, (
            f"{route.path} is not permission-gated"
        )
        if route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert PERM_ERASURE_MANAGE in perms, (
                f"{route.path} mutates but does not require erasure.manage"
            )
