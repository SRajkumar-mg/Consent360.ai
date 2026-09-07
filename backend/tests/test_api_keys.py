from datetime import datetime, timedelta, timezone

from app.core.api_keys import generate_api_key
from app.core.encryption import hmac_digest
from app.models.entities import ApiKey, Customer, Organization

INVALID_KEY_DETAIL = "Invalid integration API key"


def _make_org(db, code="APIKEY_TEST"):
    org = Organization(name=code.title(), code=code, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def test_legacy_key_still_works_by_default(client):
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={"name": "Legacy Caller", "email": "legacy-caller@example.com", "source_app": "LEGACY_APP"},
    )
    assert resp.status_code == 200
    assert resp.json()["source_app"] == "LEGACY_APP"


def test_new_format_key_resolves_tenant_and_stamps_source_app(db, client):
    org = _make_org(db, "APIKEY_RESOLVE")
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=["integration.write"]))
    db.commit()

    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": plaintext},
        json={"name": "Key Caller", "email": "key-caller@example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["source_app"] == "APIKEY_RESOLVE"


def test_new_format_key_rejects_mismatched_source_app(db, client):
    org = _make_org(db, "APIKEY_MISMATCH")
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=["integration.write"]))
    db.commit()

    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": plaintext},
        json={"name": "Bad Caller", "email": "bad-caller@example.com", "source_app": "SOME_OTHER_APP"},
    )
    assert resp.status_code == 403


def test_api_key_crud_roundtrip(db, client, staff_token):
    org = _make_org(db, "APIKEY_CRUD")
    headers = {"Authorization": f"Bearer {staff_token}"}

    created = client.post(f"/organizations/{org.id}/api-keys", headers=headers, json={"name": "ci-key"})
    assert created.status_code == 200
    body = created.json()
    assert body["plaintext_key"].startswith("c360_apikey_crud_")

    listed = client.get(f"/organizations/{org.id}/api-keys", headers=headers)
    assert listed.status_code == 200
    assert all("plaintext_key" not in row for row in listed.json())

    rotated = client.post(f"/organizations/{org.id}/api-keys/{body['id']}/rotate", headers=headers)
    assert rotated.status_code == 200
    assert rotated.json()["key_prefix"] != body["key_prefix"]

    # The old plaintext (from creation) must stop working once rotated...
    old_key_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": body["plaintext_key"]},
        json={"name": "Stale Key Caller", "email": "stale-key-caller@example.com"},
    )
    assert old_key_resp.status_code == 401

    # ...while the newly rotated plaintext works.
    new_key_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": rotated.json()["plaintext_key"]},
        json={"name": "Fresh Key Caller", "email": "fresh-key-caller@example.com"},
    )
    assert new_key_resp.status_code == 200
    assert new_key_resp.json()["source_app"] == "APIKEY_CRUD"

    revoked = client.delete(f"/organizations/{org.id}/api-keys/{body['id']}", headers=headers)
    assert revoked.status_code == 200

    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": rotated.json()["plaintext_key"]},
        json={"name": "Revoked Caller", "email": "revoked-caller@example.com"},
    )
    assert resp.status_code == 401

    from app.models.entities import AuditLog

    events = {
        row.event
        for row in db.query(AuditLog).filter(AuditLog.source_app == "APIKEY_CRUD").all()
    }
    assert {"API_KEY_CREATED", "API_KEY_ROTATED", "API_KEY_REVOKED"} <= events


def test_legacy_key_rejected_when_disabled(client, monkeypatch):
    from app.api import deps

    monkeypatch.setattr(deps.settings, "ALLOW_LEGACY_INTEGRATION_KEY", False)
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={"name": "Should Fail", "email": "should-fail@example.com"},
    )
    assert resp.status_code == 401


