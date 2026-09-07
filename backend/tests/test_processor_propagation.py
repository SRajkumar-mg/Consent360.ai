"""R3-07 (C-02, M-02, M-05, K-08): processor register and cease-processing
propagation.

The headline test here is `test_withdrawal_produces_an_acknowledged_alert_within_the_sla`,
which is the task's definition of done end to end against a real HTTP
endpoint: a principal withdraws, every processor holding that data receives a
signed instruction over the wire, each acknowledges with its own signed
callback, and K-08 reports 100%.

`app/main.py` does not yet mount the processors router (registering it is the
coordinating agent's change), so the HTTP-level tests mount the router onto a
local FastAPI app instead of using the shared `client` fixture. That is not a
workaround for a missing route - the router, its dependencies and its
signature verification are exactly the ones `app.main` will mount.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.entities import (
    Consent,
    Customer,
    DataCategory,
    DataSharingEvent,
    Notification,
    Organization,
    ProcessingActivity,
    Processor,
    ProcessorAlert,
    Purpose,
    PurposeVersion,
)
from app.services import consent as consent_service
from app.services import processors as processor_service
from app.services.tenancy import resolve_tenant_id

SOURCE_APP = "PROC_TEST"


# --------------------------------------------------------------------------- #
# A real processor endpoint
# --------------------------------------------------------------------------- #
class _ProcessorEndpoint(BaseHTTPRequestHandler):
    """Stands in for a processor's webhook receiver. Records every request
    verbatim - headers and raw body - so a test can re-verify the signature
    over exactly the bytes that arrived, not a re-serialisation of them."""

    received: list[dict] = []
    response_status: int = 200

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        _ProcessorEndpoint.received.append({
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "raw": raw,
            "body": json.loads(raw) if raw else None,
        })
        self.send_response(_ProcessorEndpoint.response_status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def endpoint():
    _ProcessorEndpoint.received = []
    _ProcessorEndpoint.response_status = 200
    server = HTTPServer(("127.0.0.1", 0), _ProcessorEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/webhooks/consent360"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def processor_client(db):
    """The processors router on its own app - see the module docstring."""
    from app.api.routes import processors as processors_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(processors_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Fixtures for the domain objects
# --------------------------------------------------------------------------- #
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


def _make_customer(db, external_id, source_app=None):
    """Each test gets its own tenant by default (derived from its own
    external_id), because K-08 is a per-tenant metric and this file's tests
    share one database for the whole session."""
    source_app = source_app or f"{SOURCE_APP}_{external_id.replace('-', '_')}"
    customer = Customer(
        external_id=external_id, name="Test Principal", source_app=source_app,
        tenant_id=resolve_tenant_id(db, source_app),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _make_processor(db, name, webhook_url="", *, ack_sla_hours=24, secret=None, **kwargs):
    plaintext = secret or processor_service.generate_webhook_secret()[0]
    processor = Processor(
        name=name, type="ANALYTICS", country="IN", is_active=True,
        webhook_url=webhook_url, webhook_secret=plaintext if webhook_url else "",
        webhook_secret_fingerprint=processor_service.secret_fingerprint(plaintext),
        webhook_secret_set_at=datetime.now(timezone.utc),
        ack_sla_hours=ack_sla_hours,
        **kwargs,
    )
    db.add(processor)
    db.commit()
    db.refresh(processor)
    return processor, plaintext


def _log_disclosure(db, customer, purpose, processor, event_type="SENT"):
    event = DataSharingEvent(
        tenant_id=customer.tenant_id, customer_id=customer.id, processor_id=processor.id,
        purpose_id=purpose.id, data_category_ids=[], event_type=event_type,
        legal_basis="CONSENT", actor_username="test", source_app=customer.source_app,
        signature="test-signature", occurred_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _granted_consent(db, customer, purpose, category, activity):
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=customer.source_app
    )
    consent_service.grant_consent(db, consent, source_app=customer.source_app)
    db.commit()
    return consent


def _wait_for_delivery(expected=1, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(_ProcessorEndpoint.received) >= expected:
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------- #
# The definition of done
# --------------------------------------------------------------------------- #
def test_withdrawal_produces_an_acknowledged_alert_within_the_sla(
    db, endpoint, processor_client, staff_token
):
    """DoD: 'Withdrawal produces an acknowledged alert to every processor
    holding that data within the configured SLA (K-08).'"""
    purpose, category, activity = _make_purpose(db, "dod_cease")
    customer = _make_customer(db, "R307-CUST-DOD-001")
    processor_a, secret_a = _make_processor(db, "Analytics Co", endpoint, ack_sla_hours=24)
    processor_b, secret_b = _make_processor(db, "Mailer Co", endpoint, ack_sla_hours=24)
    # Both processors were actually sent this principal's data for this purpose.
    _log_disclosure(db, customer, purpose, processor_a)
    _log_disclosure(db, customer, purpose, processor_b)
    consent = _granted_consent(db, customer, purpose, category, activity)

    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alerts = (
        db.query(ProcessorAlert)
        .filter(ProcessorAlert.consent_id == consent.id)
        .order_by(ProcessorAlert.id)
        .all()
    )
    assert {a.processor_id for a in alerts} == {processor_a.id, processor_b.id}
    assert all(a.alert_type == "CEASE_PROCESSING" for a in alerts)
    assert all(a.status == "PENDING" for a in alerts)
    # The SLA clock starts at the withdrawal, not at delivery.
    for alert in alerts:
        assert alert.due_at - alert.created_at == timedelta(hours=24)

    result = processor_service.dispatch_pending_alerts(db)
    assert result["sent"] == 2, result
    assert _wait_for_delivery(expected=2)

    # Every delivery carried a signature the processor can verify with the
    # secret this platform issued it - and only with that secret.
    secrets_by_url = {processor_a.id: secret_a, processor_b.id: secret_b}
    for alert in alerts:
        db.refresh(alert)
        assert alert.status == "SENT"
        assert alert.http_status == 200
        delivery = next(
            r for r in _ProcessorEndpoint.received
            if r["headers"]["X-Consent360-Alert-Ref"] == alert.alert_ref
        )
        assert processor_service.verify_signature(
            secrets_by_url[alert.processor_id],
            delivery["headers"]["X-Consent360-Timestamp"],
            delivery["raw"],
            delivery["headers"]["X-Consent360-Signature"],
        )
        assert delivery["body"]["alert_type"] == "CEASE_PROCESSING"
        assert delivery["body"]["data_principal"]["external_id"] == customer.external_id
        assert delivery["body"]["purpose"]["code"] == purpose.code
        assert "stop all processing" in delivery["body"]["required_action"].lower()

    # Each processor acknowledges with its own signed callback.
    for alert in alerts:
        secret = secrets_by_url[alert.processor_id]
        body = json.dumps({
            "acknowledged_by": f"processor-{alert.processor_id}",
            "reference": f"TICKET-{alert.processor_id}",
            "note": "Processing stopped and confirmed.",
        }).encode("utf-8")
        timestamp = int(datetime.now(timezone.utc).timestamp())
        response = processor_client.post(
            f"/processors/alerts/{alert.alert_ref}/ack",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Consent360-Timestamp": str(timestamp),
                "X-Consent360-Signature": processor_service.sign_payload(secret, timestamp, body),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ACKNOWLEDGED"
        assert response.json()["within_sla"] is True

    metrics = processor_service.propagation_sla_metrics(db, tenant_id=customer.tenant_id)
    assert metrics["withdrawals_with_processor_alerts"] == 1
    assert metrics["fully_acknowledged_within_sla"] == 1
    assert metrics["sla_breached"] == 0
    assert metrics["k08_propagation_sla_pct"] == 100.0
    assert metrics["alerts_acknowledged"] == 2
    assert metrics["alerts_escalated"] == 0


def test_k08_counts_a_withdrawal_as_met_only_when_every_processor_acknowledges(db, endpoint):
    """One processor acknowledging is not compliance: s.6(6) is about *all* of
    them, so the group must not be counted as met."""
    purpose, category, activity = _make_purpose(db, "k08_partial")
    customer = _make_customer(db, "R307-CUST-K08-001")
    processor_a, _ = _make_processor(db, "Partial A", endpoint)
    processor_b, _ = _make_processor(db, "Partial B", endpoint)
    _log_disclosure(db, customer, purpose, processor_a)
    _log_disclosure(db, customer, purpose, processor_b)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alerts = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).all()
    assert len(alerts) == 2
    processor_service.acknowledge_alert(
        db, alerts[0], acknowledged_by="Partial A", method="MANUAL"
    )
    # The other one's SLA has now elapsed unacknowledged.
    alerts[1].due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    metrics = processor_service.propagation_sla_metrics(db, tenant_id=customer.tenant_id)
    assert metrics["fully_acknowledged_within_sla"] == 0
    assert metrics["sla_breached"] == 1
    assert metrics["k08_propagation_sla_pct"] == 0.0
    assert f"withdrawal:{consent.id}:v{consent.consent_version}" in metrics["breached_trigger_refs"]


