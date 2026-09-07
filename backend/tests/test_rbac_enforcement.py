"""R3-12: RBAC enforcement tests.

tests/test_auth_hardening.py already covers role CRUD, that system roles
cannot be edited/deleted, and the STATIC shape of the seeded roles (e.g.
"auditor has no *.manage permission in its permission list"). What was
missing - and what this file adds - is a direct test of the actual
enforcement mechanism every permission-gated route depends on
(app/api/deps.py::require_permission / role_has_permission / get_org_scope):
does a user whose role's permission list does not contain the required
permission actually get denied; does the "*" wildcard (only ever used by
the seeded 'admin' role) actually grant everything; does a user with no
role, or an empty permission list, fail closed rather than raising an
unrelated error or silently allowing; and, end to end through a real route,
does a role that legitimately lacks a permission (per rbac.py) actually get
a 403 from the live dependency-injected endpoint, not just in a unit test
of the checker function in isolation.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.deps import get_org_scope, require_permission, role_has_permission
from app.core.rbac import (
    ALL_PERMISSIONS,
    ORG_SCOPE_MAP,
    PERM_CATEGORY_MANAGE,
    PERM_CONSENT_MANAGE,
    PERM_CONSENT_VIEW,
    PERM_USER_MANAGE,
    ROLE_PERMISSIONS,
)
from app.core.security import create_access_token, hash_password
from app.models.entities import Role, User


def _fake_user(permissions, role_name="custom"):
    role = SimpleNamespace(name=role_name, permissions=permissions)
    return SimpleNamespace(role=role)


# --------------------------------------------------------------------------- #
# require_permission - unit level, direct calls to the dependency checker
# --------------------------------------------------------------------------- #

def test_require_permission_denies_a_user_whose_role_lacks_it():
    checker = require_permission(PERM_CONSENT_MANAGE)
    user = _fake_user([PERM_CONSENT_VIEW])
    with pytest.raises(HTTPException) as exc:
        checker(user=user)
    assert exc.value.status_code == 403
    assert PERM_CONSENT_MANAGE in exc.value.detail


def test_require_permission_allows_a_user_whose_role_has_it():
    checker = require_permission(PERM_CONSENT_MANAGE)
    user = _fake_user([PERM_CONSENT_VIEW, PERM_CONSENT_MANAGE])
    assert checker(user=user) is user


def test_require_permission_wildcard_grants_every_known_permission():
    user = _fake_user(["*"], role_name="admin")
    for perm in ALL_PERMISSIONS:
        assert require_permission(perm)(user=user) is user


def test_require_permission_denies_by_default_when_role_is_none():
    """A user whose role failed to resolve (e.g. a dangling role_id) must be
    denied, never silently allowed and never raise an unrelated AttributeError."""
    checker = require_permission(PERM_CONSENT_VIEW)
    user = SimpleNamespace(role=None)
    with pytest.raises(HTTPException) as exc:
        checker(user=user)
    assert exc.value.status_code == 403


def test_require_permission_denies_with_an_empty_permission_list():
    checker = require_permission(PERM_CONSENT_VIEW)
    user = _fake_user([])
    with pytest.raises(HTTPException) as exc:
        checker(user=user)
    assert exc.value.status_code == 403


def test_require_permission_is_exact_match_not_a_prefix_match():
    """'consent.manage' must not be satisfied by some unrelated permission
    that merely shares a prefix or suffix - only an exact string match (or
    the literal '*' wildcard) grants access."""
    checker = require_permission(PERM_CONSENT_MANAGE)
    user = _fake_user(["consent.manage.extra", "consent", "manage"])
    with pytest.raises(HTTPException):
        checker(user=user)


# --------------------------------------------------------------------------- #
# role_has_permission
# --------------------------------------------------------------------------- #

def test_role_has_permission_matches_require_permissions_own_logic():
    role = SimpleNamespace(permissions=[PERM_CONSENT_VIEW])
    assert role_has_permission(role, PERM_CONSENT_VIEW) is True
    assert role_has_permission(role, PERM_CONSENT_MANAGE) is False
    wildcard_role = SimpleNamespace(permissions=["*"])
    assert role_has_permission(wildcard_role, PERM_USER_MANAGE) is True


def test_role_has_permission_treats_none_permissions_as_empty():
    role = SimpleNamespace(permissions=None)
    assert role_has_permission(role, PERM_CONSENT_VIEW) is False


# --------------------------------------------------------------------------- #
# get_org_scope
# --------------------------------------------------------------------------- #

def test_get_org_scope_maps_every_org_scoped_role_to_its_source_app():
    assert ORG_SCOPE_MAP  # sanity: the map itself is non-empty
    for role_name, expected_scope in ORG_SCOPE_MAP.items():
        user = _fake_user([], role_name=role_name)
        assert get_org_scope(user) == expected_scope


def test_get_org_scope_is_none_for_a_full_access_role():
    user = _fake_user(ALL_PERMISSIONS, role_name="admin")
    assert get_org_scope(user) is None


def test_get_org_scope_is_none_when_role_is_missing():
    assert get_org_scope(SimpleNamespace(role=None)) is None


# --------------------------------------------------------------------------- #
# rbac.py's static role table: every declared permission is a real one
# --------------------------------------------------------------------------- #

def test_every_permission_listed_for_a_role_is_a_known_permission():
    known = set(ALL_PERMISSIONS) | {"*"}
    for role_name, perms in ROLE_PERMISSIONS.items():
        unknown = set(perms) - known
        assert not unknown, f"role '{role_name}' lists unknown permission(s): {unknown}"


def test_only_admin_holds_the_wildcard_permission():
    for role_name, perms in ROLE_PERMISSIONS.items():
        if role_name == "admin":
            continue
        assert "*" not in perms, f"role '{role_name}' unexpectedly holds the '*' wildcard"


# --------------------------------------------------------------------------- #
# End to end: a real, seeded role hitting a real, permission-gated route
# --------------------------------------------------------------------------- #

def _make_role_and_token(db, role_name: str, username: str) -> str:
    role = db.query(Role).filter(Role.name == role_name).first()
    assert role is not None, f"expected '{role_name}' to already be seeded by app startup (rbac.sync_roles)"
    from app.core.encryption import hmac_digest

    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(
            username=username, full_name=username, email=f"{username}@example.com",
            email_search=hmac_digest(f"{username}@example.com"),
            password_hash=hash_password("Str0ng!Passw0rd"), role_id=role.id, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return create_access_token(user.id, user.username, role.name)


def test_auditor_role_gets_a_real_403_from_a_live_manage_endpoint(db, client):
    """rbac.py declares 'auditor' with no *.manage permission at all (see
    test_auth_hardening.py::test_auditor_role_is_strictly_read_only for the
    static check). This closes the loop: hitting an ACTUAL PERM_CATEGORY_MANAGE
    -gated route (POST /data-categories) with a live auditor token must be
    refused by the real dependency-injected app, not just by a unit test of
    the checker function in isolation. The body is deliberately empty/invalid
    so this also confirms permission denial happens before (or regardless of)
    request-body validation - a 422 here would be the wrong failure mode."""
    token = _make_role_and_token(db, "auditor", "rbac-test-auditor")
    resp = client.post(
        "/data-categories", headers={"Authorization": f"Bearer {token}"}, json={},
    )
    assert resp.status_code == 403
    assert PERM_CATEGORY_MANAGE in resp.json()["detail"]


def test_auditor_role_can_still_read_the_same_resource_family(db, client):
    """The flip side of the above: auditor DOES hold purpose.view, which
    gates GET /data-categories - denial above is permission-specific, not a
    blanket lockout of the whole route prefix."""
    token = _make_role_and_token(db, "auditor", "rbac-test-auditor-2")
    resp = client.get("/data-categories", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_viewer_role_is_denied_user_management(db, client):
    """'viewer' holds no user.manage permission (rbac.py) - GET /auth/users
    must refuse it via the real app, matching PERM_USER_MANAGE's contract."""
    token = _make_role_and_token(db, "viewer", "rbac-test-viewer")
    resp = client.get("/auth/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert PERM_USER_MANAGE in resp.json()["detail"]


def test_admin_role_is_not_denied_user_management(db, client, staff_token):
    resp = client.get("/auth/users", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200
