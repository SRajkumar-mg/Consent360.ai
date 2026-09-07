from urllib.parse import parse_qs, urlparse

from app.api.routes.crm import _consent_portal_url
from app.core.config import get_settings


def test_consent_portal_url_uses_token_param_and_correct_default_port():
    settings = get_settings()
    url = _consent_portal_url("sample-context-token")
    parsed = urlparse(url)
    assert parsed.path == "/portal/consent"
    query = parse_qs(parsed.query)
    assert query["token"] == ["sample-context-token"]
    assert "ctx" not in query
    assert settings.CONSENT_PORTAL_URL.endswith(":8005")


def test_crm_manage_consent_endpoint_returns_portal_url(db, client):
    from app.models.entities import CrmCustomer

    customer = CrmCustomer(name="Handoff Customer", email="handoff@example.com")
    db.add(customer)
    db.commit()
    db.refresh(customer)

    resp = client.post(f"/crm/customers/{customer.id}/consent-context")
    assert resp.status_code == 200
    body = resp.json()
    assert "token=" in body["consent_portal_url"]
    assert body["consent_portal_url"].startswith(get_settings().CONSENT_PORTAL_URL)


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
    ))
    db.commit()
    return purpose, category, activity


def _verified_context(db, email):
    from datetime import datetime, timedelta, timezone

    from app.core.security import create_context_token
    from app.models.entities import ConsentContext, Customer

    customer = Customer(external_id=f"CUST-{email}", name="Withdraw Customer", source_app="EVID_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, "EVID_TEST")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="EVID_TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()
    return customer, token


def test_portal_withdraw_records_evidence_with_interaction_step(db, client):
    """Withdrawal used to skip evidence creation entirely, so a withdrawal's
    interaction_step (and every other ClientContext field) was silently
    dropped - the one place click-depth instrumentation matters most for
    grant-vs-withdraw parity. It must now be recorded exactly like a grant."""
    from app.services import consent as consent_service

    purpose, _category, _activity = _make_purpose(db, "withdraw_evid")
    customer, token = _verified_context(db, "withdraw-evid@example.com")

    grant_resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token},
        json={
            "purpose_code": purpose.code,
            "context": {"ui_control_id": f"grant-{purpose.code}", "affirmative_action": "CLICK", "interaction_step": 1},
        },
    )
    assert grant_resp.status_code == 200

    withdraw_resp = client.post(
        "/portal/withdraw",
        headers={"X-Context-Token": token},
        json={
            "purpose_code": purpose.code,
            "context": {"ui_control_id": f"withdraw-{purpose.code}", "affirmative_action": "CLICK", "interaction_step": 1},
        },
    )
    assert withdraw_resp.status_code == 200

    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    db.refresh(consent)
    assert len(consent.evidence) == 2, "withdraw must create its own evidence row, not reuse the grant's"
    withdraw_evidence = consent.evidence[-1]
    assert withdraw_evidence.ui_control_id == f"withdraw-{purpose.code}"
    assert withdraw_evidence.details.get("interaction_step") == 1

    # Both the grant and the withdraw are recorded as one click each, so the
    # two counts are directly comparable per-consent via ConsentEvidence.details->>'interaction_step'.
    grant_evidence = consent.evidence[0]
    assert grant_evidence.details.get("interaction_step") == withdraw_evidence.details.get("interaction_step") == 1