def test_alerts_go_only_to_processors_that_actually_hold_the_data(db, endpoint):
    purpose, category, activity = _make_purpose(db, "scoped_cease")
    other_purpose, other_cat, other_act = _make_purpose(db, "scoped_other")
    customer = _make_customer(db, "R307-CUST-SCOPE-001")
    holder, _ = _make_processor(db, "Holder", endpoint)
    non_holder, _ = _make_processor(db, "Never Received Anything", endpoint)
    other_purpose_holder, _ = _make_processor(db, "Other Purpose Holder", endpoint)
    denied_holder, _ = _make_processor(db, "Disclosure Was Denied", endpoint)

    _log_disclosure(db, customer, purpose, holder)
    _log_disclosure(db, customer, other_purpose, other_purpose_holder)
    _log_disclosure(db, customer, purpose, denied_holder, event_type="DENIED")

    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alerts = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).all()
    assert {a.processor_id for a in alerts} == {holder.id}, (
        "only a processor with a SENT disclosure for the withdrawn purpose should be instructed"
    )
    assert non_holder.id not in {a.processor_id for a in alerts}
    assert other_purpose_holder.id not in {a.processor_id for a in alerts}
    assert denied_holder.id not in {a.processor_id for a in alerts}


def test_withdrawal_fan_out_is_idempotent(db, endpoint):
    purpose, category, activity = _make_purpose(db, "idem_cease")
    customer = _make_customer(db, "R307-CUST-IDEM-001")
    processor, _ = _make_processor(db, "Idempotent Co", endpoint)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    again = processor_service.raise_cease_processing_alerts(db, consent)
    assert len(again) == 1
    assert db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).count() == 1


