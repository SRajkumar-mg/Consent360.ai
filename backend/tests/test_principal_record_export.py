"""R1-11 (D-06, CM-04, D-09, S-03, T-04): principal record access/export and
decision-log reporting."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import decisions as decisions_routes
from app.core.database import get_db


@pytest.fixture()
def extra_client(db):
    app = FastAPI()
    app.include_router(decisions_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _make_purpose(db, code):
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
        consent_text="I consent.", is_current=True, created_by="test",
        data_items=[{"data_category_id": category.id, "necessity": True, "description": ""}],
    ))
    db.commit()
    return purpose, category, activity


def _verified_context(db, email, source_app="RECORD_TEST"):
    from app.core.security import create_context_token
    from app.models.entities import ConsentContext, Customer

    customer = Customer(external_id=f"CUST-{email}", name="Record Customer", email=email, source_app=source_app)
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


def test_portal_history_requires_verification(db, client, monkeypatch):
    from app.api.routes import portal

    monkeypatch.setattr(portal.settings, "PORTAL_REQUIRE_VERIFICATION", True)
    purpose, category, activity = _make_purpose(db, "hist_unverified")
    from app.core.security import create_context_token
    from app.models.entities import ConsentContext, Customer

    customer = Customer(external_id="CUST-hist-unverified", name="Unverified", source_app="RECORD_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, "RECORD_TEST")
    db.add(ConsentContext(customer_id=customer.id, token=token, source_app="RECORD_TEST",
                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)))
    db.commit()

    resp = client.get("/portal/history", headers={"X-Context-Token": token})
    assert resp.status_code == 403


def test_portal_history_and_export_json_csv_pdf(db, client):
    from app.services import consent as consent_service

    purpose, category, activity = _make_purpose(db, "hist_full")
    customer, token = _verified_context(db, "hist-full@example.com")

    consent, _created = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="RECORD_TEST",
    )
    consent_service.grant_consent(db, consent, actor_username="test", source_app="RECORD_TEST")

    history_resp = client.get("/portal/history", headers={"X-Context-Token": token})
    assert history_resp.status_code == 200, history_resp.text
    body = history_resp.json()
    assert body["customer"]["external_id"] == customer.external_id
    assert len(body["consents"]) == 1
    assert body["consents"][0]["status"] in ("GRANTED", "ACTIVE")
    assert len(body["history"]) >= 1
    assert len(body["evidence"]) >= 1
    assert len(body["receipts"]) >= 1

    json_resp = client.get("/portal/export", headers={"X-Context-Token": token}, params={"format": "json"})
    assert json_resp.status_code == 200
    assert json_resp.headers["content-type"].startswith("application/json")
    assert purpose.code in json_resp.text

    csv_resp = client.get("/portal/export", headers={"X-Context-Token": token}, params={"format": "csv"})
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "CONSENTS" in csv_resp.text
    assert "RECEIPTS" in csv_resp.text

    pdf_resp = client.get("/portal/export", headers={"X-Context-Token": token}, params={"format": "pdf"})
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content.startswith(b"%PDF")


def test_portal_export_rejects_unknown_format(db, client):
    customer, token = _verified_context(db, "hist-badformat@example.com")
    resp = client.get("/portal/export", headers={"X-Context-Token": token}, params={"format": "xml"})
    assert resp.status_code == 422


def test_decision_report_endpoint_counts_outcomes(db, extra_client, staff_token):
    from app.services.decision_engine import evaluate_decision

    purpose, category, activity = _make_purpose(db, "dec_report")
    from app.models.entities import Customer

    customer = Customer(external_id="CUST-dec-report", name="Decision Customer", source_app="DEC_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)

    decision = evaluate_decision(db, customer, purpose, category, activity, source_app="DEC_TEST")
    assert decision.decision == "REQUIRE_CONSENT"

    resp = extra_client.get(
        "/decisions/report", headers={"Authorization": f"Bearer {staff_token}"}, params={"group_by": "total"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["group_by"] == "total"
    assert body["totals"]["REQUIRE_CONSENT"] >= 1
    assert body["totals"]["total"] >= 1


def test_decision_report_grouped_by_day(db, extra_client, staff_token):
    from app.services.decision_engine import evaluate_decision

    purpose, category, activity = _make_purpose(db, "dec_report_day")
    from app.models.entities import Customer

    customer = Customer(external_id="CUST-dec-report-day", name="Decision Customer 2", source_app="DEC_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    evaluate_decision(db, customer, purpose, category, activity, source_app="DEC_TEST")

    resp = extra_client.get(
        "/decisions/report", headers={"Authorization": f"Bearer {staff_token}"}, params={"group_by": "day"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_by"] == "day"
    assert len(body["buckets"]) >= 1
    assert sum(b["total"] for b in body["buckets"]) == body["totals"]["total"]
