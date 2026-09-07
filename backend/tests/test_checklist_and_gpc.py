"""Carried items 4 and 5: a server-persisted plain-language/dark-pattern
review checklist on PurposeVersion/PolicyVersion, and a server-observed
Global Privacy Control (Sec-GPC) signal on ConsentEvidence."""
from datetime import datetime, timedelta, timezone


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


def _verified_context(db, email, source_app="GPC_TEST"):
    from app.core.security import create_context_token
    from app.models.entities import ConsentContext, Customer

    customer = Customer(external_id=f"CUST-{email}", name="GPC Customer", source_app=source_app)
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


_CHECKLIST = {
    "reviewer": "Priya Nair",
    "completed_at": "2026-09-04T00:00:00Z",
    "items": ["plain-language", "no-preselection"],
}


def test_create_purpose_persists_checklist(client, staff_token):
    resp = client.post(
        "/purposes", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Checklist Purpose", "code": "checklist_purpose_1", "checklist": _CHECKLIST},
    )
    assert resp.status_code == 201, resp.text
    versions = resp.json()["versions"]
    assert versions[0]["checklist"]["reviewer"] == "Priya Nair"
    assert versions[0]["checklist"]["items"] == ["plain-language", "no-preselection"]


def test_update_purpose_resets_checklist_when_content_changes_without_new_one(client, staff_token):
    create_resp = client.post(
        "/purposes", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Checklist Purpose 2", "code": "checklist_purpose_2", "checklist": _CHECKLIST},
    )
    purpose_id = create_resp.json()["id"]

    update_resp = client.put(
        f"/purposes/{purpose_id}", headers={"Authorization": f"Bearer {staff_token}"},
        json={"description": "changed description"},
    )
    assert update_resp.status_code == 200
    versions = update_resp.json()["versions"]
    current = next(v for v in versions if v["is_current"])
    assert current["version_number"] == 2
    assert current["checklist"] is None


def test_create_purpose_version_carries_checklist_forward(client, staff_token):
    create_resp = client.post(
        "/purposes", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Checklist Purpose 3", "code": "checklist_purpose_3", "checklist": _CHECKLIST},
    )
    purpose_id = create_resp.json()["id"]

    version_resp = client.post(
        f"/purposes/{purpose_id}/versions", headers={"Authorization": f"Bearer {staff_token}"},
        json={"reason": "manual reissue"},
    )
    assert version_resp.status_code == 200
    assert version_resp.json()["checklist"]["reviewer"] == "Priya Nair"


def test_policy_checklist_persists_and_resets_on_rule_change(client, staff_token):
    create_resp = client.post(
        "/policies", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Checklist Policy", "code": "checklist_policy_1", "checklist": _CHECKLIST},
    )
    assert create_resp.status_code == 201, create_resp.text
    policy_id = create_resp.json()["id"]
    versions = create_resp.json()["versions"]
    assert versions[0]["checklist"]["reviewer"] == "Priya Nair"

    update_resp = client.put(
        f"/policies/{policy_id}", headers={"Authorization": f"Bearer {staff_token}"},
        json={"default_decision": "ALLOW"},
    )
    assert update_resp.status_code == 200
    current = next(v for v in update_resp.json()["versions"] if v["is_current"])
    assert current["checklist"] is None


def test_portal_grant_records_server_observed_gpc_signal(db, client):
    from app.services import consent as consent_service

    purpose, category, activity = _make_purpose(db, "gpc_grant")
    customer, token = _verified_context(db, "gpc-grant@example.com")

    resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token, "Sec-GPC": "1"},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200, resp.text

    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    db.refresh(consent)
    assert consent.evidence[-1].gpc_signal is True


def test_portal_withdraw_records_gpc_false_for_non_1_header_value(db, client):
    from app.services import consent as consent_service

    purpose, category, activity = _make_purpose(db, "gpc_withdraw")
    customer, token = _verified_context(db, "gpc-withdraw@example.com")

    client.post(
        "/portal/grant", headers={"X-Context-Token": token},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK"}},
    )
    resp = client.post(
        "/portal/withdraw",
        headers={"X-Context-Token": token, "Sec-GPC": "0"},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200, resp.text

    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    db.refresh(consent)
    assert consent.evidence[-1].gpc_signal is False


def test_portal_grant_gpc_signal_is_null_when_header_absent(db, client):
    from app.services import consent as consent_service

    purpose, category, activity = _make_purpose(db, "gpc_absent")
    customer, token = _verified_context(db, "gpc-absent@example.com")

    resp = client.post(
        "/portal/grant", headers={"X-Context-Token": token},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200

    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    db.refresh(consent)
    assert consent.evidence[-1].gpc_signal is None


def test_client_claimed_gpc_signal_kept_separate_from_server_observed(db, client):
    """The client's own ClientContext.gpc_signal is a claim, kept only in
    details["claimed_gpc_signal"] - it must never overwrite the
    server-observed column even when no Sec-GPC header was actually sent."""
    from app.services import consent as consent_service

    purpose, category, activity = _make_purpose(db, "gpc_claimed")
    customer, token = _verified_context(db, "gpc-claimed@example.com")

    resp = client.post(
        "/portal/grant", headers={"X-Context-Token": token},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK", "gpc_signal": True}},
    )
    assert resp.status_code == 200

    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    db.refresh(consent)
    evidence = consent.evidence[-1]
    assert evidence.gpc_signal is None
    assert evidence.details.get("claimed_gpc_signal") is True
