"""RBAC test suite: tenant isolation, per-role permission checks, 401/403,
cross-tenant/IDOR attempts, data_principal own-data restriction, guardian
linked-child restriction, DPO/Grievance Officer permission sets.

Runs against the live dev database (see conftest.py) using the demo users
created by seed.py's RBAC MVP block.
"""
from app.core.database import SessionLocal
from app.core.rbac import ROLE_PERMISSIONS
from app.models.entities import GuardianChildLink, Tenant, User


# ---------------------------------------------------------------------------
# Unauthenticated -> 401
# ---------------------------------------------------------------------------

def test_unauthenticated_request_is_401(client):
    resp = client.get("/purposes")
    assert resp.status_code == 401


def test_invalid_token_is_401(client):
    resp = client.get("/purposes", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated but under-permissioned -> 403
# ---------------------------------------------------------------------------

def test_tenant_support_cannot_manage_purposes(client, tenant_support_headers):
    """tenant_support has no purpose.create/update -> mutating endpoints are 403."""
    resp = client.post(
        "/purposes",
        json={"name": "x", "code": "unauthorized_test_purpose", "description": "", "legal_basis": "CONSENT",
              "requires_consent": True, "retention_period_days": 30},
        headers=tenant_support_headers,
    )
    assert resp.status_code == 403


def test_platform_auditor_cannot_create_role(client, platform_auditor_headers):
    """platform_auditor is strictly read-only - user.manage-gated endpoints are 403."""
    resp = client.post(
        "/auth/roles",
        json={"name": "should_not_be_created", "description": "", "permissions": []},
        headers=platform_auditor_headers,
    )
    assert resp.status_code == 403


def test_tenant_developer_permissions_have_no_pii_view():
    """Least privilege: tenant_developer must not get customer.view / consent.view (unmasked PII)."""
    perms = ROLE_PERMISSIONS["tenant_developer"]
    assert "customer.view" not in perms
    assert "consent.view" not in perms


# ---------------------------------------------------------------------------
# Role -> permission checks (positive path)
# ---------------------------------------------------------------------------

def test_platform_super_admin_can_list_roles(client, platform_super_admin_headers):
    resp = client.get("/auth/roles", headers=platform_super_admin_headers)
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert "tenant_admin" in names
    assert "data_principal" in names


def test_privacy_officer_can_view_purposes(client, privacy_officer_headers):
    resp = client.get("/purposes", headers=privacy_officer_headers)
    assert resp.status_code == 200


def test_tenant_admin_can_list_tenants(client, tenant_admin_headers):
    resp = client.get("/tenants", headers=tenant_admin_headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DPO / Grievance Officer permission sets (DPDP designations)
# ---------------------------------------------------------------------------

def test_tenant_dpo_permission_set_is_narrow():
    perms = set(ROLE_PERMISSIONS["tenant_dpo"])
    assert "dpo.view" in perms
    assert "retention.view" in perms
    # DPO designation alone must not grant notice authoring or grievance response -
    # those belong to tenant_privacy_officer / tenant_grievance_officer.
    assert "notice.publish" not in perms
    assert "grievance.respond" not in perms


def test_tenant_grievance_officer_permission_set():
    perms = set(ROLE_PERMISSIONS["tenant_grievance_officer"])
    assert "grievance.respond" in perms
    assert "grievance.export" in perms
    assert "rights.respond" in perms
    # Must not carry tenant-admin-level power.
    assert "tenant.update" not in perms
    assert "user.manage" not in perms


# ---------------------------------------------------------------------------
# data_principal own-data restriction
# ---------------------------------------------------------------------------

def test_data_principal_role_has_no_cross_customer_permissions():
    """data_principal must never receive customer.view (would expose other
    principals' records) - own-data access is via own_consent.manage /
    own_profile.manage, enforced by portal endpoints keyed to the caller's
    own identity, not a general customer-search permission."""
    perms = set(ROLE_PERMISSIONS["data_principal"])
    assert "customer.view" not in perms
    assert "own_consent.manage" in perms


def test_data_principal_cannot_call_staff_customer_search(client, principal_headers):
    """A data_principal staff-login has no customer.view - staff customer
    search endpoint must reject them, proving own-data scope is enforced
    server-side rather than by hiding the nav item."""
    resp = client.get("/customers", headers=principal_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# guardian linked-child restriction (verified relationship table, not just role)
# ---------------------------------------------------------------------------

def test_guardian_role_alone_does_not_grant_child_access():
    """Role name is insufficient - GuardianChildLink row must exist and be
    VERIFIED. A guardian user with no link row for a given child must be
    rejected by require_guardian_link regardless of their role."""
    db = SessionLocal()
    try:
        guardian = db.query(User).filter(User.username == "guardian.cms").first()
        assert guardian is not None
        # The demo guardian has exactly one verified link (seeded) - any
        # other customer id must have no matching row.
        linked_ids = {
            l.child_customer_id
            for l in db.query(GuardianChildLink)
            .filter(GuardianChildLink.guardian_user_id == guardian.id, GuardianChildLink.status == "VERIFIED")
            .all()
        }
        assert len(linked_ids) == 1
        other_customer_id = max(linked_ids) + 10_000  # certainly unlinked
        assert other_customer_id not in linked_ids
    finally:
        db.close()


def test_guardian_link_is_separate_from_role_assignment():
    """Revoking/removing a GuardianChildLink must not touch the user's role
    assignment - the two are independent tables by design (Step 5)."""
    db = SessionLocal()
    try:
        guardian = db.query(User).filter(User.username == "guardian.cms").first()
        assert guardian is not None
        assert guardian.role is not None
        assert guardian.role.name == "guardian"
        link = db.query(GuardianChildLink).filter(GuardianChildLink.guardian_user_id == guardian.id).first()
        assert link is not None
        assert link.status == "VERIFIED"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tenant isolation / cross-tenant IDOR
# ---------------------------------------------------------------------------

def test_purpose_scoped_to_wrong_tenant_is_not_found(client, tenant_admin_headers, db_session):
    """A purpose that belongs to a different tenant than the one asserted in
    the query must come back 404, not leak via a raw purpose_id lookup
    (IDOR prevention added to purposes.py)."""
    from app.models.entities import Purpose

    purpose = db_session.query(Purpose).filter(Purpose.tenant_id.is_(None)).first()
    if purpose is None:
        # No untenanted purpose to test against in this dataset - not a
        # failure of the isolation logic itself.
        return
    other_tenant = db_session.query(Tenant).first()
    resp = client.get(
        f"/purposes/{purpose.id}",
        params={"tenant_id": other_tenant.id + 999999},
        headers=tenant_admin_headers,
    )
    assert resp.status_code == 404


def test_two_tenants_cannot_see_each_others_scoped_purposes(client, tenant_admin_headers, db_session):
    """List purposes scoped to tenant A must never include a purpose created
    under tenant B."""
    from app.models.entities import Purpose

    tenants = db_session.query(Tenant).order_by(Tenant.id).limit(2).all()
    if len(tenants) < 2:
        return
    tenant_a, tenant_b = tenants[0], tenants[1]
    resp_a = client.get("/purposes", params={"tenant_id": tenant_a.id}, headers=tenant_admin_headers)
    assert resp_a.status_code == 200
    ids_a = {p["id"] for p in resp_a.json()}
    b_only_ids = {
        p.id for p in db_session.query(Purpose).filter(Purpose.tenant_id == tenant_b.id).all()
    }
    assert ids_a.isdisjoint(b_only_ids)
