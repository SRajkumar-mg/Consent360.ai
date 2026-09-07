"""R1-08: consent receipts, sharing events and objections (B-09, D-05, M-02, C-06)."""
from datetime import datetime, timedelta, timezone

from app.core.security import create_context_token
from app.models.entities import (
    Consent,
    ConsentContext,
    ConsentReceipt,
    Customer,
    DataCategory,
    DataSharingEvent,
    Objection,
    Processor,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.services import consent as consent_service
from app.services.receipts import verify_receipt


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_purpose(db, code, legal_basis="CONSENT", requires_consent=True):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, legal_basis=legal_basis, requires_consent=requires_consent)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose, category, activity


def _make_customer(db, external_id, source_app):
    customer = Customer(external_id=external_id, name="Test Customer", source_app=source_app)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _make_verified_context(db, customer, source_app):
    token = create_context_token(customer.id, source_app)
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app=source_app,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()
    return token


# --------------------------------------------------------------------------- #
# Receipts
# --------------------------------------------------------------------------- #

def test_grant_issues_a_receipt_retrievable_by_staff_and_by_the_principal(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "receipt_grant")
    customer = _make_customer(db, "CUST-RECEIPT-001", "RECEIPT_TEST")
    token = _make_verified_context(db, customer, "RECEIPT_TEST")

    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="RECEIPT_TEST")
    consent_service.grant_consent(db, consent, source_app="RECEIPT_TEST")
    db.commit()

    receipts = db.query(ConsentReceipt).filter(ConsentReceipt.consent_id == consent.id).all()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.action == "GRANTED"
    assert receipt.payload["purpose"]["code"] == purpose.code
    assert verify_receipt(receipt) is True

    staff_resp = client.get(f"/receipts/{receipt.receipt_ref}", headers=_auth(staff_token))
    assert staff_resp.status_code == 200
    assert staff_resp.json()["valid"] is True
    assert staff_resp.json()["payload"]["consent_record_id"] == consent.id

    mine = client.get("/receipts/me", headers={"X-Context-Token": token})
    assert mine.status_code == 200
    refs = [r["receipt_ref"] for r in mine.json()]
    assert receipt.receipt_ref in refs

    mine_detail = client.get(f"/receipts/me/{receipt.receipt_ref}", headers={"X-Context-Token": token})
    assert mine_detail.status_code == 200


def test_renew_issues_a_second_receipt(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "receipt_renew")
    customer = _make_customer(db, "CUST-RECEIPT-002", "RECEIPT_TEST")
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="RECEIPT_TEST")
    consent_service.grant_consent(db, consent, source_app="RECEIPT_TEST")
    db.commit()
    consent_service.renew_consent(db, consent, source_app="RECEIPT_TEST")
    db.commit()

    receipts = db.query(ConsentReceipt).filter(ConsentReceipt.consent_id == consent.id).order_by(ConsentReceipt.id).all()
    assert len(receipts) == 2
    assert receipts[0].action == "GRANTED"
    assert receipts[1].action == "RENEWED"
    assert receipts[1].consent_version == consent.consent_version


def test_a_customer_cannot_see_another_customers_receipt(client, db):
    purpose, category, activity = _make_purpose(db, "receipt_isolation")
    victim = _make_customer(db, "CUST-RECEIPT-VICTIM", "RECEIPT_TEST")
    attacker = _make_customer(db, "CUST-RECEIPT-ATTACKER", "RECEIPT_TEST")
    attacker_token = _make_verified_context(db, attacker, "RECEIPT_TEST")

    consent, _ = consent_service.get_or_create_consent(db, victim, purpose, category, activity, source_app="RECEIPT_TEST")
    consent_service.grant_consent(db, consent, source_app="RECEIPT_TEST")
    db.commit()
    receipt = db.query(ConsentReceipt).filter(ConsentReceipt.consent_id == consent.id).first()

    resp = client.get(f"/receipts/me/{receipt.receipt_ref}", headers={"X-Context-Token": attacker_token})
    assert resp.status_code == 404


def test_tampering_with_a_stored_receipt_payload_is_detectable(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "receipt_tamper")
    customer = _make_customer(db, "CUST-RECEIPT-TAMPER", "RECEIPT_TEST")
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="RECEIPT_TEST")
    consent_service.grant_consent(db, consent, source_app="RECEIPT_TEST")
    db.commit()

    receipt = db.query(ConsentReceipt).filter(ConsentReceipt.consent_id == consent.id).first()
    assert verify_receipt(receipt) is True
    receipt.payload = {**receipt.payload, "status": "WITHDRAWN"}
    db.commit()
    db.refresh(receipt)
    assert verify_receipt(receipt) is False

    resp = client.get(f"/receipts/{receipt.receipt_ref}", headers=_auth(staff_token))
    assert resp.json()["valid"] is False


# --------------------------------------------------------------------------- #
# Data sharing events
# --------------------------------------------------------------------------- #

def _make_processor(db, name="Sharing Test Processor"):
    processor = Processor(name=name, type="PROCESSOR", country="IN", is_active=True)
    db.add(processor)
    db.commit()
    db.refresh(processor)
    return processor


