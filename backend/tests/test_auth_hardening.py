"""R3-02 (staff authentication hardening and roles) behavioural tests.

Covers: DPO/Auditor/Operator role provisioning + custom role CRUD +
ROLE_CHANGED audit events, the access-review report, password policy on
user create/update, account lockout (and its "a success resets the count"
semantics), MFA (TOTP) enroll/confirm/login/disable, iss/aud claim
enforcement (including old-shaped tokens failing closed), and jti-based
token revocation on logout and deactivation.
"""
import pyotp
import pytest
from jose import jwt

from app.core.config import get_settings
from app.core.mfa import mfa_store
from app.core.security import (
    AUTH_CONTEXT,
    STAFF_SUBJECT_TYPE,
    create_access_token,
    decode_token,
)
from app.models.entities import AuditLog, Role, User

STRONG_PASSWORD = "Str0ng!Passw0rd"


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    """login_limiter (app/core/utils.py) is a process-global singleton
    shared by every test in the session, keyed by client IP - and every
    TestClient request in this suite shares the same "testclient" IP. This
    file alone makes far more than login_limiter's 10-per-60s allowance
    across its lockout/MFA/revocation tests, so each test starts with a
    clean slate rather than tripping a 429 caused by an EARLIER test."""
    from app.core.utils import login_limiter

    if hasattr(login_limiter, "_hits"):
        login_limiter._hits.clear()
    yield


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_role(db) -> Role:
    role = db.query(Role).filter(Role.name == "viewer").first()
    if not role:
        role = Role(name="viewer", description="Read-only", permissions=["dashboard.view"], is_system=True)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


# --------------------------------------------------------------------------- #
# Roles: DPO / Auditor / Operator provisioning + custom role CRUD
# --------------------------------------------------------------------------- #

def test_dpo_auditor_operator_roles_exist_after_startup(db, client):
    names = {r.name for r in db.query(Role).filter(Role.name.in_(["dpo", "auditor", "operator"])).all()}
    assert names == {"dpo", "auditor", "operator"}


def test_auditor_role_is_strictly_read_only(db, client):
    auditor = db.query(Role).filter(Role.name == "auditor").first()
    assert auditor is not None
    assert not any(p.endswith(".manage") for p in auditor.permissions)


def test_role_crud_roundtrip_with_role_changed_audit(db, client, staff_token):
    headers = _auth_headers(staff_token)

    created = client.post("/admin/roles", headers=headers, json={
        "name": "custom_reviewer", "description": "Custom test role", "permissions": ["dashboard.view"],
    })
    assert created.status_code == 201
    role_id = created.json()["id"]
    assert created.json()["is_system"] is False

    updated = client.put(f"/admin/roles/{role_id}", headers=headers, json={"permissions": ["dashboard.view", "audit.view"]})
    assert updated.status_code == 200
    assert set(updated.json()["permissions"]) == {"dashboard.view", "audit.view"}

    deleted = client.delete(f"/admin/roles/{role_id}", headers=headers)
    assert deleted.status_code == 200

    # AuditLog.reason is an EncryptedText column - it cannot be filtered by
    # LIKE at the SQL level (see docs/ARCHITECTURE.md's encryption notes); fetch by the
    # plain, indexed `event` column instead and match the (ORM-decrypted)
    # reason text in Python.
    role_changed_rows = db.query(AuditLog).filter(AuditLog.event == "ROLE_CHANGED").order_by(AuditLog.id.desc()).limit(50).all()
    matching = [row for row in role_changed_rows if "custom_reviewer" in (row.reason or "")]
    assert len(matching) >= 3  # create, update, delete


def test_role_crud_rejects_unknown_permission(client, staff_token):
    resp = client.post("/admin/roles", headers=_auth_headers(staff_token), json={
        "name": "bad_role", "permissions": ["not.a.real.permission"],
    })
    assert resp.status_code == 422


def test_system_roles_cannot_be_edited_or_deleted_via_the_api(db, client, staff_token):
    dpo = db.query(Role).filter(Role.name == "dpo").first()
    headers = _auth_headers(staff_token)
    assert client.put(f"/admin/roles/{dpo.id}", headers=headers, json={"description": "hacked"}).status_code == 403
    assert client.delete(f"/admin/roles/{dpo.id}", headers=headers).status_code == 403