def test_key_cannot_purge_customer_of_another_tenant(db, client):
    org_a = _make_org(db, "APIKEY_PURGE_A")
    plaintext_a, prefix_a, hash_a = generate_api_key(org_a.code)
    db.add(ApiKey(tenant_id=org_a.id, name="ci", key_prefix=prefix_a, key_hash=hash_a, scopes=["integration.write"]))
    db.commit()

    # Customer belongs to a different tenant (APIKEY_PURGE_B), created via the
    # unscoped legacy key with an explicit source_app.
    email = "tenant-b-customer@example.com"
    created = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={"name": "Tenant B Customer", "email": email, "source_app": "APIKEY_PURGE_B"},
    )
    assert created.status_code == 200

    purge_resp = client.delete(
        f"/crm/customers/by-email/{email}",
        headers={"X-API-Key": plaintext_a},
    )
    # org_a's key was never scoped for customer.purge at all - this 403
    # comes from require_scope, before purge_customer_by_email_core (and
    # its tenant check) is ever reached. See
    # test_purge_by_email_refuses_a_properly_scoped_key_across_tenants for
    # the R3 tenant-mismatch check in isolation.
    assert purge_resp.status_code == 403

    still_there = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    assert still_there is not None


def test_key_for_inactive_tenant_is_rejected(db, client):
    org = _make_org(db, "APIKEY_INACTIVE")
    org.is_active = False
    db.commit()
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=["integration.write"]))
    db.commit()

    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": plaintext},
        json={"name": "Inactive Tenant Caller", "email": "inactive-tenant-caller@example.com"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == INVALID_KEY_DETAIL


def test_purge_requires_customer_purge_scope(db, client):
    """R1-09 finding: ApiKey.scopes was stored but never enforced - any
    tenant-bound key could purge any customer of its own tenant regardless
    of what scopes it was actually granted. Choice made: enforce, don't
    remove (see app.api.deps.require_scope / app.core.api_keys.py)."""
    from app.core.api_keys import SCOPE_CUSTOMER_PURGE, SCOPE_INTEGRATION_WRITE

    org = _make_org(db, "APIKEY_PURGE_SCOPE")
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=[SCOPE_INTEGRATION_WRITE]))
    db.commit()

    email = "purge-scope-test@example.com"
    created = client.post(
        "/consent/customer-context", headers={"X-API-Key": plaintext},
        json={"name": "Purge Scope Customer", "email": email},
    )
    assert created.status_code == 200

    # Without SCOPE_CUSTOMER_PURGE, purging a customer of this key's OWN
    # tenant is still rejected - tenant-match alone used to be sufficient.
    resp = client.delete(f"/crm/customers/by-email/{email}", headers={"X-API-Key": plaintext})
    assert resp.status_code == 403

    still_there = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    assert still_there is not None
    assert still_there.status != "ANONYMISED"

    # Granting the scope (as if rotated/updated by an admin) makes the same
    # call succeed.
    key_row = db.query(ApiKey).filter(ApiKey.tenant_id == org.id).first()
    key_row.scopes = [SCOPE_INTEGRATION_WRITE, SCOPE_CUSTOMER_PURGE]
    db.commit()

    resp = client.delete(f"/crm/customers/by-email/{email}", headers={"X-API-Key": plaintext})
    assert resp.status_code == 200


def test_legacy_key_is_no_longer_exempt_from_scope_checks(db, client):
    """R3 fix ("require a credential and a tenant to purge a customer"):
    the legacy, unbound key used to be exempt from every require_scope()
    check, so it could purge ANY tenant's customer despite never having
    been granted SCOPE_CUSTOMER_PURGE. It now implicitly carries only
    SCOPE_INTEGRATION_WRITE (see app.api.deps.require_scope) - enough to
    still mint a consent context, never enough to purge."""
    email = "legacy-no-longer-exempt@example.com"
    created = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={"name": "Legacy Purge Target", "email": email, "source_app": "LEGACY_PURGE_APP"},
    )
    assert created.status_code == 200

    resp = client.delete(
        f"/crm/customers/by-email/{email}",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
    )
    assert resp.status_code == 403

    still_there = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    assert still_there is not None
    assert still_there.status != "ANONYMISED"


def test_expired_key_is_rejected(db, client):
    org = _make_org(db, "APIKEY_EXPIRED")
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(
        tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash,
        scopes=["integration.write"], expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    ))
    db.commit()

    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": plaintext},
        json={"name": "Expired Key Caller", "email": "expired-key-caller@example.com"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == INVALID_KEY_DETAIL