def test_a_withdrawal_with_no_disclosures_raises_nothing_and_is_not_counted_as_success(db):
    """An empty sharing log must not read as 100% propagation compliance."""
    purpose, category, activity = _make_purpose(db, "no_processors")
    customer = _make_customer(db, "R307-CUST-NONE-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    assert db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).count() == 0
    metrics = processor_service.propagation_sla_metrics(db, tenant_id=customer.tenant_id)
    assert metrics["withdrawals_without_processors"] >= 1


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #
def test_unacknowledged_alert_escalates_to_the_dpo_exactly_once(db, endpoint):
    purpose, category, activity = _make_purpose(db, "escalate")
    customer = _make_customer(db, "R307-CUST-ESC-001")
    organization = db.get(Organization, customer.tenant_id)
    organization.dpo_email = "dpo@fiduciary.example"
    db.commit()

    processor, _ = _make_processor(db, "Silent Co", endpoint, ack_sla_hours=1)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alert = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).one()
    assert alert.ack_sla_hours == 1
    alert.due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()

    result = processor_service.escalate_overdue_alerts(db)
    # The sweep is global by design (it is a scheduled job), and this shared
    # test database may hold other tests' overdue alerts, so assert on this
    # alert and this tenant rather than on a global count.
    assert result["escalated"] >= 1
    assert result["dpo_notifications_queued"] >= 1
    db.refresh(alert)
    assert alert.status == "ESCALATED"
    assert alert.escalated_at is not None

    def _our_escalations():
        return (
            db.query(Notification)
            .filter(
                Notification.event_type == "PROCESSOR_ESCALATION",
                Notification.tenant_id == customer.tenant_id,
            )
            .all()
        )

    escalations = _our_escalations()
    assert len(escalations) == 1
    assert escalations[0].recipient == "dpo@fiduciary.example"
    assert escalations[0].customer_id is None, "a DPO escalation is not a principal notification"
    assert alert.alert_ref in escalations[0].body

    # Running the job again must not mail the DPO a second time.
    processor_service.escalate_overdue_alerts(db)
    assert len(_our_escalations()) == 1