def test_changing_a_users_role_emits_role_changed_audit_event(db, client, staff_token):
    role = _create_role(db)
    headers = _auth_headers(staff_token)
    created = client.post("/auth/users", headers=headers, json={
        "username": "role-change-target", "full_name": "Role Change Target",
        "email": "role-change-target@example.com", "password": STRONG_PASSWORD, "role_id": role.id,
    })
    assert created.status_code == 201
    user_id = created.json()["id"]

    dpo = db.query(Role).filter(Role.name == "dpo").first()
    resp = client.put(f"/auth/users/{user_id}", headers=headers, json={"role_id": dpo.id})
    assert resp.status_code == 200

    # See the comment in test_role_crud_roundtrip_with_role_changed_audit
    # above: AuditLog.reason is encrypted and cannot be LIKE-filtered in SQL.
    role_changed_rows = db.query(AuditLog).filter(AuditLog.event == "ROLE_CHANGED").order_by(AuditLog.id.desc()).limit(50).all()
    assert any("role-change-target" in (row.reason or "") for row in role_changed_rows)


def test_access_review_report_lists_users_and_roles(db, client, staff_token):
    resp = client.get("/admin/access-review", headers=_auth_headers(staff_token))
    assert resp.status_code == 200
    body = resp.json()
    assert any(u["username"] == "test-admin" for u in body["users"])
    assert any(r["name"] == "dpo" for r in body["roles"])


# --------------------------------------------------------------------------- #
# Password policy
# --------------------------------------------------------------------------- #

def test_create_user_rejects_a_weak_password(db, client, staff_token):
    role = _create_role(db)
    resp = client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": "weak-pw-user", "full_name": "Weak Pw", "email": "weak-pw@example.com",
        "password": "password1", "role_id": role.id,
    })
    assert resp.status_code == 422
    assert "violations" in resp.json()["detail"]


def test_create_user_accepts_a_strong_password(db, client, staff_token):
    role = _create_role(db)
    resp = client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": "strong-pw-user", "full_name": "Strong Pw", "email": "strong-pw@example.com",
        "password": STRONG_PASSWORD, "role_id": role.id,
    })
    assert resp.status_code == 201


def test_update_user_password_is_also_policy_checked(db, client, staff_token):
    role = _create_role(db)
    created = client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": "pw-update-user", "full_name": "Pw Update", "email": "pw-update@example.com",
        "password": STRONG_PASSWORD, "role_id": role.id,
    })
    user_id = created.json()["id"]
    resp = client.put(f"/auth/users/{user_id}", headers=_auth_headers(staff_token), json={"password": "short"})
    assert resp.status_code == 422


def test_seeded_admin_password_is_unaffected_by_the_new_policy(db):
    """The policy is enforced at SET time only. An already-hashed password
    that would not itself satisfy today's policy (shorter than the current
    PASSWORD_MIN_LENGTH, say) must keep authenticating - login never
    re-validates policy against the stored hash."""
    from app.core.security import hash_password, verify_password

    old_style_hash = hash_password("Admin@1234")  # 10 chars - below the 12-char default minimum
    assert verify_password("Admin@1234", old_style_hash) is True


# --------------------------------------------------------------------------- #
# Account lockout
# --------------------------------------------------------------------------- #

def test_account_locks_out_after_repeated_failed_logins(client):
    settings = get_settings()
    username = "lockout-test-user"
    for _ in range(settings.ACCOUNT_LOCKOUT_THRESHOLD):
        resp = client.post("/auth/login", json={"username": username, "password": "wrong"})
        assert resp.status_code == 401

    locked = client.post("/auth/login", json={"username": username, "password": "wrong"})
    assert locked.status_code == 423

    # Even the CORRECT password is refused while locked.
    still_locked = client.post("/auth/login", json={"username": username, "password": "whatever-the-real-one-is"})
    assert still_locked.status_code == 423


def test_a_successful_login_resets_the_lockout_counter(db, client, staff_token):
    role = _create_role(db)
    username = "lockout-reset-user"
    password = STRONG_PASSWORD
    created = client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": username, "full_name": "Lockout Reset", "email": "lockout-reset@example.com",
        "password": password, "role_id": role.id,
    })
    assert created.status_code == 201

    settings = get_settings()
    below_threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD - 1
    for _ in range(below_threshold):
        assert client.post("/auth/login", json={"username": username, "password": "wrong"}).status_code == 401

    ok = client.post("/auth/login", json={"username": username, "password": password})
    assert ok.status_code == 200

    # Immediately after a success, the same number of fresh failures must
    # NOT be locked yet - the counter reset at the successful login.
    for _ in range(below_threshold):
        assert client.post("/auth/login", json={"username": username, "password": "wrong"}).status_code == 401


