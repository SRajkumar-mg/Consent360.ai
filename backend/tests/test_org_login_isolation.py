"""Regression tests for the org-login privilege-escalation fix.

`organization_users.id` and `users.id` are independent, colliding sequences.
`/auth/login` used to have a branch that, on successful `OrganizationUser`
authentication, minted a token with the *staff* `ctx`/`type`
(`ctx="consent-auth"`, `type="access"`) carrying `sub=str(org_user.id)`.
`get_current_user` (app/api/deps.py) accepted that context and resolved the
subject with `db.get(User, user_id)` against the *staff* `users` table, so an
organization user whose id happened to match a staff user's id could
authenticate as that staff user — e.g. `organization_users` id 1 (a
single-tenant org admin) acting as `users` id 1 (the platform administrator)
across every tenant.

The fix:
  * The org branch was removed from `/auth/login` entirely. Organization
    users authenticate exclusively through `POST /organizations/auth/login`,
    which mints a structurally distinct `ctx="org-auth"` / `type="org-access"`
    token.
  * `get_current_user`, `/auth/refresh` and `/auth/me` now also require an
    explicit `sub_type == STAFF_SUBJECT_TYPE` claim before resolving `sub`
    against the `users` table, so a token minted with the wrong context in
    the future fails closed instead of silently resolving to whichever staff
    row shares that id. `_require_org_user` mirrors this with `sub_type ==
    ORG_SUBJECT_TYPE` for the `organization_users` table.

These tests reproduce the exact id-collision setup that made the original
bug exploitable and pin all three guarantees: a colliding org token is
refused by staff endpoints, the intended org-login path still works end to
end, and the refresh endpoint cannot be used to launder an org-issued token
into a staff one.
"""
from datetime import datetime, timedelta, timezone

from jose import jwt
from sqlalchemy import func, text

from app.core.config import get_settings
from app.core.encryption import hmac_digest
from app.core.rbac import ALL_PERMISSIONS
from app.core.security import create_access_token, create_refresh_token, hash_password
from app.models.entities import Organization, OrganizationUser, Role, User

STAFF_PASSWORD = "Staff@1234"
ORG_PASSWORD = "OrgUser@1234"


def _next_shared_id(db) -> int:
    """An id not yet used in either `users` or `organization_users`, so we
    can deliberately create a same-id row in both tables — the exact
    precondition (`organization_users` id N == `users` id N) that made the
    original bug exploitable — regardless of what earlier tests in this
    session's shared database have already inserted."""
    max_user_id = db.query(func.max(User.id)).scalar() or 0
    max_org_user_id = db.query(func.max(OrganizationUser.id)).scalar() or 0
    return max(max_user_id, max_org_user_id) + 1


def _bump_sequence(db, table: str) -> None:
    """Explicitly inserting a chosen id (below) bypasses the table's identity
    sequence, so later autoincrement inserts in *other* tests could collide
    with it. Re-sync the sequence to the current max id so every other test
    in this shared session-scoped database keeps getting fresh ids."""
    db.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"
    ))
    db.commit()


def _make_colliding_pair(db):
    shared_id = _next_shared_id(db)

    role = db.query(Role).filter(Role.name == "admin").first()
    if not role:
        role = Role(name="admin", description="Full access", permissions=ALL_PERMISSIONS, is_system=True)
        db.add(role)
        db.flush()

    staff = User(
        id=shared_id,
        username=f"staff-collide-{shared_id}",
        full_name="Platform Admin",
        email=f"staff-collide-{shared_id}@example.com",
        email_search=hmac_digest(f"staff-collide-{shared_id}@example.com"),
        password_hash=hash_password(STAFF_PASSWORD),
        role_id=role.id,
        is_active=True,
    )
    db.add(staff)

    org = Organization(name="JobHub Collision Test", code=f"JOBHUB_COLLIDE_{shared_id}", is_active=True)
    db.add(org)
    db.flush()

    org_user = OrganizationUser(
        id=shared_id,
        organization_id=org.id,
        username=f"org-collide-{shared_id}",
        full_name="JobHub Admin",
        email=f"org-collide-{shared_id}@example.com",
        email_search=hmac_digest(f"org-collide-{shared_id}@example.com"),
        password_hash=hash_password(ORG_PASSWORD),
        role="jobhub_admin",
        is_active=True,
    )
    db.add(org_user)
    db.commit()
    db.refresh(staff)
    db.refresh(org_user)
    db.refresh(org)
    _bump_sequence(db, "users")
    _bump_sequence(db, "organization_users")

    # Sanity check on the exploit precondition itself, so a future change to
    # id-assignment doesn't silently turn these tests into no-ops.
    assert staff.id == org_user.id
    return staff, org_user, org