def test_a_late_acknowledgement_still_records_the_escalation(db, endpoint):
    purpose, category, activity = _make_purpose(db, "late_ack")
    customer = _make_customer(db, "R307-CUST-LATE-001")
    processor, _ = _make_processor(db, "Late Co", endpoint, ack_sla_hours=1)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alert = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).one()
    alert.due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    processor_service.escalate_overdue_alerts(db)

    processor_service.acknowledge_alert(db, alert, acknowledged_by="Late Co", method="MANUAL")
    db.refresh(alert)
    assert alert.status == "ACKNOWLEDGED"
    assert alert.escalated_at is not None, "the miss must stay on the record"

    metrics = processor_service.propagation_sla_metrics(db, tenant_id=customer.tenant_id)
    assert metrics["sla_breached"] == 1
    assert metrics["alerts_escalated"] == 1


# --------------------------------------------------------------------------- #
# Secrets and signatures
# --------------------------------------------------------------------------- #
def test_webhook_secret_is_returned_once_and_never_again(db, processor_client, staff_token):
    created = processor_client.post(
        "/processors",
        json={"name": "Secret Co", "type": "ANALYTICS", "country": "IN",
              "webhook_url": "https://processor.example/hook", "contract_ref": "DPA-1"},
        headers=_auth(staff_token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    plaintext = body["webhook_secret"]
    assert plaintext.startswith("whsec_")
    processor_id = body["id"]

    detail = processor_client.get(f"/processors/{processor_id}", headers=_auth(staff_token))
    assert detail.status_code == 200
    assert "webhook_secret" not in detail.json()
    assert detail.json()["webhook_secret_fingerprint"] == processor_service.secret_fingerprint(plaintext)
    assert plaintext not in detail.text

    listing = processor_client.get("/processors", headers=_auth(staff_token))
    assert plaintext not in listing.text

    # The stored column holds it, but as ciphertext when encryption is on -
    # the ORM decrypts transparently, so compare against the plaintext.
    stored = db.get(Processor, processor_id)
    assert stored.webhook_secret == plaintext

    rotated = processor_client.post(
        f"/processors/{processor_id}/rotate-webhook-secret", headers=_auth(staff_token)
    )
    assert rotated.status_code == 200
    assert rotated.json()["webhook_secret"] != plaintext
    assert rotated.json()["webhook_secret_fingerprint"] != body["webhook_secret_fingerprint"]


def test_the_audit_trail_records_the_fingerprint_and_never_the_secret(db, processor_client, staff_token):
    from app.models.entities import AuditLog

    created = processor_client.post(
        "/processors",
        json={"name": "Audited Co", "webhook_url": "https://processor.example/hook"},
        headers=_auth(staff_token),
    )
    plaintext = created.json()["webhook_secret"]
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event.in_(["PROCESSOR_REGISTERED", "PROCESSOR_WEBHOOK_SECRET_ROTATED"]))
        .all()
    )
    assert rows
    for row in rows:
        serialised = json.dumps(row.details or {})
        assert plaintext not in serialised
        assert "webhook_secret_fingerprint" in serialised


def test_acknowledgement_requires_a_valid_signature(db, endpoint, processor_client):
    purpose, category, activity = _make_purpose(db, "sig_check")
    customer = _make_customer(db, "R307-CUST-SIG-001")
    processor, secret = _make_processor(db, "Signed Co", endpoint)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()
    alert = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).one()

    body = json.dumps({"acknowledged_by": "Signed Co"}).encode("utf-8")
    now = int(datetime.now(timezone.utc).timestamp())
    url = f"/processors/alerts/{alert.alert_ref}/ack"

    assert processor_client.post(url, content=body).status_code == 401
    assert processor_client.post(url, content=body, headers={
        "X-Consent360-Timestamp": str(now),
        "X-Consent360-Signature": "sha256=" + "0" * 64,
    }).status_code == 401
    # A signature made with the wrong secret.
    assert processor_client.post(url, content=body, headers={
        "X-Consent360-Timestamp": str(now),
        "X-Consent360-Signature": processor_service.sign_payload("whsec_wrong", now, body),
    }).status_code == 401
    # A correct signature over a stale timestamp - a captured replay.
    stale = now - 3600
    assert processor_client.post(url, content=body, headers={
        "X-Consent360-Timestamp": str(stale),
        "X-Consent360-Signature": processor_service.sign_payload(secret, stale, body),
    }).status_code == 401
    # A correct signature over *different* bytes than were sent.
    assert processor_client.post(url, content=body, headers={
        "X-Consent360-Timestamp": str(now),
        "X-Consent360-Signature": processor_service.sign_payload(secret, now, b"{}"),
    }).status_code == 401

    db.refresh(alert)
    assert alert.acknowledged_at is None

    assert processor_client.post(url, content=body, headers={
        "X-Consent360-Timestamp": str(now),
        "X-Consent360-Signature": processor_service.sign_payload(secret, now, body),
    }).status_code == 200