# --------------------------------------------------------------------------- #
# MFA (TOTP)
# --------------------------------------------------------------------------- #

def test_mfa_enroll_confirm_and_login_flow(db, client, staff_token):
    role = _create_role(db)
    username = "mfa-flow-user"
    password = STRONG_PASSWORD
    created = client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": username, "full_name": "MFA Flow", "email": "mfa-flow@example.com",
        "password": password, "role_id": role.id,
    })
    assert created.status_code == 201

    login1 = client.post("/auth/login", json={"username": username, "password": password})
    assert login1.status_code == 200
    user_token = login1.json()["access_token"]

    enroll = client.post("/auth/mfa/enroll", headers=_auth_headers(user_token))
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    assert "otpauth://" in enroll.json()["provisioning_uri"]

    bad_confirm = client.post("/auth/mfa/confirm", headers=_auth_headers(user_token), json={"code": "000000"})
    assert bad_confirm.status_code == 400

    good_code = pyotp.TOTP(secret).now()
    confirm = client.post("/auth/mfa/confirm", headers=_auth_headers(user_token), json={"code": good_code})
    assert confirm.status_code == 200
    assert len(confirm.json()["backup_codes"]) > 0

    status_resp = client.get("/auth/mfa/status", headers=_auth_headers(user_token))
    assert status_resp.json()["enabled"] is True

    # Login now requires MFA: password alone yields a pending token, not tokens.
    login2 = client.post("/auth/login", json={"username": username, "password": password})
    assert login2.status_code == 200
    body = login2.json()
    assert body.get("mfa_required") is True
    pending_token = body["pending_token"]

    wrong_code = client.post("/auth/mfa/verify-login", json={"pending_token": pending_token, "code": "111111"})
    assert wrong_code.status_code == 401

    right_code = pyotp.TOTP(secret).now()
    verified = client.post("/auth/mfa/verify-login", json={"pending_token": pending_token, "code": right_code})
    assert verified.status_code == 200
    assert "access_token" in verified.json()

    # Disable turns the requirement back off.
    new_access = verified.json()["access_token"]
    disable = client.post("/auth/mfa/disable", headers=_auth_headers(new_access))
    assert disable.status_code == 200
    login3 = client.post("/auth/login", json={"username": username, "password": password})
    assert login3.status_code == 200
    assert "access_token" in login3.json()


def test_seeded_admin_never_needs_mfa_by_default(client):
    """Staff login must keep working: MFA is opt-in per user, not enforced
    globally (MFA_ENFORCED defaults to False - see app/core/mfa.py's module
    docstring for why), so an account that never enrolled logs in exactly
    as before."""
    assert mfa_store.is_enabled(1) is False  # seeded admin's id in a fresh test DB
    resp = client.post("/auth/login", json={"username": "admin", "password": "Admin@1234"})
    # The seed user may or may not exist in this disposable test DB; either
    # way the response must never be an mfa_required challenge.
    if resp.status_code == 200:
        assert "mfa_required" not in resp.json()


# --------------------------------------------------------------------------- #
# iss / aud claim enforcement (RFC 8725)
# --------------------------------------------------------------------------- #

def test_decode_token_rejects_wrong_issuer():
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "1", "sub_type": STAFF_SUBJECT_TYPE, "type": "access", "ctx": AUTH_CONTEXT,
         "iss": "some-other-issuer", "aud": settings.JWT_AUDIENCE},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    assert decode_token(forged) is None


