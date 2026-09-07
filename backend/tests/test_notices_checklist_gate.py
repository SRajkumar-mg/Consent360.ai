"""A-09 (R2-09): plain-language & dark-pattern review checklist gate on the
notice publish path.

Before this gate, `PUT /notices/{id}` then `POST /notices/{id}/publish`
published a new notice version with no reviewer and no checklist recorded,
returning 200 - notices.py validated only title/body, and NoticeVersion had
no column to hold a review record at all (unlike PurposeVersion/
PolicyVersion, which have carried an unused `checklist` column since
da570732eb52). These tests pin down the fix: publish refuses without a
completed checklist, naming what is missing; a checklist attached via PUT
lets it through; the stored record is readable afterwards as evidence.

Grandfathering decision (deliberate, not an oversight): a notice version
published *before* this column existed keeps `is_current=True` and is never
retroactively un-published - see `test_pre_existing_published_version_is_
grandfathered_not_unpublished` below. Un-publishing it would pull a live
notice out from under a running site with no replacement, and every
ConsentEvidence/Consent row that already pinned that notice_version_id would
be left pointing at "evidence" the platform had just invalidated - worse for
compliance than a NULL checklist on an old, otherwise-valid record. Only
*new* publishes (from here on) are gated; a version with checklist=None is
simply visible as one that predates the gate.
"""
from app.models.entities import DataCategory, Notice, NoticeVersion, ProcessingActivity, Purpose, PurposeVersion


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_purpose(db, code):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        description="A test purpose", data_category_ids=[category.id],
        processing_activity_ids=[activity.id], consent_text="I consent.",
        data_items=[{"data_category_id": category.id, "necessity": True, "description": "needed"}],
        is_current=True, created_by="test",
    ))
    db.commit()
    return purpose


_CHECKLIST = {
    "reviewer": "Priya Nair",
    "completed_at": "2026-09-04T00:00:00Z",
    "items": ["plain-language", "no-preselection", "no-confirmshaming"],
}


def test_publish_first_version_refuses_without_a_checklist(client, staff_token, db):
    """Reproduces the gap exactly: create v1 with no checklist, publish it -
    this must now be refused instead of returning 200."""
    purpose = _make_purpose(db, "gate_v1_noc")
    create = client.post("/notices", json={"purpose_id": purpose.id, "title": "T", "body": "B"},
                         headers=_auth(staff_token))
    assert create.status_code == 201, create.text
    notice_id = create.json()["id"]

    resp = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "checklist" in detail.lower()
    assert f"/notices/{notice_id}" in detail

    # Confirmed refused, not silently accepted: the notice never went live.
    notice = db.get(Notice, notice_id)
    assert notice.status == "DRAFT"
    assert notice.current_version == 0


def test_publish_first_version_succeeds_with_a_checklist_and_it_is_stored(client, staff_token, db):
    purpose = _make_purpose(db, "gate_v1_ok")
    create = client.post(
        "/notices",
        json={"purpose_id": purpose.id, "title": "T", "body": "B", "checklist": _CHECKLIST},
        headers=_auth(staff_token),
    )
    assert create.status_code == 201, create.text
    notice_id = create.json()["id"]

    resp = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert resp.status_code == 200, resp.text
    v1 = resp.json()["versions"][0]
    assert v1["checklist"]["reviewer"] == "Priya Nair"
    assert v1["checklist"]["items"] == _CHECKLIST["items"]

    # Readable afterwards as evidence, independent of the publish response.
    read = client.get(f"/notices/{notice_id}", headers=_auth(staff_token))
    assert read.status_code == 200
    stored = read.json()["versions"][0]["checklist"]
    assert stored["reviewer"] == "Priya Nair"
    assert stored["completed_at"] == "2026-09-04T00:00:00Z"