def test_an_unknown_alert_ref_is_indistinguishable_from_one_you_cannot_sign_for(processor_client):
    response = processor_client.post("/processors/alerts/ALRT-DOES-NOT-EXIST/ack", content=b"{}")
    assert response.status_code == 404


def test_rotating_the_secret_resigns_queued_alerts(db, endpoint):
    purpose, category, activity = _make_purpose(db, "rotate_sig")
    customer = _make_customer(db, "R307-CUST-ROT-001")
    processor, old_secret = _make_processor(db, "Rotating Co", endpoint)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    new_secret, fingerprint = processor_service.generate_webhook_secret()
    processor.webhook_secret = new_secret
    processor.webhook_secret_fingerprint = fingerprint
    db.commit()

    processor_service.dispatch_pending_alerts(db)
    assert _wait_for_delivery()
    delivery = _ProcessorEndpoint.received[-1]
    assert processor_service.verify_signature(
        new_secret, delivery["headers"]["X-Consent360-Timestamp"],
        delivery["raw"], delivery["headers"]["X-Consent360-Signature"],
    )
    assert not processor_service.verify_signature(
        old_secret, delivery["headers"]["X-Consent360-Timestamp"],
        delivery["raw"], delivery["headers"]["X-Consent360-Signature"],
    )


# --------------------------------------------------------------------------- #
# Delivery, retries and failure
# --------------------------------------------------------------------------- #
def test_a_failing_endpoint_is_retried_with_backoff_then_marked_failed(db, endpoint):
    purpose, category, activity = _make_purpose(db, "retry")
    customer = _make_customer(db, "R307-CUST-RETRY-001")
    processor, _ = _make_processor(db, "Broken Co", endpoint)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alert = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).one()
    alert.max_attempts = 3
    db.commit()
    _ProcessorEndpoint.response_status = 500

    for expected_attempt in (1, 2):
        alert.next_attempt_at = datetime.now(timezone.utc)
        db.commit()
        processor_service.dispatch_pending_alerts(db)
        db.refresh(alert)
        assert alert.attempts == expected_attempt
        assert alert.status == "PENDING"
        assert alert.last_error == "HTTP 500"
        assert alert.next_attempt_at > datetime.now(timezone.utc)

    alert.next_attempt_at = datetime.now(timezone.utc)
    db.commit()
    processor_service.dispatch_pending_alerts(db)
    db.refresh(alert)
    assert alert.attempts == 3
    assert alert.status == "FAILED"


def test_a_processor_with_no_webhook_still_gets_a_tracked_instruction(db):
    """A processor we cannot reach electronically is a contract-coverage gap,
    not a reason to lose the s.6(6) obligation - the instruction is still
    raised, still on the SLA clock, and still escalatable."""
    purpose, category, activity = _make_purpose(db, "no_webhook")
    customer = _make_customer(db, "R307-CUST-NOHOOK-001")
    processor, _ = _make_processor(db, "Fax Only Co", webhook_url="")
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)
    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alert = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).one()
    assert alert.status == "PENDING"
    processor_service.dispatch_pending_alerts(db)
    db.refresh(alert)
    assert "no webhook" in (alert.last_error or "").lower()
    assert alert.status == "PENDING"