def test_org_user_token_rejected_by_staff_endpoints(db, client):
    staff, org_user, _org = _make_colliding_pair(db)

    login = client.post(
        "/organizations/auth/login",
        json={"username": org_user.username, "password": ORG_PASSWORD},
    )
    assert login.status_code == 200
    org_token = login.json()["access_token"]

    claims = jwt.get_unverified_claims(org_token)
    assert claims["sub"] == str(org_user.id) == str(staff.id)
    assert claims["ctx"] == "org-auth"

    headers = {"Authorization": f"Bearer {org_token}"}

    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 401

    users = client.get("/auth/users", headers=headers)
    assert users.status_code == 401
    # Never resolves to the staff row's data even indirectly.
    assert "admin" not in (users.text or "").lower() or users.status_code != 200

    # The historically-vulnerable path itself: logging in through the staff
    # endpoint with organization-user credentials must not succeed.
    staff_login = client.post(
        "/auth/login",
        json={"username": org_user.username, "password": ORG_PASSWORD},
    )
    assert staff_login.status_code == 401


def test_org_user_authenticates_via_org_login_and_reaches_org_scoped_route(db, client):
    staff, org_user, org = _make_colliding_pair(db)

    login = client.post(
        "/organizations/auth/login",
        json={"username": org_user.username, "password": ORG_PASSWORD},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["user"]["username"] == org_user.username
    assert body["organization"]["id"] == org.id

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    dashboard = client.get("/organizations/portal/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["organization"]["id"] == org.id

    # And the reverse must also hold: a staff token (even for the colliding
    # id) must not be accepted by the org-scoped route.
    staff_headers = {"Authorization": f"Bearer {create_access_token(staff.id, staff.username, 'admin')}"}
    staff_on_org_route = client.get("/organizations/portal/dashboard", headers=staff_headers)
    assert staff_on_org_route.status_code == 401


def test_refresh_cannot_launder_org_context_into_staff_token(db, client):
    staff, org_user, _org = _make_colliding_pair(db)
    settings = get_settings()

    # Reconstructs exactly what the pre-fix `/auth/login` org branch used to
    # mint: a refresh token with the staff ctx/type for organization_users.id,
    # missing the new sub_type claim.
    forged_legacy_refresh = jwt.encode(
        {
            "sub": str(org_user.id),
            "type": "refresh",
            "ctx": "consent-auth",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iat": datetime.now(timezone.utc),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    resp = client.post("/auth/refresh", json={"refresh_token": forged_legacy_refresh})
    assert resp.status_code == 401

    # A genuine org-access token must not be accepted by the staff refresh
    # endpoint either (wrong ctx/type entirely).
    org_login = client.post(
        "/organizations/auth/login",
        json={"username": org_user.username, "password": ORG_PASSWORD},
    )
    org_access_token = org_login.json()["access_token"]
    resp2 = client.post("/auth/refresh", json={"refresh_token": org_access_token})
    assert resp2.status_code == 401

    # Control: a real staff refresh token still refreshes into a staff token.
    real_refresh = create_refresh_token(staff.id)
    resp3 = client.post("/auth/refresh", json={"refresh_token": real_refresh})
    assert resp3.status_code == 200
    assert resp3.json()["user"]["username"] == staff.username
