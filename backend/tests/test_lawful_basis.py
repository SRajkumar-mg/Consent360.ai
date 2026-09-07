"""R1-05: lawful basis enum + itemised purpose data model (B-06, L-01, L-02, L-03)."""
import pytest
from sqlalchemy.exc import DBAPIError

from app.models.entities import DataCategory, ProcessingActivity, Purpose, PurposeVersion


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_purpose_rejects_invalid_legal_basis(client, staff_token):
    resp = client.post(
        "/purposes",
        json={"name": "Bad Basis", "code": "bad_basis_purpose", "legal_basis": "LEGITIMATE_INTEREST"},
        headers=_auth(staff_token),
    )
    assert resp.status_code == 422


def test_create_purpose_accepts_every_s7_clause(client, staff_token):
    for letter in "ABCDEFGHI":
        basis = f"S7_{letter}"
        resp = client.post(
            "/purposes",
            json={
                "name": f"Purpose {basis}", "code": f"purpose_{basis.lower()}",
                "legal_basis": basis, "requires_consent": False,
            },
            headers=_auth(staff_token),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["legal_basis"] == basis
        assert body["versions"][0]["legal_basis"] == basis


def test_database_check_constraint_blocks_invalid_legal_basis_bypassing_the_api(db):
    """Defense in depth (B-06): even a direct ORM write - a script, a future
    migration, seed.py making a mistake again - cannot persist an invalid
    gateway, because the API's Pydantic validator is not the only thing
    stopping it."""
    purpose = Purpose(name="Sneaky", code="sneaky_purpose", legal_basis="LEGITIMATE_INTEREST")
    db.add(purpose)
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_purpose_update_versions_when_legal_basis_changes_and_preserves_old_version(client, staff_token):
    create = client.post(
        "/purposes",
        json={"name": "Versioned Basis", "code": "versioned_basis_purpose", "legal_basis": "CONSENT"},
        headers=_auth(staff_token),
    )
    assert create.status_code == 201
    purpose_id = create.json()["id"]

    update = client.put(
        f"/purposes/{purpose_id}",
        json={"legal_basis": "S7_I", "requires_consent": False},
        headers=_auth(staff_token),
    )
    assert update.status_code == 200
    body = update.json()
    assert body["legal_basis"] == "S7_I"
    versions = sorted(body["versions"], key=lambda v: v["version_number"])
    assert len(versions) == 2
    assert versions[0]["legal_basis"] == "CONSENT" and versions[0]["is_current"] is False
    assert versions[1]["legal_basis"] == "S7_I" and versions[1]["is_current"] is True


def test_data_items_services_enabled_child_restricted_retention_policy_round_trip(client, staff_token):
    cat_resp = client.post(
        "/data-categories", json={"name": "Itemised Cat", "code": "itemised_cat"}, headers=_auth(staff_token),
    )
    assert cat_resp.status_code == 201
    category_id = cat_resp.json()["id"]

    resp = client.post(
        "/purposes",
        json={
            "name": "Itemised Purpose", "code": "itemised_purpose", "legal_basis": "CONSENT",
            "data_category_ids": [category_id],
            "data_items": [{"data_category_id": category_id, "necessity": True, "description": "needed for X"}],
            "services_enabled": "Shows X to the user.",
            "child_restricted": True,
            "retention_policy_id": "RET-001",
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["services_enabled"] == "Shows X to the user."
    assert body["child_restricted"] is True
    assert body["retention_policy_id"] == "RET-001"
    pv = body["versions"][0]
    assert pv["data_items"] == [{"data_category_id": category_id, "necessity": True, "description": "needed for X"}]


def test_purpose_templates_endpoint_offers_employment_and_state_gateways(client, staff_token):
    resp = client.get("/purposes/templates", headers=_auth(staff_token))
    assert resp.status_code == 200
    templates = {t["key"]: t for t in resp.json()}
    assert templates["employment"]["purpose"]["legal_basis"] == "S7_I"
    assert templates["employment"]["purpose"]["requires_consent"] is False
    assert templates["state_benefit"]["purpose"]["legal_basis"] == "S7_B"
    assert templates["state_benefit"]["purpose"]["requires_consent"] is False

    # The template payload is a real PurposeIn - prove it can actually be
    # submitted to create a purpose, not just cosmetic scaffolding.
    payload = dict(templates["employment"]["purpose"])
    create = client.post("/purposes", json=payload, headers=_auth(staff_token))
    assert create.status_code == 201
    assert create.json()["legal_basis"] == "S7_I"


def _seed_full_coverage(db, suffix=""):
    cat = DataCategory(name=f"Coverage Cat{suffix}", code=f"coverage_cat{suffix}")
    act_covered = ProcessingActivity(name=f"Covered Act{suffix}", code=f"coverage_act_covered{suffix}")
    db.add_all([cat, act_covered])
    db.flush()
    purpose = Purpose(name=f"Coverage Purpose{suffix}", code=f"coverage_purpose{suffix}", legal_basis="CONSENT", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[cat.id], processing_activity_ids=[act_covered.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return act_covered


def test_coverage_report_reports_100_percent_when_every_activity_has_a_gateway(client, staff_token, db):
    # Isolate: only activities created in *this* test should be counted, so
    # deactivate anything left over from other tests in this session.
    db.query(ProcessingActivity).update({ProcessingActivity.is_active: False})
    db.commit()
    _seed_full_coverage(db)

    resp = client.get("/purposes/coverage-report", headers=_auth(staff_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_activities"] == 1
    assert body["covered_activities"] == 1
    assert body["coverage_pct"] == 100.0
    assert body["activities"][0]["covered"] is True
    assert "CONSENT" in body["activities"][0]["gateways"]


def test_coverage_report_flags_an_uncovered_activity(client, staff_token, db):
    db.query(ProcessingActivity).update({ProcessingActivity.is_active: False})
    db.commit()
    _seed_full_coverage(db, suffix="_2")
    orphan = ProcessingActivity(name="Orphan Act", code="coverage_act_orphan")
    db.add(orphan)
    db.commit()

    resp = client.get("/purposes/coverage-report", headers=_auth(staff_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_activities"] == 2
    assert body["covered_activities"] == 1
    assert body["coverage_pct"] == 50.0
    orphan_row = next(a for a in body["activities"] if a["code"] == "coverage_act_orphan")
    assert orphan_row["covered"] is False
    assert orphan_row["gateways"] == []