def test_create_sharing_event_records_recipient_categories_purpose_and_type(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "sharing_basic")
    customer = _make_customer(db, "CUST-SHARE-001", "SHARE_TEST")
    processor = _make_processor(db)

    resp = client.post("/sharing-events", json={
        "customer_external_id": customer.external_id, "processor_id": processor.id,
        "purpose_code": purpose.code, "data_category_codes": [category.code],
        "event_type": "SENT", "reason": "Fulfilment partner needs this to ship the order",
    }, headers=_auth(staff_token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["processor_name"] == processor.name
    assert body["purpose_code"] == purpose.code
    assert body["data_category_codes"] == [category.code]
    assert body["event_type"] == "SENT"
    assert body["legal_basis"] == "CONSENT"
    assert body["signature_valid"] is True

    event = db.query(DataSharingEvent).filter(DataSharingEvent.customer_id == customer.id).first()
    assert event is not None
    assert event.data_category_ids == [category.id]


def test_create_sharing_event_rejects_unknown_processor(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "sharing_badproc")
    customer = _make_customer(db, "CUST-SHARE-002", "SHARE_TEST")
    resp = client.post("/sharing-events", json={
        "customer_external_id": customer.external_id, "processor_id": 999999,
        "purpose_code": purpose.code, "data_category_codes": [category.code], "event_type": "SENT",
    }, headers=_auth(staff_token))
    assert resp.status_code == 422


def test_create_sharing_event_rejects_consent_belonging_to_a_different_customer(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "sharing_wrongconsent")
    customer = _make_customer(db, "CUST-SHARE-003", "SHARE_TEST")
    other_customer = _make_customer(db, "CUST-SHARE-004", "SHARE_TEST")
    processor = _make_processor(db, "Other Processor")
    other_consent, _ = consent_service.get_or_create_consent(db, other_customer, purpose, category, activity, source_app="SHARE_TEST")
    db.commit()

    resp = client.post("/sharing-events", json={
        "customer_external_id": customer.external_id, "processor_id": processor.id,
        "purpose_code": purpose.code, "data_category_codes": [category.code],
        "event_type": "SENT", "consent_id": other_consent.id,
    }, headers=_auth(staff_token))
    assert resp.status_code == 400


def test_list_sharing_events_filters_by_customer(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "sharing_list")
    customer = _make_customer(db, "CUST-SHARE-005", "SHARE_TEST")
    other = _make_customer(db, "CUST-SHARE-006", "SHARE_TEST")
    processor = _make_processor(db, "List Processor")
    for cust in (customer, other):
        client.post("/sharing-events", json={
            "customer_external_id": cust.external_id, "processor_id": processor.id,
            "purpose_code": purpose.code, "data_category_codes": [category.code], "event_type": "SENT",
        }, headers=_auth(staff_token))

    resp = client.get("/sharing-events", params={"customer_external_id": customer.external_id}, headers=_auth(staff_token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["data_category_codes"] == [category.code]


# --------------------------------------------------------------------------- #
# Objections
# --------------------------------------------------------------------------- #

def test_objection_requires_a_s7a_purpose(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "objection_wrong_basis", legal_basis="CONSENT")
    customer = _make_customer(db, "CUST-OBJ-001", "OBJ_TEST")
    resp = client.post("/objections", json={
        "customer_external_id": customer.external_id, "purpose_code": purpose.code, "reason": "no",
    }, headers=_auth(staff_token))
    assert resp.status_code == 422


def test_objection_create_and_duplicate_conflict(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "objection_dup", legal_basis="S7_A", requires_consent=False)
    customer = _make_customer(db, "CUST-OBJ-002", "OBJ_TEST")
    first = client.post("/objections", json={
        "customer_external_id": customer.external_id, "purpose_code": purpose.code, "reason": "I object",
    }, headers=_auth(staff_token))
    assert first.status_code == 201
    assert first.json()["status"] == "ACTIVE"

    dup = client.post("/objections", json={
        "customer_external_id": customer.external_id, "purpose_code": purpose.code, "reason": "again",
    }, headers=_auth(staff_token))
    assert dup.status_code == 409


def test_objection_self_service_via_context_token(client, db):
    purpose, category, activity = _make_purpose(db, "objection_self", legal_basis="S7_A", requires_consent=False)
    customer = _make_customer(db, "CUST-OBJ-003", "OBJ_TEST")
    token = _make_verified_context(db, customer, "OBJ_TEST")

    resp = client.post("/objections/me", json={"purpose_code": purpose.code, "reason": "stop processing my data"},
                       headers={"X-Context-Token": token})
    assert resp.status_code == 201
    assert resp.json()["reason"] == "stop processing my data"

    mine = client.get("/objections/me", headers={"X-Context-Token": token})
    assert mine.status_code == 200
    assert len(mine.json()) == 1


def test_objection_resolve(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "objection_resolve", legal_basis="S7_A", requires_consent=False)
    customer = _make_customer(db, "CUST-OBJ-004", "OBJ_TEST")
    create = client.post("/objections", json={
        "customer_external_id": customer.external_id, "purpose_code": purpose.code, "reason": "stop",
    }, headers=_auth(staff_token))
    objection_id = create.json()["id"]

    resolve = client.post(f"/objections/{objection_id}/resolve", json={"resolution_note": "Processing stopped"},
                          headers=_auth(staff_token))
    assert resolve.status_code == 200
    assert resolve.json()["status"] == "RESOLVED"
    assert resolve.json()["resolution_note"] == "Processing stopped"

    again = client.post(f"/objections/{objection_id}/resolve", json={"resolution_note": "x"}, headers=_auth(staff_token))
    assert again.status_code == 409

    db_row = db.get(Objection, objection_id)
    assert db_row.resolved_by is not None
