"""R1-04: notice management (A-01, A-02, A-07, D-04, A-12)."""
from app.models.entities import Consent, ConsentEvidence, DataCategory, Notice, NoticeVersion, ProcessingActivity, Purpose, PurposeVersion


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# A-09: the plain-language & dark-pattern review record every test below that
# publishes a notice must now supply - see test_notices_checklist_gate.py for
# the tests that specifically exercise the gate itself (with and without
# this record).
_CHECKLIST = {
    "reviewer": "Priya Nair",
    "completed_at": "2026-09-04T00:00:00Z",
    "items": ["plain-language", "no-preselection"],
}


def _make_purpose(db, code, **spec_overrides):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, requires_consent=True,
                      services_enabled=spec_overrides.get("services_enabled", ""))
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        description="A test purpose", data_category_ids=[category.id],
        processing_activity_ids=[activity.id], consent_text="I consent.",
        data_items=[{"data_category_id": category.id, "necessity": True, "description": "needed"}],
        services_enabled=spec_overrides.get("services_enabled", ""),
        is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()
    return purpose, category, activity


def test_create_notice_requires_existing_purpose(client, staff_token):
    resp = client.post("/notices", json={"purpose_id": 999999, "title": "T", "body": "B"}, headers=_auth(staff_token))
    assert resp.status_code == 404


