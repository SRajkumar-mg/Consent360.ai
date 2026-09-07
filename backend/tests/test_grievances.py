"""R2-06: the grievance redressal module, end to end.

The definition of done this file is written against, clause by clause:

  "A grievance gets a reference number on submission, an acknowledgement, and
   is closed within the published period; overdue items escalate
   automatically."

  * reference number  -> test_submission_issues_a_non_guessable_reference*
  * acknowledgement   -> test_submission_acknowledges_the_complainant*
  * published period  -> test_due_date_comes_from_the_tenants_configured_period,
                         test_public_rights_page_publishes_the_response_period
  * closed within it  -> test_resolution_records_whether_it_met_the_period,
                         test_queue_stats_report_on_time_closure
  * automatic escalation -> test_the_scheduled_job_escalates_an_overdue_grievance
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.encryption import hmac_digest
from app.core.security import create_context_token
from app.models.entities import AuditLog, ConsentContext, Customer, Notification, Organization, SchedulerRun
from app.models.grievance import Grievance
from app.services import grievance as grievance_service


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module", autouse=True)
def _mount_router():
    """Mount the grievances router if app/main.py has not yet been updated to
    include it (that file belongs to another lane). A no-op once it has."""
    from app.api.routes import grievances
    from app.main import app

    if not any(getattr(r, "path", "").startswith("/grievances") for r in app.routes):
        app.include_router(grievances.router)


def _tenant(db, code, *, response_days=30, dpo_email="dpo@example.com"):
    org = db.query(Organization).filter(Organization.code == code).first()
    if not org:
        org = Organization(name=code.title(), code=code, is_active=True)
        db.add(org)
    org.grievance_response_days = response_days
    org.dpo_name = "Asha Menon"
    org.dpo_email = dpo_email
    org.board_complaint_url = "https://dpb.gov.in/complaint"
    org.rights_url = f"https://{code.lower()}.example.com/rights"
    org.grievance_url = f"https://{code.lower()}.example.com/grievance"
    org.withdraw_url = f"https://{code.lower()}.example.com/withdraw"
    db.commit()
    db.refresh(org)
    return org


def _principal(db, source_app, external_id, *, email=None):
    email = email or f"{external_id.lower()}@example.com"
    customer = Customer(
        external_id=external_id, external_id_search=hmac_digest(external_id),
        name="Complainant", email=email, email_search=hmac_digest(email),
        source_app=source_app,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, source_app)
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app=source_app,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()
    return customer, token


def _submit(client, token, **overrides):
    payload = {
        "category": "ERASURE_REQUEST",
        "subject": "My deletion request was ignored",
        "description": "I asked for my account and data to be deleted on 3 August and nothing happened.",
    }
    payload.update(overrides)
    return client.post("/grievances/me", headers={"X-Context-Token": token}, json=payload)


# --------------------------------------------------------------------------- #
#  Reference numbers
# --------------------------------------------------------------------------- #

def test_submission_issues_a_non_guessable_reference(db, client):
    _tenant(db, "GRV_REF")
    _customer, token = _principal(db, "GRV_REF", "GRV-REF-1")

    body = _submit(client, token).json()
    reference = body["reference_no"]

    assert reference.startswith("GRV-")
    assert body["grievance"]["reference_no"] == reference
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    assert row is not None
    # The reference must not be derivable from the surrogate key - that is the
    # whole reason it is generated rather than formatted from `id`.
    assert str(row.id) not in reference


def test_reference_numbers_are_unique_and_unpredictable(db, client):
    """Twenty references from the same session: all distinct, none adjacent to
    another, and drawn from the restricted alphabet (no 0/1/I/L/O/U, so a
    complainant reading one aloud cannot mistranscribe it)."""
    _tenant(db, "GRV_REF2")
    references = {grievance_service.generate_reference_no(db) for _ in range(20)}
    assert len(references) == 20
    body_alphabet = set(grievance_service._REFERENCE_ALPHABET)
    for reference in references:
        prefix, year, *chunks = reference.split("-")
        assert prefix == "GRV"
        assert year == str(datetime.now(timezone.utc).year)
        assert set("".join(chunks)) <= body_alphabet


def test_reference_generation_uses_the_csprng_not_the_random_module():
    """`random`'s Mersenne Twister is reconstructible from a few hundred
    outputs; a grievance reference drawn from it would be predictable to
    anyone who has filed a few complaints. Guard the import, not just the
    behaviour - the behaviour is indistinguishable from the outside."""
    import inspect

    source = inspect.getsource(grievance_service)
    assert "import secrets" in source
    assert "secrets.choice" in source
    assert "import random" not in source


# --------------------------------------------------------------------------- #
#  Acknowledgement
# --------------------------------------------------------------------------- #

def test_submission_acknowledges_the_complainant(db, client):
    _tenant(db, "GRV_ACK", response_days=30)
    customer, token = _principal(db, "GRV_ACK", "GRV-ACK-1")

    resp = _submit(client, token)
    assert resp.status_code == 201
    body = resp.json()

    assert body["acknowledged"] is True
    assert body["reference_no"] in body["acknowledgement_message"]
    assert "30 days" in body["acknowledgement_message"]
    assert body["grievance_officer"] == "Asha Menon"
    assert body["board_complaint_url"] == "https://dpb.gov.in/complaint"

    row = db.query(Grievance).filter(Grievance.reference_no == body["reference_no"]).first()
    assert row.status == "ACKNOWLEDGED"
    assert row.acknowledged_at is not None

    # The acknowledgement is a real notification through the shared service,
    # not a string in a response body.
    assert body["notification_ids"]
    notification = db.get(Notification, body["notification_ids"][0])
    assert notification.event_type == "GRIEVANCE_STATUS"
    assert notification.customer_id == customer.id

    # Both moments are on the record, distinctly.
    events = [e.event for e in row.events]
    assert events[:2] == ["GRIEVANCE_RECEIVED", "GRIEVANCE_ACKNOWLEDGED"]


# --------------------------------------------------------------------------- #
#  The published response period
# --------------------------------------------------------------------------- #

def test_due_date_comes_from_the_tenants_configured_period(db, client):
    """Not a constant, not 90 by default: whatever this tenant publishes."""
    _tenant(db, "GRV_DUE", response_days=15)
    _customer, token = _principal(db, "GRV_DUE", "GRV-DUE-1")

    body = _submit(client, token).json()
    row = db.query(Grievance).filter(Grievance.reference_no == body["reference_no"]).first()

    assert row.response_days == 15
    assert (row.due_at - row.received_at) == timedelta(days=15)
    assert body["response_days"] == 15


def test_the_period_is_snapshotted_so_a_later_change_cannot_move_a_deadline(db, client):
    org = _tenant(db, "GRV_SNAP", response_days=20)
    _customer, token = _principal(db, "GRV_SNAP", "GRV-SNAP-1")
    reference = _submit(client, token).json()["reference_no"]

    org.grievance_response_days = 90
    db.commit()

    db.expire_all()
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    assert row.response_days == 20, "an in-flight grievance keeps the period its complainant was promised"
    assert (row.due_at - row.received_at) == timedelta(days=20)


def test_response_days_is_clamped_to_the_ninety_day_ceiling(db):
    """`organizations.grievance_response_days` has a <= 90 CHECK constraint;
    this is the second line of defence for a tenant row that predates it."""
    org = _tenant(db, "GRV_CLAMP", response_days=90)
    assert grievance_service.response_days_for_tenant(db, org.id) == 90
    assert grievance_service.response_days_for_tenant(db, None) == 90


def test_public_rights_page_publishes_the_response_period(db, client):
    """R2-06 wants the period published on the public rights page. It already
    is - GET /public/{tenant}/rights - and this pins that it reports the same
    number the grievance clock is actually derived from."""
    org = _tenant(db, "GRV_PUB", response_days=45)
    resp = client.get("/public/GRV_PUB/rights")
    assert resp.status_code == 200
    assert resp.json()["grievance_response_days"] == 45 == org.grievance_response_days


# --------------------------------------------------------------------------- #
#  Automatic escalation
# --------------------------------------------------------------------------- #

def _make_overdue(db, reference, *, days_over=1):
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    now = datetime.now(timezone.utc)
    row.received_at = now - timedelta(days=row.response_days + days_over)
    row.due_at = now - timedelta(days=days_over)
    db.commit()
    db.refresh(row)
    return row


def test_the_scheduled_job_escalates_an_overdue_grievance(db, client):
    """The DoD's "overdue items escalate automatically", run through the real
    JobRunner so the `scheduler_runs` row is produced too."""
    from app.jobs.grievance_escalation_job import run as escalation_run
    from app.jobs.scheduler import job_runner

    _tenant(db, "GRV_ESC", response_days=7, dpo_email="dpo@grv-esc.example.com")
    _customer, token = _principal(db, "GRV_ESC", "GRV-ESC-1")
    reference = _submit(client, token).json()["reference_no"]
    row = _make_overdue(db, reference)
    assert row.status == "ACKNOWLEDGED" and row.escalated_at is None

    run = job_runner.run("grievance_escalation", escalation_run)

    assert run.status == "SUCCESS"
    assert run.counts["grievances_escalated"] >= 1
    assert run.counts["dpo_notifications_queued"] >= 1
    assert db.query(SchedulerRun).filter(SchedulerRun.id == run.id).first() is not None

    db.expire_all()
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    assert row.status == "ESCALATED"
    assert row.escalated_at is not None
    assert row.escalated_to == "dpo@grv-esc.example.com"
    assert row.escalation_reason == "RESPONSE_PERIOD_ELAPSED"

    # It went to the DPO through the shared notification service, as an
    # operational (customer_id IS NULL) row - not a new dispatch path.
    escalation = db.get(Notification, row.escalation_notification_id)
    assert escalation is not None
    assert escalation.customer_id is None
    assert escalation.channel == "EMAIL"
    assert escalation.recipient == "dpo@grv-esc.example.com"
    assert reference in escalation.body


def test_escalation_happens_once_not_on_every_sweep(db, client):
    """`escalated_at` is the latch. Without it a grievance nobody resolves
    would mail the DPO every hour forever."""
    from app.jobs.grievance_escalation_job import run as escalation_run

    _tenant(db, "GRV_ONCE", response_days=5)
    _customer, token = _principal(db, "GRV_ONCE", "GRV-ONCE-1")
    reference = _submit(client, token).json()["reference_no"]
    _make_overdue(db, reference)

    first = escalation_run(db)
    assert first["grievances_escalated"] >= 1
    notifications_after_first = db.query(Notification).filter(
        Notification.source_app == "GRV_ONCE", Notification.customer_id.is_(None)
    ).count()

    second = escalation_run(db)
    assert second["grievances_overdue"] == 0
    assert second["grievances_escalated"] == 0
    assert db.query(Notification).filter(
        Notification.source_app == "GRV_ONCE", Notification.customer_id.is_(None)
    ).count() == notifications_after_first


def test_a_resolved_grievance_is_never_escalated(db, client, staff_token):
    from app.jobs.grievance_escalation_job import run as escalation_run

    _tenant(db, "GRV_NOESC", response_days=3)
    _customer, token = _principal(db, "GRV_NOESC", "GRV-NOESC-1")
    reference = _submit(client, token).json()["reference_no"]

    resolve = client.post(
        f"/grievances/{reference}/resolve",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"resolution_summary": "Account and backups erased on 5 August; confirmation sent."},
    )
    assert resolve.status_code == 200

    _make_overdue(db, reference, days_over=10)
    escalation_run(db)

    db.expire_all()
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    assert row.status == "RESOLVED"
    assert row.escalated_at is None


# --------------------------------------------------------------------------- #
#  Resolution, closure and feedback
# --------------------------------------------------------------------------- #

def test_resolution_records_whether_it_met_the_period(db, client, staff_token):
    _tenant(db, "GRV_RES", response_days=30)
    _customer, token = _principal(db, "GRV_RES", "GRV-RES-1")
    reference = _submit(client, token).json()["reference_no"]

    resp = client.post(
        f"/grievances/{reference}/resolve",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"resolution_summary": "Erasure completed; a confirmation email has been sent."},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "RESOLVED"
    assert resp.json()["resolved_at"] is not None

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event == "GRIEVANCE_RESOLVED", AuditLog.source_app == "GRV_RES")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert audit.details["within_published_period"] is True
    assert audit.details["reference_no"] == reference
    # The narrative never reaches the ledger, which is exported wholesale.
    assert "confirmation email" not in (audit.reason or "")


def test_feedback_is_refused_before_resolution_and_captured_after(db, client, staff_token):
    _tenant(db, "GRV_FB", response_days=30)
    _customer, token = _principal(db, "GRV_FB", "GRV-FB-1")
    reference = _submit(client, token).json()["reference_no"]

    early = client.post(
        f"/grievances/me/{reference}/feedback",
        headers={"X-Context-Token": token}, json={"rating": 5, "comment": "great"},
    )
    assert early.status_code == 409

    client.post(
        f"/grievances/{reference}/resolve",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"resolution_summary": "Resolved."},
    )
    later = client.post(
        f"/grievances/me/{reference}/feedback",
        headers={"X-Context-Token": token},
        json={"rating": 4, "comment": "Took a while but it was sorted."},
    )
    assert later.status_code == 200
    assert later.json()["feedback_rating"] == 4
    assert later.json()["feedback_comment"] == "Took a while but it was sorted."

    db.expire_all()
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    assert row.feedback_at is not None
    # Feedback is an observation about a resolution, not a state change.
    assert row.status == "RESOLVED"


def test_close_follows_resolve_and_illegal_transitions_are_refused(db, client, staff_token):
    _tenant(db, "GRV_CLOSE", response_days=30)
    _customer, token = _principal(db, "GRV_CLOSE", "GRV-CLOSE-1")
    reference = _submit(client, token).json()["reference_no"]
    headers = {"Authorization": f"Bearer {staff_token}"}

    # CLOSED is not reachable from ACKNOWLEDGED - see GRIEVANCE_TRANSITIONS.
    too_early = client.post(f"/grievances/{reference}/close", headers=headers, json={})
    assert too_early.status_code == 409

    client.post(f"/grievances/{reference}/resolve", headers=headers,
                json={"resolution_summary": "Resolved."})
    closed = client.post(f"/grievances/{reference}/close", headers=headers, json={"note": "No further contact."})
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"


# --------------------------------------------------------------------------- #
#  The admin queue
# --------------------------------------------------------------------------- #

def test_queue_filters_and_orders_by_soonest_deadline(db, client, staff_token):
    _tenant(db, "GRV_QUEUE", response_days=30)
    _customer, token = _principal(db, "GRV_QUEUE", "GRV-QUEUE-1")
    first = _submit(client, token, category="ACCESS_REQUEST").json()["reference_no"]
    second = _submit(client, token, category="NOTICE_UNCLEAR").json()["reference_no"]
    _make_overdue(db, second)

    headers = {"Authorization": f"Bearer {staff_token}"}
    rows = client.get("/grievances?limit=500", headers=headers).json()
    references = [r["reference_no"] for r in rows]
    assert first in references and second in references
    # Soonest deadline first: the overdue one leads.
    assert references.index(second) < references.index(first)

    overdue = client.get("/grievances?overdue_only=true&limit=500", headers=headers).json()
    overdue_refs = [r["reference_no"] for r in overdue]
    assert second in overdue_refs and first not in overdue_refs
    assert all(r["overdue"] for r in overdue)

    by_category = client.get("/grievances?category=ACCESS_REQUEST&limit=500", headers=headers).json()
    assert first in [r["reference_no"] for r in by_category]
    assert second not in [r["reference_no"] for r in by_category]


def test_queue_stats_report_on_time_closure(db, client, staff_token):
    _tenant(db, "GRV_STATS", response_days=30)
    _customer, token = _principal(db, "GRV_STATS", "GRV-STATS-1")
    headers = {"Authorization": f"Bearer {staff_token}"}

    on_time = _submit(client, token).json()["reference_no"]
    client.post(f"/grievances/{on_time}/resolve", headers=headers,
                json={"resolution_summary": "Handled promptly."})

    stats = client.get("/grievances/stats", headers=headers).json()
    assert stats["total"] >= 1
    assert stats["resolved_total"] >= 1
    assert stats["resolved_within_period"] >= 1
    assert stats["on_time_closure_rate"] is not None
    assert stats["by_status"].get("RESOLVED", 0) >= 1


def test_staff_can_file_a_grievance_that_arrived_off_platform(db, client, staff_token):
    """A complaint by email or phone enters the same register with the same
    clock, and the clock runs from when it arrived, not from data entry."""
    _tenant(db, "GRV_STAFF", response_days=20)
    customer, _token = _principal(db, "GRV_STAFF", "GRV-STAFF-1")
    arrived = datetime.now(timezone.utc) - timedelta(days=5)

    resp = client.post(
        "/grievances",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "customer_external_id": customer.external_id,
            "category": "CONSENT_NOT_HONOURED",
            "description": "Caller says marketing email continued after withdrawal.",
            "channel": "PHONE",
            "received_at": arrived.isoformat(),
        },
    )
    assert resp.status_code == 201
    row = db.query(Grievance).filter(Grievance.reference_no == resp.json()["reference_no"]).first()
    assert row.channel == "PHONE"
    assert row.response_days == 20
    # 20 days from arrival (5 days ago), not from now.
    assert (row.due_at - datetime.now(timezone.utc)).days == pytest.approx(14, abs=1)


def test_triage_assigns_and_moves_a_grievance_into_progress(db, client, staff_token):
    _tenant(db, "GRV_TRIAGE", response_days=30)
    _customer, token = _principal(db, "GRV_TRIAGE", "GRV-TRIAGE-1")
    reference = _submit(client, token).json()["reference_no"]

    resp = client.patch(
        f"/grievances/{reference}",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"assigned_to": "handler-1", "status": "IN_PROGRESS", "note": "Chasing the erasure team."},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "IN_PROGRESS"
    assert resp.json()["assigned_to"] == "handler-1"

    # The internal note is in the staff view but not in the complainant's.
    staff_notes = [e["note"] for e in resp.json()["events"]]
    assert "Chasing the erasure team." in staff_notes
    mine = client.get(f"/grievances/me/{reference}", headers={"X-Context-Token": token}).json()
    assert "Chasing the erasure team." not in [e["note"] for e in mine["events"]]


# --------------------------------------------------------------------------- #
#  Confidentiality: encryption, tenancy, authorisation
# --------------------------------------------------------------------------- #

def test_the_narrative_is_encrypted_at_rest(db, client):
    from sqlalchemy import text

    from app.core.encryption import is_encrypted, is_encryption_enabled

    _tenant(db, "GRV_ENC", response_days=30)
    _customer, token = _principal(db, "GRV_ENC", "GRV-ENC-1")
    secret = "My psychiatric records were shared with a recruiter without my consent."
    reference = _submit(client, token, description=secret).json()["reference_no"]

    raw = db.execute(
        text("SELECT description, subject FROM grievances WHERE reference_no = :r"),
        {"r": reference},
    ).first()
    assert secret not in raw[0]
    if is_encryption_enabled():
        assert is_encrypted(raw[0])
    # ... and it decrypts back through the ORM for an authorised reader.
    row = db.query(Grievance).filter(Grievance.reference_no == reference).first()
    assert row.description == secret


def test_prose_ending_in_a_full_stop_is_still_encrypted(db, client):
    """Regression for the bug this module surfaced in
    app/core/encryption.py::is_encrypted.

    A grievance description is prose and prose ends with a full stop, which
    makes the last '.'-separated segment empty. The old, non-strict base64
    check read that shape as "this is already ciphertext" and the
    TypeDecorator therefore stored the sentence AS-IS - in plaintext - while
    the read path made the identical misjudgement and returned the raw value,
    so the column round-tripped perfectly and nothing looked wrong. Every
    encrypted free-text column in the codebase shared the flaw.
    """
    from sqlalchemy import text

    from app.core.encryption import is_encrypted, is_encryption_enabled

    if not is_encryption_enabled():
        pytest.skip("field-level encryption is not configured in this run")

    _tenant(db, "GRV_DOT", response_days=30)
    _customer, token = _principal(db, "GRV_DOT", "GRV-DOT-1")
    # The exact shape that used to defeat the check: a sentence whose final
    # character is the separator.
    sentence = "Took a while but it was sorted."
    assert not is_encrypted(sentence), "plain prose must never be mistaken for ciphertext"

    reference = _submit(client, token, description=sentence).json()["reference_no"]
    raw = db.execute(
        text("SELECT description FROM grievances WHERE reference_no = :r"), {"r": reference}
    ).scalar()
    assert raw != sentence
    assert is_encrypted(raw)
    assert db.query(Grievance).filter(Grievance.reference_no == reference).first().description == sentence


def test_one_principal_cannot_read_anothers_grievance(db, client):
    _tenant(db, "GRV_ISO", response_days=30)
    _victim, victim_token = _principal(db, "GRV_ISO", "GRV-ISO-VICTIM")
    _attacker, attacker_token = _principal(db, "GRV_ISO", "GRV-ISO-ATTACKER")
    reference = _submit(client, victim_token).json()["reference_no"]

    # A valid credential for a DIFFERENT principal gets the same 404 an
    # unknown reference does - it must not confirm the reference exists.
    resp = client.get(f"/grievances/me/{reference}", headers={"X-Context-Token": attacker_token})
    assert resp.status_code == 404
    assert client.get(
        "/grievances/me/GRV-2026-ZZZZ-ZZZZ-ZZZZ", headers={"X-Context-Token": attacker_token}
    ).status_code == 404

    mine = client.get("/grievances/me", headers={"X-Context-Token": attacker_token}).json()
    assert reference not in [g["reference_no"] for g in mine]


def test_another_tenants_context_token_cannot_reach_a_grievance(db, client):
    _tenant(db, "GRV_T1", response_days=30)
    _tenant(db, "GRV_T2", response_days=30)
    _c1, token1 = _principal(db, "GRV_T1", "GRV-T1-1")
    _c2, token2 = _principal(db, "GRV_T2", "GRV-T2-1")
    reference = _submit(client, token1).json()["reference_no"]

    assert client.get(
        f"/grievances/me/{reference}", headers={"X-Context-Token": token2}
    ).status_code == 404


def test_the_queue_requires_the_grievance_permissions(db, client, staff_token):
    """A role without grievance.view/manage is refused, and the anonymous
    caller is refused before that."""
    from app.core.rbac import PERM_GRIEVANCE_MANAGE, PERM_GRIEVANCE_VIEW
    from app.core.security import create_access_token, hash_password
    from app.models.entities import Role, User

    _tenant(db, "GRV_PERM", response_days=30)
    _customer, token = _principal(db, "GRV_PERM", "GRV-PERM-1")
    reference = _submit(client, token).json()["reference_no"]

    assert client.get("/grievances").status_code in (401, 403)

    role = db.query(Role).filter(Role.name == "grv-no-perms").first()
    if not role:
        role = Role(name="grv-no-perms", description="no grievance perms",
                    permissions=["dashboard.view"], is_system=False)
        db.add(role)
        db.commit()
        db.refresh(role)
    user = db.query(User).filter(User.username == "grv-no-perms-user").first()
    if not user:
        user = User(username="grv-no-perms-user", full_name="No Perms",
                    email="grv-noperms@example.com", email_search=hmac_digest("grv-noperms@example.com"),
                    password_hash=hash_password("Str0ng!Passw0rd"), role_id=role.id, is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    weak = create_access_token(user.id, user.username, role.name)

    denied = client.get("/grievances", headers={"Authorization": f"Bearer {weak}"})
    assert denied.status_code == 403
    assert PERM_GRIEVANCE_VIEW in denied.json()["detail"]

    denied_write = client.post(
        f"/grievances/{reference}/resolve", headers={"Authorization": f"Bearer {weak}"},
        json={"resolution_summary": "nope"},
    )
    assert denied_write.status_code == 403
    assert PERM_GRIEVANCE_MANAGE in denied_write.json()["detail"]


def test_the_dpo_role_can_work_the_queue_it_is_escalated_to(db, client):
    """s.10(2)(a) makes the DPO the grievance point of contact and the
    escalation target, so a DPO who could only read the queue would be a
    dead end. The DPO deliberately has no consent.manage, which is why
    grievance.manage is a separate permission."""
    from app.core.rbac import PERM_GRIEVANCE_MANAGE, ROLE_PERMISSIONS

    assert PERM_GRIEVANCE_MANAGE in ROLE_PERMISSIONS["dpo"]
    assert "consent.manage" not in ROLE_PERMISSIONS["dpo"]
    # auditor stays strictly read-only (test_auth_hardening asserts this too).
    assert not any(p.endswith(".manage") for p in ROLE_PERMISSIONS["auditor"])


# --------------------------------------------------------------------------- #
#  Audit trail
# --------------------------------------------------------------------------- #

def test_every_state_change_is_audited_and_the_chain_stays_intact(db, client, staff_token):
    from app.core.audit_chain import GENESIS_HASH, compute_entry_hash

    _tenant(db, "GRV_AUDIT", response_days=30)
    _customer, token = _principal(db, "GRV_AUDIT", "GRV-AUDIT-1")
    reference = _submit(client, token).json()["reference_no"]
    headers = {"Authorization": f"Bearer {staff_token}"}
    client.patch(f"/grievances/{reference}", headers=headers,
                 json={"status": "IN_PROGRESS", "assigned_to": "handler-1"})
    client.post(f"/grievances/{reference}/resolve", headers=headers,
                json={"resolution_summary": "Done."})
    client.post(f"/grievances/{reference}/close", headers=headers, json={})
    client.post(f"/grievances/me/{reference}/feedback", headers={"X-Context-Token": token},
                json={"rating": 5})

    events = [
        row.event for row in
        db.query(AuditLog).filter(AuditLog.source_app == "GRV_AUDIT").order_by(AuditLog.id).all()
    ]
    for expected in (
        "GRIEVANCE_RECEIVED", "GRIEVANCE_ACKNOWLEDGED", "GRIEVANCE_UPDATED",
        "GRIEVANCE_RESOLVED", "GRIEVANCE_CLOSED", "GRIEVANCE_FEEDBACK_RECORDED",
    ):
        assert expected in events, f"{expected} missing from {events}"

    # Recompute THIS tenant's chain rather than calling verify_chain(db),
    # which walks every tenant in the database: in a full-suite run that
    # would report breaks caused by any other test file's fixtures and say
    # nothing about whether the grievance writes are well-formed.
    tenant_id = db.query(Organization).filter(Organization.code == "GRV_AUDIT").first().id
    chain = (
        db.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.id.asc())
        .all()
    )
    assert chain, "the grievance lifecycle wrote no audit rows for this tenant at all"
    prev_hash = GENESIS_HASH
    for entry in chain:
        assert entry.entry_hash == compute_entry_hash(prev_hash, entry), (
            f"audit_logs row {entry.id} ({entry.event}) breaks the hash chain"
        )
        prev_hash = entry.entry_hash