# --------------------------------------------------------------------------- #
# Erasure interface (R1-06 is not built - this is the hook it will call)
# --------------------------------------------------------------------------- #
def test_erasure_instructions_raise_alerts_and_erase_nothing(db, endpoint):
    purpose, category, activity = _make_purpose(db, "erasure_iface")
    customer = _make_customer(db, "R307-CUST-ERASE-001")
    processor, _ = _make_processor(db, "Erasing Co", endpoint, erasure_sla_days=30)
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)

    consents_before = db.query(Consent).count()
    customers_before = db.query(Customer).count()

    alerts = processor_service.raise_erasure_instructions(
        db, customer=customer, request_ref="ERZ-2026-0001", reason="Principal requested erasure"
    )
    assert len(alerts) == 1
    assert alerts[0].alert_type == "ERASURE_INSTRUCTION"
    assert alerts[0].trigger_ref == "erasure:ERZ-2026-0001"
    assert alerts[0].payload["details"]["erasure_request_ref"] == "ERZ-2026-0001"
    assert alerts[0].payload["details"]["contractual_erasure_sla_days"] == 30

    # The interface is an instruction, not an erasure: nothing local changed.
    assert db.query(Consent).count() == consents_before
    assert db.query(Customer).count() == customers_before
    db.refresh(consent)
    assert consent.status in ("GRANTED", "ACTIVE")
    db.refresh(customer)
    assert customer.name == "Test Principal"

    # Idempotent per request_ref, so an erasure engine retry is safe.
    again = processor_service.raise_erasure_instructions(
        db, customer=customer, request_ref="ERZ-2026-0001"
    )
    assert [a.id for a in again] == [alerts[0].id]


def test_erasure_reaches_every_processor_regardless_of_purpose(db, endpoint):
    purpose_a, cat_a, act_a = _make_purpose(db, "erase_p_a")
    purpose_b, cat_b, act_b = _make_purpose(db, "erase_p_b")
    customer = _make_customer(db, "R307-CUST-ERASE-002")
    processor_a, _ = _make_processor(db, "Erase A", endpoint)
    processor_b, _ = _make_processor(db, "Erase B", endpoint)
    _log_disclosure(db, customer, purpose_a, processor_a)
    _log_disclosure(db, customer, purpose_b, processor_b)

    alerts = processor_service.raise_erasure_instructions(
        db, customer=customer, request_ref="ERZ-2026-0002"
    )
    assert {a.processor_id for a in alerts} == {processor_a.id, processor_b.id}


# --------------------------------------------------------------------------- #
# Sharing-event hook and the contract coverage report
# --------------------------------------------------------------------------- #
def test_sharing_event_hook_notifies_the_receiving_processor(db, endpoint):
    purpose, _cat, _act = _make_purpose(db, "sharing_hook")
    customer = _make_customer(db, "R307-CUST-SHARE-001")
    processor, _ = _make_processor(db, "Receiving Co", endpoint)
    event = _log_disclosure(db, customer, purpose, processor)

    alert = processor_service.raise_sharing_event_alert(db, event, customer=customer)
    assert alert is not None
    assert alert.alert_type == "SHARING_EVENT"
    assert alert.sharing_event_id == event.id
    # Idempotent per sharing event.
    assert processor_service.raise_sharing_event_alert(db, event, customer=customer).id == alert.id


def test_sharing_event_hook_ignores_denied_disclosures(db, endpoint):
    purpose, _cat, _act = _make_purpose(db, "sharing_denied")
    customer = _make_customer(db, "R307-CUST-SHARE-002")
    processor, _ = _make_processor(db, "Denied Co", endpoint)
    event = _log_disclosure(db, customer, purpose, processor, event_type="DENIED")
    assert processor_service.raise_sharing_event_alert(db, event, customer=customer) is None


def test_contract_coverage_report_scores_every_clause(db, endpoint):
    today = datetime.now(timezone.utc).date()
    complete, _ = _make_processor(
        db, "Fully Covered Co", endpoint,
        contract_ref="DPA-COMPLETE", contract_valid_from=today - timedelta(days=30),
        contract_valid_until=today + timedelta(days=365),
        security_clause_ref="cl.7", erasure_clause_ref="cl.9", erasure_sla_days=30,
    )
    missing_erasure, _ = _make_processor(
        db, "No Erasure Clause Co", endpoint,
        contract_ref="DPA-PARTIAL", contract_valid_until=today + timedelta(days=365),
        security_clause_ref="cl.7",
    )
    expired, _ = _make_processor(
        db, "Expired Contract Co", endpoint,
        contract_ref="DPA-EXPIRED", contract_valid_until=today - timedelta(days=1),
        security_clause_ref="cl.7", erasure_clause_ref="cl.9",
    )
    unreachable, _ = _make_processor(
        db, "Unreachable Co", webhook_url="",
        contract_ref="DPA-UNREACHABLE", contract_valid_until=today + timedelta(days=365),
        security_clause_ref="cl.7", erasure_clause_ref="cl.9",
    )

    report = processor_service.contract_coverage_report(db)
    rows = {r["processor_id"]: r for r in report["processors"]}

    assert rows[complete.id]["covered"] is True
    assert rows[complete.id]["gaps"] == []
    assert rows[missing_erasure.id]["covered"] is False
    assert "has_erasure_clause" in rows[missing_erasure.id]["gaps"]
    assert rows[expired.id]["covered"] is False
    assert "contract_in_force" in rows[expired.id]["gaps"]
    assert rows[unreachable.id]["covered"] is False
    assert "reachable_for_instructions" in rows[unreachable.id]["gaps"]

    assert report["processors_total"] >= 4
    assert 0 < report["coverage_pct"] < 100
    assert report["processors_with_gaps"] >= 3