def test_content_edit_resets_checklist_and_republish_is_refused_until_resubmitted(client, staff_token, db):
    purpose = _make_purpose(db, "gate_v2_reset")
    create = client.post(
        "/notices",
        json={"purpose_id": purpose.id, "title": "Original", "body": "B", "checklist": _CHECKLIST},
        headers=_auth(staff_token),
    )
    notice_id = create.json()["id"]
    assert client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token)).status_code == 200

    # Editing the live version drafts v2 - content changed, no checklist
    # resubmitted, so the prior review no longer covers it.
    update = client.put(f"/notices/{notice_id}", json={"title": "Changed title"}, headers=_auth(staff_token))
    assert update.status_code == 200
    v2 = next(v for v in update.json()["versions"] if v["version_number"] == 2)
    assert v2["checklist"] is None

    refused = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert refused.status_code == 422, refused.text
    assert "checklist" in refused.json()["detail"].lower()

    # Attach a fresh checklist (no further content change) and it publishes.
    attach = client.put(f"/notices/{notice_id}", json={"checklist": _CHECKLIST}, headers=_auth(staff_token))
    assert attach.status_code == 200
    v2_again = next(v for v in attach.json()["versions"] if v["version_number"] == 2)
    assert v2_again["title"] == "Changed title"  # the earlier content edit was not lost
    assert v2_again["checklist"]["reviewer"] == "Priya Nair"

    succeeded = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert succeeded.status_code == 200, succeeded.text
    published_v2 = next(v for v in succeeded.json()["versions"] if v["version_number"] == 2)
    assert published_v2["is_current"] is True
    assert published_v2["checklist"]["reviewer"] == "Priya Nair"


def test_publish_refuses_a_checklist_with_no_reviewer_or_no_items(client, staff_token, db):
    """Pydantic's ReviewChecklistIn already requires a non-empty reviewer at
    the schema level, so this reaches the route-level check through the one
    gap that schema leaves open: an empty `items` list is schema-valid."""
    purpose = _make_purpose(db, "gate_empty_items")
    create = client.post(
        "/notices",
        json={
            "purpose_id": purpose.id, "title": "T", "body": "B",
            "checklist": {"reviewer": "Priya Nair", "completed_at": "2026-09-04T00:00:00Z", "items": []},
        },
        headers=_auth(staff_token),
    )
    assert create.status_code == 201, create.text
    notice_id = create.json()["id"]

    resp = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert resp.status_code == 422, resp.text
    assert "at least one confirmed checklist item" in resp.json()["detail"]


def test_checklist_only_update_does_not_disturb_unrelated_content(client, staff_token, db):
    """A PUT that supplies only `checklist` (no title/body/etc.) must not be
    treated as a content change - it should not wipe anything, and should
    let publish through."""
    purpose = _make_purpose(db, "gate_checklist_only")
    create = client.post(
        "/notices", json={"purpose_id": purpose.id, "title": "Keep me", "body": "Keep me too"},
        headers=_auth(staff_token),
    )
    notice_id = create.json()["id"]

    attach = client.put(f"/notices/{notice_id}", json={"checklist": _CHECKLIST}, headers=_auth(staff_token))
    assert attach.status_code == 200
    v1 = attach.json()["versions"][0]
    assert v1["title"] == "Keep me"
    assert v1["body"] == "Keep me too"
    assert v1["checklist"]["reviewer"] == "Priya Nair"

    resp = client.post(f"/notices/{notice_id}/publish", headers=_auth(staff_token))
    assert resp.status_code == 200, resp.text


def test_pre_existing_published_version_is_grandfathered_not_unpublished(client, staff_token, db):
    """A notice version published before the checklist column existed has
    checklist=None. This must not retroactively un-publish it - is_current
    stays True and it is still served as the live version. Simulated here by
    inserting a NoticeVersion straight through the ORM the way a pre-gate
    publish would have left it, bypassing the API's own (now-gated) publish
    path entirely."""
    from app.services.tenancy import platform_tenant_id

    purpose = _make_purpose(db, "gate_grandfather")
    notice = Notice(
        tenant_id=platform_tenant_id(db), purpose_id=purpose.id,
        status="ACTIVE", current_version=1, is_active=True,
    )
    db.add(notice)
    db.flush()
    nv = NoticeVersion(
        notice_id=notice.id, version_number=1, title="Pre-gate notice", body="Published long ago",
        is_current=True, checklist=None, created_by="system",
    )
    db.add(nv)
    db.commit()

    resp = client.get(f"/notices/{notice.id}", headers=_auth(staff_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ACTIVE"
    v1 = body["versions"][0]
    assert v1["is_current"] is True
    assert v1["checklist"] is None

    public = client.get(f"/public/{_platform_tenant_code(db)}/notices/{purpose.code}")
    assert public.status_code == 200
    assert public.json()["title"] == "Pre-gate notice"


def _platform_tenant_code(db):
    from app.services.tenancy import PLATFORM_TENANT_CODE

    return PLATFORM_TENANT_CODE