def test_create_notice_defaults_data_items_and_services_enabled_from_purpose(client, staff_token, db):
    purpose, category, activity = _make_purpose(db, "notice_defaults", services_enabled="Does X for you.")
    resp = client.post("/notices", json={"purpose_id": purpose.id, "title": "Notice title", "body": "Notice body"},
                       headers=_auth(staff_token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "DRAFT"
    assert body["current_version"] == 0
    v1 = body["versions"][0]
    assert v1["is_current"] is False
    assert v1["services_enabled"] == "Does X for you."
    assert v1["data_items"] == [{"data_category_id": category.id, "necessity": True, "description": "needed"}]


def test_create_notice_duplicate_purpose_conflicts(client, staff_token, db):
    purpose, _, _ = _make_purpose(db, "notice_dup")
    first = client.post("/notices", json={"purpose_id": purpose.id, "title": "T", "body": "B"}, headers=_auth(staff_token))
    assert first.status_code == 201
    second = client.post("/notices", json={"purpose_id": purpose.id, "title": "T2", "body": "B2"}, headers=_auth(staff_token))
    assert second.status_code == 409


def _create_and_publish(client, staff_token, purpose_id, title="Notice title", body="Notice body", **extra):
    # A-09: publishing refuses without a checklist (see
    # tests/test_notices_checklist_gate.py), so every other test in this file
    # that only cares about the rest of the publish behaviour gets one by
    # default here; pass checklist=None explicitly to opt out.
    payload = {"purpose_id": purpose_id, "title": title, "body": body, "checklist": _CHECKLIST}
    payload.update(extra)
    create = client.post("/notices", json=payload, headers=_auth(staff_token))
    assert create.status_code == 201, create.text
    notice_id = create.json()["id"]
    publish = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert publish.status_code == 200, publish.text
    return notice_id, publish.json()


def test_publish_sets_current_version_content_hash_links_and_contact(client, staff_token, db):
    from app.models.entities import Organization
    from app.services.tenancy import platform_tenant_id

    tenant_id = platform_tenant_id(db)
    tenant = db.get(Organization, tenant_id)
    tenant.dpo_name = "Test DPO"
    tenant.dpo_email = "dpo@example.com"
    tenant.withdraw_url = "https://example.com/withdraw"
    db.commit()

    purpose, _, _ = _make_purpose(db, "notice_publish")
    notice_id, body = _create_and_publish(client, staff_token, purpose.id)

    assert body["status"] == "ACTIVE"
    assert body["current_version"] == 1
    v1 = body["versions"][0]
    assert v1["is_current"] is True
    assert v1["content_hash"] is not None
    assert v1["published_by"] is not None
    assert v1["effective_from"] is not None
    assert v1["links"]["withdraw_url"] == "https://example.com/withdraw"
    assert v1["contact_snapshot"]["dpo_name"] == "Test DPO"
    assert v1["purposes"][0]["code"] == purpose.code


def test_publish_twice_without_a_new_draft_conflicts(client, staff_token, db):
    purpose, _, _ = _make_purpose(db, "notice_republish")
    notice_id, _ = _create_and_publish(client, staff_token, purpose.id)
    resp = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert resp.status_code == 409


def test_update_after_publish_creates_a_new_draft_without_touching_the_live_version(client, staff_token, db):
    purpose, _, _ = _make_purpose(db, "notice_redraft")
    notice_id, published = _create_and_publish(client, staff_token, purpose.id, title="Original title")

    update = client.put(f"/notices/{notice_id}", json={"title": "Updated title"}, headers=_auth(staff_token))
    assert update.status_code == 200
    versions = sorted(update.json()["versions"], key=lambda v: v["version_number"])
    assert len(versions) == 2
    assert versions[0]["is_current"] is True and versions[0]["title"] == "Original title"
    assert versions[1]["is_current"] is False and versions[1]["title"] == "Updated title"

    # The live render still serves the original, unpublished-change content.
    tenant_code = client.get(f"/notices/{notice_id}", headers=_auth(staff_token)).json()
    assert tenant_code["status"] == "ACTIVE"
    assert tenant_code["current_version"] == 1


def test_public_notice_endpoint_renders_published_content_with_language_fallback(client, staff_token, db):
    from app.models.entities import Organization

    org = Organization(name="Notice Render Tenant", code="NOTICE_RENDER_TENANT", is_active=True)
    db.add(org)
    db.commit()

    purpose, category, activity = _make_purpose(db, "notice_render")
    notice_id, _ = _create_and_publish(
        client, staff_token, purpose.id, title="English title", body="English body",
        translations={"hi": {"title": "Hindi title", "body": "Hindi body"}},
    )

    en = client.get(f"/public/NOTICE_RENDER_TENANT/notices/{purpose.code}")
    assert en.status_code == 200
    en_body = en.json()
    assert en_body["title"] == "English title"
    assert en_body["language_served"] == "en"
    assert en_body["data_items"][0]["data_category_id"] == category.id

    hi = client.get(f"/public/NOTICE_RENDER_TENANT/notices/{purpose.code}", params={"lang": "hi"})
    assert hi.status_code == 200
    hi_body = hi.json()
    assert hi_body["title"] == "Hindi title"
    assert hi_body["language_served"] == "hi"

    # Unsupported language falls back to the version's default, never 404s.
    fr = client.get(f"/public/NOTICE_RENDER_TENANT/notices/{purpose.code}", params={"lang": "fr"})
    assert fr.status_code == 200
    assert fr.json()["title"] == "English title"
    assert fr.json()["language_served"] == "en"


def test_public_notice_404_before_publish(client, staff_token, db):
    from app.models.entities import Organization

    org = Organization(name="Notice Unpub Tenant", code="NOTICE_UNPUB_TENANT", is_active=True)
    db.add(org)
    db.commit()
    purpose, _, _ = _make_purpose(db, "notice_unpub")
    client.post("/notices", json={"purpose_id": purpose.id, "title": "T", "body": "B"}, headers=_auth(staff_token))

    resp = client.get(f"/public/NOTICE_UNPUB_TENANT/notices/{purpose.code}")
    assert resp.status_code == 404


def test_grant_after_publication_links_consent_and_evidence_to_the_notice_version(client, staff_token, db):
    from datetime import datetime, timedelta, timezone

    from app.core.security import create_context_token
    from app.models.entities import ConsentContext, Customer
    from app.services import consent as consent_service

    purpose, category, activity = _make_purpose(db, "notice_link_consent")
    notice_id, published = _create_and_publish(client, staff_token, purpose.id)
    notice_version_id = published["versions"][0]["id"]

    customer = Customer(external_id="CUST-NOTICE-LINK-001", name="Notice Link Customer", source_app="NOTICE_LINK_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="NOTICE_LINK_TEST")
    consent_service.grant_consent(db, consent, source_app="NOTICE_LINK_TEST")
    db.commit()
    db.refresh(consent)

    assert consent.notice_version_id == notice_version_id
    evidence = consent.evidence[-1]
    assert evidence.notice_version_id == notice_version_id
    assert evidence.notice_hash == published["versions"][0]["content_hash"]


def test_delete_notice_retires_when_referenced_by_evidence(client, staff_token, db):
    from app.services import consent as consent_service
    from app.models.entities import Customer

    purpose, category, activity = _make_purpose(db, "notice_delete_inuse")
    notice_id, _ = _create_and_publish(client, staff_token, purpose.id)

    customer = Customer(external_id="CUST-NOTICE-DEL-001", name="Notice Delete Customer", source_app="NOTICE_DEL_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="NOTICE_DEL_TEST")
    consent_service.grant_consent(db, consent, source_app="NOTICE_DEL_TEST")
    db.commit()

    resp = client.delete(f"/notices/{notice_id}", headers=_auth(staff_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is False
    assert body["retired"] is True
    notice = db.get(Notice, notice_id)
    assert notice.status == "RETIRED"
    assert notice.is_active is False


def test_delete_notice_hard_deletes_when_unused(client, staff_token, db):
    purpose, _, _ = _make_purpose(db, "notice_delete_unused")
    create = client.post("/notices", json={"purpose_id": purpose.id, "title": "T", "body": "B"}, headers=_auth(staff_token))
    notice_id = create.json()["id"]

    resp = client.delete(f"/notices/{notice_id}", headers=_auth(staff_token))
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert db.get(Notice, notice_id) is None