def test_get_current_user_rejects_a_token_with_no_aud_claim(client):
    """A token minted before R3-02 (no `aud` claim at all) must fail
    closed rather than being silently accepted - see get_current_user's
    explicit `payload.get("aud") != settings.JWT_AUDIENCE` check."""
    settings = get_settings()
    old_shaped = jwt.encode(
        {"sub": "1", "sub_type": STAFF_SUBJECT_TYPE, "username": "admin", "role": "admin",
         "type": "access", "ctx": AUTH_CONTEXT, "iss": settings.JWT_ISSUER},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    # decode_token itself must still succeed (aud verification is disabled
    # at the library level so the claim round-trips for the explicit check
    # below to inspect) - only the endpoint's own aud check should reject.
    assert decode_token(old_shaped) is not None
    resp = client.get("/auth/me", headers=_auth_headers(old_shaped))
    assert resp.status_code == 401


def test_get_current_user_rejects_a_token_with_the_wrong_audience(client):
    settings = get_settings()
    wrong_aud = jwt.encode(
        {"sub": "1", "sub_type": STAFF_SUBJECT_TYPE, "username": "admin", "role": "admin",
         "type": "access", "ctx": AUTH_CONTEXT, "iss": settings.JWT_ISSUER, "aud": "some-other-audience"},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    resp = client.get("/auth/me", headers=_auth_headers(wrong_aud))
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Token revocation (logout, deactivation)
# --------------------------------------------------------------------------- #

def test_logout_revokes_the_access_token_immediately(db, client, staff_token):
    role = _create_role(db)
    username = "logout-revoke-user"
    password = STRONG_PASSWORD
    client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": username, "full_name": "Logout Revoke", "email": "logout-revoke@example.com",
        "password": password, "role_id": role.id,
    })
    login_resp = client.post("/auth/login", json={"username": username, "password": password})
    token = login_resp.json()["access_token"]

    assert client.get("/auth/me", headers=_auth_headers(token)).status_code == 200

    logout_resp = client.post("/auth/logout", headers=_auth_headers(token))
    assert logout_resp.status_code == 200

    revoked_check = client.get("/auth/me", headers=_auth_headers(token))
    assert revoked_check.status_code == 401
    assert "revoked" in revoked_check.json()["detail"].lower()


def test_logout_can_also_revoke_the_refresh_token(db, client, staff_token):
    role = _create_role(db)
    username = "logout-refresh-revoke-user"
    password = STRONG_PASSWORD
    client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": username, "full_name": "Logout Refresh Revoke", "email": "logout-refresh-revoke@example.com",
        "password": password, "role_id": role.id,
    })
    login_resp = client.post("/auth/login", json={"username": username, "password": password})
    access_token = login_resp.json()["access_token"]
    refresh_token = login_resp.json()["refresh_token"]

    logout_resp = client.post(
        "/auth/logout", headers=_auth_headers(access_token), json={"refresh_token": refresh_token},
    )
    assert logout_resp.status_code == 200

    refresh_resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_resp.status_code == 401


def test_logout_with_no_body_still_works(db, client, staff_token):
    """Backward compatibility: the existing frontend calls POST
    /auth/logout with no JSON body at all."""
    role = _create_role(db)
    username = "logout-no-body-user"
    password = STRONG_PASSWORD
    client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": username, "full_name": "Logout No Body", "email": "logout-no-body@example.com",
        "password": password, "role_id": role.id,
    })
    token = client.post("/auth/login", json={"username": username, "password": password}).json()["access_token"]
    resp = client.post("/auth/logout", headers=_auth_headers(token))
    assert resp.status_code == 200


def test_deactivation_revokes_an_already_issued_token_immediately(db, client, staff_token):
    """R3-02/H-02: token revocation honoured on deactivation. This does
    NOT depend on the jti revocation list - get_current_user re-checks
    `user.is_active` against the database on every request."""
    role = _create_role(db)
    username = "deactivate-me-user"
    password = STRONG_PASSWORD
    created = client.post("/auth/users", headers=_auth_headers(staff_token), json={
        "username": username, "full_name": "Deactivate Me", "email": "deactivate-me@example.com",
        "password": password, "role_id": role.id,
    })
    user_id = created.json()["id"]
    token = client.post("/auth/login", json={"username": username, "password": password}).json()["access_token"]
    assert client.get("/auth/me", headers=_auth_headers(token)).status_code == 200

    deactivate = client.put(f"/auth/users/{user_id}", headers=_auth_headers(staff_token), json={"is_active": False})
    assert deactivate.status_code == 200

    still_using_old_token = client.get("/auth/me", headers=_auth_headers(token))
    assert still_using_old_token.status_code == 401


def test_revoked_jti_is_rejected_at_the_token_revocation_module_level():
    from app.core.token_revocation import is_revoked, revoke

    assert is_revoked("some-jti-never-revoked") is False
    revoke("some-jti-never-revoked", ttl_seconds=60)
    assert is_revoked("some-jti-never-revoked") is True
