"""Round 3 regression tests: the structural fix (resolve_customer) and the
three concrete instances an adversarial re-review found live.

See app/services/tenancy.py::resolve_customer and
tests/test_customer_resolution_guard.py for the choke point itself and the
guard that keeps future call sites honest.
"""
from datetime import datetime, timedelta, timezone

from app.core.api_keys import SCOPE_INTEGRATION_WRITE, generate_api_key
from app.core.encryption import hmac_digest
from app.models.entities import ApiKey, AuditLog, ConsentContext, Customer, Organization

LEGACY_KEY = "dev-demo-integration-key-2026"


def _make_org_with_key(db, code, scopes):
    org = Organization(name=code.title(), code=code, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    plaintext, prefix, key_hash = generate_api_key(code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=scopes))
    db.commit()
    return org, plaintext


def _make_org_scoped_staff(db, role_name):
    from app.core.rbac import ROLE_PERMISSIONS
    from app.core.security import create_access_token, hash_password
    from app.models.entities import Role, User

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        role = Role(name=role_name, description=role_name, permissions=ROLE_PERMISSIONS[role_name])
        db.add(role)
        db.flush()
    username = f"{role_name}-round3-test"
    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(
            username=username, full_name=username,
            email=f"{username}@example.com", email_search=hmac_digest(f"{username}@example.com"),
            password_hash=hash_password("Test@1234"), role_id=role.id, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return create_access_token(user.id, user.username, role.name)


# ---------------------------------------------------------------------------
# 1. consents.py::customer_summary / export_customer_consents
# ---------------------------------------------------------------------------

def test_customer_summary_refuses_a_customer_outside_org_scope(db, client):
    """A codex_admin token given a SkillLearn customer's external id must
    not get that customer's real name/email/phone - before R3 only the
    nested Consent query was scoped, not the Customer lookup itself."""
    codex_token = _make_org_scoped_staff(db, "codex_admin")
    victim = Customer(
        external_id="CUST-R3-SUMMARY-VICTIM", name="SkillLearn Real Name",
        email="r3-summary-victim@example.com", phone="+910000000010", source_app="SKILLLEARN",
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)

    resp = client.get(
        f"/consents/summary/{victim.external_id}", headers={"Authorization": f"Bearer {codex_token}"}
    )
    assert resp.status_code == 404
    assert "SkillLearn Real Name" not in resp.text
    assert "r3-summary-victim@example.com" not in resp.text


def test_export_customer_consents_refuses_a_customer_outside_org_scope(db, client):
    """The PDF export endpoint shares the same unscoped-lookup bug."""
    codex_token = _make_org_scoped_staff(db, "codex_admin")
    victim = Customer(
        external_id="CUST-R3-EXPORT-VICTIM", name="SkillLearn Export Victim",
        email="r3-export-victim@example.com", phone="+910000000011", source_app="SKILLLEARN",
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)

    resp = client.get(
        f"/consents/export/{victim.external_id}", headers={"Authorization": f"Bearer {codex_token}"}
    )
    assert resp.status_code == 404


def test_list_consents_customer_filter_is_scoped(db, client):
    """/consents?customer_id=<external_id> must return nothing for a
    customer outside the caller's org scope, not silently ignore the scope
    for this one filter."""
    codex_token = _make_org_scoped_staff(db, "codex_admin")
    victim = Customer(
        external_id="CUST-R3-LIST-VICTIM", name="SkillLearn List Victim",
        email="r3-list-victim@example.com", source_app="SKILLLEARN",
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)

    resp = client.get(
        "/consents", params={"customer_id": victim.external_id},
        headers={"Authorization": f"Bearer {codex_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 2. scripts/merge_duplicate_customers.py
# ---------------------------------------------------------------------------

def test_merge_script_refuses_to_merge_across_tenants(db, tmp_path):
    """Two customers sharing an email at two DIFFERENT tenants must never
    be merged - the grouping key now includes source_app, so this is not a
    guard that can be bypassed, it is a group that cannot exist."""
    import scripts.merge_duplicate_customers as merge_script

    email = "shared-across-tenants@example.com"
    victim_a = Customer(external_id="CUST-MERGE-A", name="Tenant A Person", email=email, source_app="MERGE_TENANT_A")
    victim_b = Customer(external_id="CUST-MERGE-B", name="Tenant B Person", email=email, source_app="MERGE_TENANT_B")
    db.add_all([victim_a, victim_b])
    db.commit()
    db.refresh(victim_a)
    db.refresh(victim_b)

    merge_script.main(snapshot_dir=tmp_path)  # dry run - argv has no --apply in a pytest run

    db.expire_all()
    still_a = db.get(Customer, victim_a.id)
    still_b = db.get(Customer, victim_b.id)
    assert still_a is not None
    assert still_b is not None
    assert still_a.source_app == "MERGE_TENANT_A"
    assert still_b.source_app == "MERGE_TENANT_B"


def test_merge_script_still_merges_true_same_tenant_duplicates(db, monkeypatch, tmp_path):
    """Control: the script's actual job - deduplicating two rows for the
    SAME email at the SAME tenant - must still work."""
    import sys

    import scripts.merge_duplicate_customers as merge_script

    email = "same-tenant-duplicate@example.com"
    keeper = Customer(external_id="CUST-MERGE-KEEP", name="Keeper", email=email, source_app="MERGE_SAME_TENANT")
    dup = Customer(external_id="CUST-MERGE-DUP", name="Duplicate", email=email, source_app="MERGE_SAME_TENANT")
    db.add_all([keeper, dup])
    db.commit()
    db.refresh(keeper)
    db.refresh(dup)
    keeper_id, dup_id = keeper.id, dup.id

    monkeypatch.setattr(sys, "argv", ["merge_duplicate_customers.py", "--apply"])
    merge_script.main(snapshot_dir=tmp_path)

    db.expire_all()
    assert db.get(Customer, keeper_id) is not None
    assert db.get(Customer, dup_id) is None


# ---------------------------------------------------------------------------
# 3. POST /crm/login
# ---------------------------------------------------------------------------

def test_crm_login_rejects_a_source_app_outside_the_allowed_family(client):
    resp = client.post(
        "/crm/login",
        json={"name": "Anonymous Caller", "email": "anon-caller@example.com", "source_app": "SOME_REAL_TENANT"},
    )
    assert resp.status_code == 422


def test_crm_login_cannot_reach_a_customer_outside_the_crm_family(db, client):
    """Before R3, /crm/login took no credential, source_app was a free
    string, and its existence check was global by email - so an anonymous
    request naming a real tenant's source_app and a victim's email
    overwrote that customer's name and phone. Now source_app is
    allow-listed to the three sibling sites, so a customer belonging to a
    real, UNRELATED tenant must be completely unreachable regardless."""
    victim = Customer(
        external_id="CUST-CRMLOGIN-VICTIM", name="Real Tenant Victim",
        email="crmlogin-victim@example.com", phone="+910000000020", source_app="REAL_UNRELATED_TENANT",
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)

    resp = client.post(
        "/crm/login",
        json={
            "name": "Attacker Controlled Name", "email": "crmlogin-victim@example.com",
            "phone": "+919999999998", "source_app": "CODEX",
        },
    )
    assert resp.status_code == 200

    db.refresh(victim)
    assert victim.name == "Real Tenant Victim"
    assert victim.phone == "+910000000020"

    attacker_side = db.query(Customer).filter(
        Customer.email_search == hmac_digest("crmlogin-victim@example.com"),
        Customer.source_app == "CODEX",
    ).first()
    assert attacker_side is not None
    assert attacker_side.id != victim.id
    assert attacker_side.name == "Attacker Controlled Name"


def test_crm_login_still_shares_identity_within_the_family(db, client):
    """Control: the intentional CRM/Codex/SkillLearn shared identity must
    still work - a customer created via CRM_PORTAL is still recognised
    (same row) when the same email logs in via CODEX."""
    email = "family-shared@example.com"
    first = client.post(
        "/crm/login",
        json={"name": "Family Person", "email": email, "source_app": "CRM_PORTAL"},
    )
    assert first.status_code == 200
    second = client.post(
        "/crm/login",
        json={"name": "Family Person", "email": email, "source_app": "CODEX"},
    )
    assert second.status_code == 200

    matches = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).all()
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# Squat-then-claim
# ---------------------------------------------------------------------------

def test_legacy_key_auto_provisioning_a_new_tenant_is_logged(db, client):
    resp = client.post(
        "/consent/customer-context", headers={"X-API-Key": LEGACY_KEY},
        json={"name": "Squatter", "email": "squatter@example.com", "source_app": "SQUAT_CANDIDATE_TENANT"},
    )
    assert resp.status_code == 200
    event = (
        db.query(AuditLog)
        .filter(AuditLog.event == "TENANT_AUTO_PROVISIONED_VIA_LEGACY_KEY", AuditLog.source_app == "SQUAT_CANDIDATE_TENANT")
        .first()
    )
    assert event is not None


def test_issuing_first_key_for_a_tenant_with_preexisting_customers_is_refused(db, client, staff_token):
    org = Organization(name="Preexisting Data Org", code="PREEXISTING_DATA_ORG", is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    planted = Customer(
        external_id="CUST-PLANTED-BEFORE-KEY", name="Planted", source_app="PREEXISTING_DATA_ORG", tenant_id=org.id,
    )
    db.add(planted)
    db.commit()

    headers = {"Authorization": f"Bearer {staff_token}"}
    refused = client.post(f"/organizations/{org.id}/api-keys", headers=headers, json={"name": "first-key"})
    assert refused.status_code == 409

    acknowledged = client.post(
        f"/organizations/{org.id}/api-keys", headers=headers,
        json={"name": "first-key", "confirm_preexisting_data": True},
    )
    assert acknowledged.status_code == 200


def test_issuing_a_key_for_a_fresh_org_is_unaffected(db, client, staff_token):
    """Control: the ordinary case (seed_orgs.py then seed_api_keys.py, or an
    admin creating an org and immediately issuing its first key) never has
    pre-existing customers and must not be asked to confirm anything."""
    org = Organization(name="Fresh Org", code="FRESH_ORG_NO_CUSTOMERS", is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)

    resp = client.post(
        f"/organizations/{org.id}/api-keys", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "first-key"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# JWT context-token nonce
# ---------------------------------------------------------------------------

def test_rapid_repeated_context_mints_do_not_collide(db, client):
    """create_context_token had no nonce and JWT exp/iat are second-
    granularity, so two mints for the same customer/source_app within one
    wall-clock second produced a byte-identical token, and
    consent_contexts.token is unique - the second insert raised an
    IntegrityError (500)."""
    _org, key = _make_org_with_key(db, "NONCE_TEST_TENANT", [SCOPE_INTEGRATION_WRITE])
    for _ in range(5):
        resp = client.post(
            "/consent/customer-context", headers={"X-API-Key": key},
            json={"name": "Nonce Test", "email": "nonce-test@example.com"},
        )
        assert resp.status_code == 200

    contexts = db.query(ConsentContext).filter(ConsentContext.source_app == "NONCE_TEST_TENANT").all()
    assert len(contexts) == 5
    assert len({c.token for c in contexts}) == 5