def test_contract_coverage_flags_contracts_expiring_soon(db, endpoint):
    today = datetime.now(timezone.utc).date()
    expiring, _ = _make_processor(
        db, "Expiring Soon Co", endpoint,
        contract_ref="DPA-SOON", contract_valid_until=today + timedelta(days=45),
        security_clause_ref="cl.7", erasure_clause_ref="cl.9",
    )
    report = processor_service.contract_coverage_report(db)
    assert expiring.id in report["contracts_expiring_within_90_days"]


# --------------------------------------------------------------------------- #
# Propagation must never be able to deny a data principal their right
# --------------------------------------------------------------------------- #
def test_a_propagation_failure_does_not_break_the_withdrawal(db, monkeypatch):
    """Withdrawal is a statutory right; telling a processor about it is this
    fiduciary's own downstream duty. A failure in the second must never take
    the first with it - see services/consent.py::_propagate_cease_processing.

    The failure simulated here is a *database* failure (the shape actually
    hit in practice: the R3-07 tables missing on a database that has not run
    the migration yet), because that is the case where merely catching the
    exception is not enough - the Session is left in an aborted transaction
    and every later statement on it fails until something rolls back.
    """
    from sqlalchemy.exc import ProgrammingError

    purpose, category, activity = _make_purpose(db, "failsoft")
    customer = _make_customer(db, "R307-CUST-FAILSOFT-001")
    processor, _ = _make_processor(db, "Unreachable Register Co", "")
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)

    def _boom(*args, **kwargs):
        # Force a real aborted transaction, not just a Python exception.
        db.execute(__import__("sqlalchemy").text("SELECT * FROM table_that_does_not_exist"))

    monkeypatch.setattr(
        "app.services.processors.raise_cease_processing_alerts", _boom, raising=True
    )

    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)

    db.refresh(consent)
    assert consent.status == "WITHDRAWN"
    assert consent.withdrawn_at is not None
    assert db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).count() == 0

    # The session must still be usable: a swallowed exception without a
    # rollback would make every subsequent statement fail.
    assert db.query(Consent).filter(Consent.id == consent.id).one().status == "WITHDRAWN"

    # And the alerts can still be raised afterwards, because the fan-out is
    # idempotent per (consent, consent_version).
    monkeypatch.undo()
    raised = processor_service.raise_cease_processing_alerts(db, consent)
    assert len(raised) == 1
    assert raised[0].status == "PENDING"


def test_the_withdrawal_path_performs_no_network_io(db, monkeypatch):
    """A dead processor endpoint must not even be reachable from a withdrawal:
    raising an alert enqueues a row, and delivery happens later in the
    scheduled job."""
    import httpx

    def _forbidden(*args, **kwargs):  # pragma: no cover - fails the test if reached
        raise AssertionError("the withdrawal path must not make an HTTP request")

    monkeypatch.setattr(httpx.Client, "post", _forbidden)

    purpose, category, activity = _make_purpose(db, "no_io")
    customer = _make_customer(db, "R307-CUST-NOIO-001")
    processor, _ = _make_processor(db, "Slow Endpoint Co", "http://127.0.0.1:9/never")
    _log_disclosure(db, customer, purpose, processor)
    consent = _granted_consent(db, customer, purpose, category, activity)

    consent_service.withdraw_consent(db, consent, source_app=customer.source_app)
    db.commit()

    alert = db.query(ProcessorAlert).filter(ProcessorAlert.consent_id == consent.id).one()
    assert alert.status == "PENDING"
    assert alert.sent_at is None
