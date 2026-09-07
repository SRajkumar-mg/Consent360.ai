"""Regression test for the R3 "require a credential and a tenant to purge a
customer" fix.

An adversarial review demonstrated a fully unauthenticated, two-call
exploit against HEAD 776cdb9:

    POST /crm/login          (no headers at all)
      {"name": "x", "email": "<victim's real email>", "source_app": "CODEX"}
    DELETE /crm/customers/{the new directory row's id}   (no headers at all)

The victim - an ordinary customer of a different, fully-onboarded tenant
with its own API key, who never touched the CRM/Codex/SkillLearn sites -
ended up anonymised (name/email wiped, status=ANONYMISED).

Root causes, all now closed:
  1. crm_directory.py::delete_crm_customer had no authentication dependency
     at all, and cascaded an unverified, self-asserted identity into the
     shared purge logic.
  2. purge_customer_by_email_core defaulted an unpassed/falsy scope to
     ANY_TENANT - crm_directory.py's call never passed one, so it silently
     inherited "any tenant, no restriction at all" despite taking no
     credential whatsoever.
  3. app.api.deps.require_scope exempted the legacy, unbound
     INTEGRATION_API_KEY from every scope check, so even a caller
     authenticated with that key - but never granted SCOPE_CUSTOMER_PURGE -
     could purge any tenant's customer (covered separately in
     test_api_keys.py::test_legacy_key_is_no_longer_exempt_from_scope_checks).

Fix: purge_customer_by_email_core's `scope` is now a required keyword with
no default, and crm_directory.py's unauthenticated call site passes
CRM_FAMILY_SOURCE_APPS explicitly - the demo directory stays credential-free
(matching /crm/login's own already-documented, accepted trust model for
these three specific sites), but its blast radius is now structurally
confined to them and can never reach a real, unrelated, independently
authenticated tenant.
"""
from app.core.encryption import hmac_digest
from app.models.entities import Customer, Organization


def _make_victim(db, *, source_app="CRM_PURGE_EXPLOIT_VICTIM_TENANT", email="victim@unrelated-tenant.example.com"):
    org = db.query(Organization).filter(Organization.code == source_app).first()
    if not org:
        org = Organization(name=source_app.title(), code=source_app, is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    victim = Customer(
        external_id="VICTIM-001",
        external_id_search=hmac_digest("VICTIM-001"),
        name="Real Victim",
        email=email,
        email_search=hmac_digest(email),
        phone="+91-90000-00099",
        status="ACTIVE",
        source_app=source_app,
        tenant_id=org.id,
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)
    return victim


def test_unauthenticated_crm_directory_delete_cannot_purge_a_customer_outside_the_family(db, client):
    victim_email = "victim@unrelated-tenant.example.com"
    victim = _make_victim(db, email=victim_email)

    # Step 1: unauthenticated CRM login claiming the victim's email under
    # one of the three demo sites - no header of any kind.
    login_resp = client.post(
        "/crm/login",
        json={"name": "Attacker Claim", "email": victim_email, "source_app": "CODEX"},
    )
    assert login_resp.status_code == 200
    crm_customer_id = login_resp.json()["customer"]["id"]

    # Step 2: unauthenticated delete of that (attacker-created) directory
    # row - again, no header of any kind.
    delete_resp = client.delete(f"/crm/customers/{crm_customer_id}")
    assert delete_resp.status_code == 200

    # The victim's REAL Consent360 profile must be completely untouched -
    # assert the actual row, not merely that a status code changed.
    db.refresh(victim)
    assert victim.status == "ACTIVE"
    assert victim.name == "Real Victim"
    assert victim.email_search == hmac_digest(victim_email)
    assert victim.anonymised_ref is None
    assert victim.external_id == "VICTIM-001"


def test_unauthenticated_crm_directory_delete_purges_a_same_family_customer(db, client):
    """The demo directory's accepted trust model (see /crm/login's own
    docstring) is unchanged for the three sites it actually serves: a
    customer of CODEX/SKILLLEARN/CRM_PORTAL itself is still reachable -
    this asserts the fix didn't overcorrect into breaking the feature."""
    email = "same-family-customer@example.com"
    login_resp = client.post(
        "/crm/login",
        json={"name": "Family Customer", "email": email, "source_app": "CODEX"},
    )
    assert login_resp.status_code == 200
    crm_customer_id = login_resp.json()["customer"]["id"]

    found = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    assert found is not None and found.source_app == "CODEX"

    delete_resp = client.delete(f"/crm/customers/{crm_customer_id}")
    assert delete_resp.status_code == 200

    db.refresh(found)
    assert found.status == "ANONYMISED"
    assert found.anonymised_ref is not None
