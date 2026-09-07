"""R3-06 (O-01, O-02, O-04, C-04): the notification service.

Every import that transitively reaches app/integrations/notifications/email.py
(app.services.notifications, app.api.routes.notifications, app.main) is
deliberately LOCAL (inside a fixture/function), never at module level: pytest
collects (imports) every test module up front, before any fixture - including
conftest.py's session-scoped `_test_database`, which runs Alembic in-process
and Alembic's env.py calls `logging.config.fileConfig(..., disable_existing_loggers=True)`
- disabling every logger that already exists at that moment. A module-level
import here would create app.integrations.notifications.email's logger during
COLLECTION, i.e. before that one fileConfig call, and get it silently disabled
for the rest of the session (breaking tests/test_verification.py's caplog
assertion on that exact logger - found by running the full suite). See
tests/test_observability.py's own `_reenable_consent360_loggers` fixture for
the identical root cause hitting different loggers.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.entities import Customer, Notification


@pytest.fixture()
def notification_service():
    from app.services import notifications

    return notifications


@pytest.fixture()
def extra_client(db):
    """These two routers are not mounted in app.main (that file is out of
    this lane's scope - see the task's own instructions); exercised here
    against a standalone app that shares the same dependency wiring
    (get_current_user/require_permission are the same functions, so a real
    staff_token Bearer header authenticates identically)."""
    from app.api.routes import decisions as decisions_routes
    from app.api.routes import notifications as notifications_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(notifications_routes.router)
    app.include_router(decisions_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _customer(db, *, external_id, email="", phone="", source_app="NOTIF_TEST"):
    customer = Customer(external_id=external_id, name="Notif Customer", email=email, phone=phone,
                         source_app=source_app, status="ACTIVE")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _purpose(db, code):
    from app.models.entities import DataCategory, ProcessingActivity, Purpose, PurposeVersion

    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test", data_items=[
            {"data_category_id": category.id, "necessity": True, "description": ""}
        ],
    ))
    db.commit()
    return purpose, category, activity


def test_queue_notification_creates_email_and_in_app_rows(db, notification_service):
    customer = _customer(db, external_id="CUST-NOTIF-1", email="notif1@example.com")
    queued = notification_service.queue_notification(
        db, customer=customer, event_type="CONSENT_ACKNOWLEDGEMENT", source_app="NOTIF_TEST",
        context={"purpose_name": "Marketing"},
    )
    channels = {n.channel for n in queued}
    assert channels == {"EMAIL", "IN_APP"}
    for n in queued:
        assert n.status == "PENDING"
        assert n.event_type == "CONSENT_ACKNOWLEDGEMENT"
        assert "Marketing" in n.body
    email_row = next(n for n in queued if n.channel == "EMAIL")
    assert email_row.recipient == "notif1@example.com"
    assert email_row.recipient_search is not None


def test_queue_notification_skips_channel_with_no_recipient(db, notification_service):
    customer = _customer(db, external_id="CUST-NOTIF-2", email="")
    queued = notification_service.queue_notification(
        db, customer=customer, event_type="ERASURE_WARNING_48H", source_app="NOTIF_TEST",
        channels=["EMAIL", "SMS", "IN_APP"], context={},
    )
    channels = {n.channel for n in queued}
    # No email and no phone on file - only IN_APP (which needs no recipient) is queued.
    assert channels == {"IN_APP"}


def test_dispatch_pending_delivers_and_sets_provider_ref(db, notification_service):
    customer = _customer(db, external_id="CUST-NOTIF-3", email="notif3@example.com")
    notification_service.queue_notification(
        db, customer=customer, event_type="WITHDRAWAL_CONFIRMATION", source_app="NOTIF_TEST",
        context={"purpose_name": "Analytics"},
    )
    result = notification_service.dispatch_pending(db)
    assert result["attempted"] >= 2
    rows = db.query(Notification).filter(Notification.customer_id == customer.id).all()
    for n in rows:
        assert n.status == "DELIVERED"
        assert n.provider_ref
        assert n.sent_at is not None
        assert n.delivered_at is not None


def test_dispatch_pending_retries_then_fails_after_max_retries(db, monkeypatch, notification_service):
    customer = _customer(db, external_id="CUST-NOTIF-4", email="notif4@example.com")
    [n] = notification_service.queue_notification(
        db, customer=customer, event_type="BREACH_NOTICE", source_app="NOTIF_TEST",
        channels=["EMAIL"], context={"details": "test breach"},
    )
    n.max_retries = 2

    def _boom(*args, **kwargs):
        raise RuntimeError("SMTP unreachable")

    monkeypatch.setattr(notification_service, "get_email_sender", lambda: type("S", (), {"send": staticmethod(_boom)})())

    ok1 = notification_service._attempt_send(db, n)
    assert ok1 is False
    db.refresh(n)
    assert n.status == "PENDING"
    assert n.retry_count == 1
    assert n.next_attempt_at > datetime.now(timezone.utc)

    ok2 = notification_service._attempt_send(db, n)
    assert ok2 is False
    db.refresh(n)
    assert n.status == "FAILED"
    assert n.retry_count == 2
    assert n.last_error and "SMTP unreachable" in n.last_error


def test_acknowledge_notification_requires_delivered_status(db, notification_service):
    customer = _customer(db, external_id="CUST-NOTIF-5", email="notif5@example.com")
    [n] = notification_service.queue_notification(
        db, customer=customer, event_type="RENEWAL_REMINDER", source_app="NOTIF_TEST",
        channels=["IN_APP"], context={},
    )
    with pytest.raises(ValueError):
        notification_service.acknowledge_notification(db, n, actor_username="test")

    notification_service.dispatch_pending(db)
    db.refresh(n)
    acked = notification_service.acknowledge_notification(db, n, actor_username="test")
    assert acked.status == "ACKNOWLEDGED"
    assert acked.acknowledged_at is not None


def test_grant_consent_queues_consent_acknowledgement_notification(db):
    from app.services import consent as consent_service

    purpose, category, activity = _purpose(db, "notif_grant")
    customer = _customer(db, external_id="CUST-NOTIF-6", email="notif6@example.com")
    consent, _created = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="NOTIF_TEST",
    )
    consent_service.grant_consent(db, consent, actor_username="test", source_app="NOTIF_TEST")

    rows = db.query(Notification).filter(
        Notification.customer_id == customer.id, Notification.event_type == "CONSENT_ACKNOWLEDGEMENT"
    ).all()
    assert rows, "grant_consent must queue a CONSENT_ACKNOWLEDGEMENT notification"


def test_withdraw_consent_queues_withdrawal_confirmation_notification(db):
    from app.services import consent as consent_service

    purpose, category, activity = _purpose(db, "notif_withdraw")
    customer = _customer(db, external_id="CUST-NOTIF-7", email="notif7@example.com")
    consent, _created = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="NOTIF_TEST",
    )
    consent_service.grant_consent(db, consent, actor_username="test", source_app="NOTIF_TEST")
    consent_service.withdraw_consent(db, consent, actor_username="test", source_app="NOTIF_TEST")

    rows = db.query(Notification).filter(
        Notification.customer_id == customer.id, Notification.event_type == "WITHDRAWAL_CONFIRMATION"
    ).all()
    assert rows, "withdraw_consent must queue a WITHDRAWAL_CONFIRMATION notification"


def test_renewal_reminders_job_is_idempotent_per_expiry_cycle(db):
    from app.jobs import renewal_reminders_job
    from app.services import consent as consent_service

    purpose, category, activity = _purpose(db, "notif_renew")
    customer = _customer(db, external_id="CUST-NOTIF-8", email="notif8@example.com")
    consent, _created = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="NOTIF_TEST",
    )
    consent_service.grant_consent(db, consent, expires_in_days=10, actor_username="test", source_app="NOTIF_TEST")

    # The job scans every consent in the database and the test database is
    # not truncated between runs, so neither the global counts nor this
    # customer's row count can be pinned to an exact number. The property
    # this test names is idempotency per expiry cycle: a first run queues
    # this consent's reminder, a second run queues nothing at all and
    # reports the first one as already handled.
    result1 = renewal_reminders_job.run(db)
    assert result1["reminders_queued"] + result1["already_queued"] >= 1

    result2 = renewal_reminders_job.run(db)
    assert result2["reminders_queued"] == 0
    assert result2["already_queued"] >= 1


def test_notification_delivery_metrics_kpi(db, notification_service):
    from app.services.kpi import notification_delivery_metrics

    customer = _customer(db, external_id="CUST-NOTIF-9", email="notif9@example.com")
    notification_service.queue_notification(
        db, customer=customer, event_type="LEGACY_NOTICE", source_app="NOTIF_TEST",
        channels=["EMAIL", "IN_APP"], context={"details": "legacy data notice"},
    )
    notification_service.dispatch_pending(db)
    metrics = notification_delivery_metrics(db)
    # This is a global (not per-customer) metric, so other tests in the same
    # shared test database contribute rows too - assert shape and bounds,
    # not an exact rate.
    assert metrics["notifications_delivered"] >= 2
    assert 0.0 <= metrics["notification_delivery_rate_pct"] <= 100.0
    assert "EMAIL" in metrics["by_channel"]
    assert "IN_APP" in metrics["by_channel"]
    for channel_stats in metrics["by_channel"].values():
        assert channel_stats["delivered"] <= channel_stats["attempted"]


def test_staff_trigger_and_list_notifications(db, extra_client, staff_token):
    customer = _customer(db, external_id="CUST-NOTIF-10", email="notif10@example.com", source_app="NOTIF_STAFF")
    resp = extra_client.post(
        "/notifications/trigger",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"customer_external_id": customer.external_id, "event_type": "GRIEVANCE_STATUS",
              "context": {"status": "under review"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(n["event_type"] == "GRIEVANCE_STATUS" for n in body)

    list_resp = extra_client.get(
        "/notifications", headers={"Authorization": f"Bearer {staff_token}"},
        params={"customer_external_id": customer.external_id},
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1

    metrics_resp = extra_client.get("/notifications/metrics", headers={"Authorization": f"Bearer {staff_token}"})
    assert metrics_resp.status_code == 200
    assert "notification_delivery_rate_pct" in metrics_resp.json()


def test_staff_template_crud(db, extra_client, staff_token):
    create_resp = extra_client.post(
        "/notifications/templates",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"event_type": "LEGACY_NOTICE", "channel": "EMAIL", "language": "hi",
              "subject": "test", "body_template": "Hello {customer_name}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    template_id = create_resp.json()["id"]

    dup_resp = extra_client.post(
        "/notifications/templates",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"event_type": "LEGACY_NOTICE", "channel": "EMAIL", "language": "hi",
              "subject": "test2", "body_template": "Hi again"},
    )
    assert dup_resp.status_code == 409

    update_resp = extra_client.put(
        f"/notifications/templates/{template_id}",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"subject": "updated subject"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["subject"] == "updated subject"

    list_resp = extra_client.get("/notifications/templates", headers={"Authorization": f"Bearer {staff_token}"})
    assert any(t["id"] == template_id for t in list_resp.json())
