import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user, require_permission
from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import find_by_search_digest, hmac_digest
from app.core.mfa import mfa_store, provisioning_uri
from app.core.rbac import PERM_USER_MANAGE
from app.core.security import (
    AUTH_CONTEXT,
    MFA_PENDING_CONTEXT,
    STAFF_SUBJECT_TYPE,
    create_access_token,
    create_mfa_pending_token,
    create_refresh_token,
    decode_token,
    failed_logins_since_last_success,
    hash_password,
    login_failure_delay_seconds,
    validate_password_strength,
    verify_password,
)
from app.core.utils import login_limiter, mask_identifier
from app.models.entities import User

from app.schemas.schemas import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserListOut,
    UserOut,
    UserUpdate,
)
from app.services.audit import log_audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=mask_identifier(user.email),
        role_id=user.role_id,
        role_name=user.role.name if user.role else "",
        role_permissions=(user.role.permissions if user.role else []),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _password_policy_error(problems: list[str]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"message": "Password does not meet the security policy", "violations": problems},
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(f"login:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts, please try again later")

    settings = get_settings()
    # R3-02/H-02 account lockout, scoped to (username, source IP).
    #
    # The trade-off, stated plainly. A lockout counted per USERNAME defends
    # against online password guessing but hands anyone who knows a
    # username an unauthenticated denial-of-service: five wrong passwords
    # locked the real account holder out for fifteen minutes, and the
    # review confirmed the sixth attempt WITH THE CORRECT PASSWORD returned
    # 423. The per-IP login_limiter (10/min) does not meaningfully raise
    # that bar at five requests. Both properties are real; the question is
    # only which one to buy with a hard block.
    #
    # The position taken here: a hard block is only ever applied to the
    # (username, IP) pair that produced the failures. An attacker can
    # therefore lock out only themselves, while the account holder logging
    # in from their own address is unaffected - the DoS is gone. Continued
    # guessing from a single origin still stops dead after
    # ACCOUNT_LOCKOUT_THRESHOLD attempts, which is the case the lockout was
    # actually for.
    #
    # The username-wide signal is NOT discarded, it is downgraded from a
    # block to a cost: `login_failure_delay_seconds` below adds a doubling
    # delay to each further FAILED attempt on a username regardless of
    # origin, so a distributed guesser pays wall-clock time per try while a
    # correct password still returns immediately.
    #
    # Residual risk, accepted and recorded: an attacker spreading attempts
    # across many source addresses gets ACCOUNT_LOCKOUT_THRESHOLD attempts
    # per address before that address is blocked, so distributed credential
    # stuffing is slowed (per-IP rate limit x per-attempt delay) and
    # alerted (R3-04's REPEATED_FAILED_LOGINS still counts per username
    # across every origin - see check_failed_logins below) rather than
    # blocked outright. Blocking it needs a control this codebase does not
    # have: a CAPTCHA/proof-of-work step on the login form or an IP
    # reputation feed. `client_ip` is also only as trustworthy as the
    # reverse proxy in front of the app, so a deployment that does not set
    # a trusted proxy header correctly weakens the per-IP scoping (never
    # the delay, which is origin-independent).
    #
    # Both counts are backed by the same append-only audit trail as
    # R3-04's failed-login alert rather than a new counter column - see
    # app.core.security.failed_logins_since_last_success.
    if failed_logins_since_last_success(
        db, payload.username, window_minutes=settings.ACCOUNT_LOCKOUT_WINDOW_MINUTES,
        client_ip=client_ip,
    ) >= settings.ACCOUNT_LOCKOUT_THRESHOLD:
        log_audit(db, "LOGIN_FAILED", actor_username=payload.username, source_app="UI", actor_type="USER",
                  reason="Account temporarily locked for this source address (too many recent failed attempts)",
                  metadata={"ip": client_ip, "locked": True})
        raise HTTPException(
            status_code=423,
            detail=(
                "Too many failed login attempts from this address. "
                f"Try again in a few minutes (locks clear {settings.ACCOUNT_LOCKOUT_WINDOW_MINUTES} "
                "minutes after the last failed attempt, or immediately on the next correct password)."
            ),
        )

    user = db.query(User).filter(User.username == payload.username).first()
    if user and verify_password(payload.password, user.password_hash):
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User account is disabled")

        if mfa_store.is_enabled(user.id):
            # Password verified, but MFA (R3-02) is enrolled for this user:
            # do not issue real tokens yet. `pending_token` is structurally
            # distinct (ctx="mfa-pending") from a staff access/refresh
            # token, so get_current_user/refresh reject it outright even if
            # it leaked - it is only ever accepted at
            # POST /auth/mfa/verify-login, and only for 5 minutes.
            pending = create_mfa_pending_token(user.id)
            log_audit(db, "LOGIN", actor_username=user.username, actor_role=user.role.name if user.role else "",
                      actor_type="USER", actor_id=str(user.id), source_app="UI",
                      reason="Password verified, awaiting MFA", metadata={"ip": client_ip, "mfa_pending": True})
            return JSONResponse(status_code=200, content={"mfa_required": True, "pending_token": pending})

        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        log_audit(db, "LOGIN", actor_username=user.username, actor_role=user.role.name if user.role else "",
                  actor_type="USER", actor_id=str(user.id),
                  source_app="UI", reason="User logged in", metadata={"ip": client_ip})
        return TokenResponse(
            access_token=create_access_token(user.id, user.username, user.role.name if user.role else ""),
            refresh_token=create_refresh_token(user.id),
            user=_user_out(user),
        )
    # NOTE: `OrganizationUser` accounts do not authenticate here. This branch
    # used to mint a token with the staff `ctx`/`type` for `org_user.id`, and
    # because `organization_users.id` and `users.id` are independent,
    # colliding sequences, that token would resolve in `get_current_user` as
    # whichever `users` row happened to share the same id (see
    # STAFF_SUBJECT_TYPE) — e.g. `organization_users` id 1 (an org user)
    # authenticating as `users` id 1 (the platform admin). Organization users
    # authenticate exclusively through `POST /organizations/auth/login`,
    # which mints a structurally distinct `ctx="org-auth"` token that staff
    # endpoints reject outright.
    log_audit(db, "LOGIN_FAILED", actor_username=payload.username, source_app="UI",
              actor_type="USER",
              reason="Invalid credentials", metadata={"ip": client_ip})
    from app.core.alerting import check_failed_logins
    from app.core.metrics import LOGIN_FAILURES_TOTAL

    LOGIN_FAILURES_TOTAL.inc()
    check_failed_logins(db, username=payload.username)

    # Username-wide progressive delay - the brake that replaces the
    # username-wide hard lock (see the lockout comment above). Counted
    # across every origin, applied ONLY here on the failure branch, so it
    # never delays a correct password. Bounded by
    # LOGIN_FAILURE_DELAY_MAX_SECONDS because the delay itself occupies a
    # worker thread: an uncapped backoff would be its own denial of
    # service, which is the mistake this whole change exists to avoid.
    delay = login_failure_delay_seconds(
        failed_logins_since_last_success(
            db, payload.username, window_minutes=settings.ACCOUNT_LOCKOUT_WINDOW_MINUTES,
        ),
        threshold=settings.ACCOUNT_LOCKOUT_THRESHOLD,
        cap_seconds=settings.LOGIN_FAILURE_DELAY_MAX_SECONDS,
    )
    if delay > 0:
        time.sleep(delay)
    raise HTTPException(status_code=401, detail="Invalid username or password")


class MfaVerifyLoginRequest(BaseModel):
    pending_token: str
    code: str


@router.post("/mfa/verify-login", response_model=TokenResponse)
def mfa_verify_login(payload: MfaVerifyLoginRequest, db: Session = Depends(get_db)):
    """Exchanges the `pending_token` from a password-verified-but-MFA-pending
    `/auth/login` response, plus a valid TOTP (or one-time backup) code,
    for a real access/refresh token pair."""
    settings = get_settings()
    data = decode_token(payload.pending_token)
    if (
        not data
        or data.get("ctx") != MFA_PENDING_CONTEXT
        or data.get("type") != "mfa-pending"
        or data.get("sub_type") != STAFF_SUBJECT_TYPE
        or data.get("aud") != settings.JWT_AUDIENCE
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired MFA session")
    user = db.get(User, int(data.get("sub", 0)))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    from app.core.metrics import MFA_CHALLENGES_TOTAL

    if not mfa_store.verify(user.id, payload.code):
        MFA_CHALLENGES_TOTAL.labels(result="failure").inc()
        log_audit(db, "LOGIN_FAILED", actor_username=user.username, actor_type="USER", source_app="UI",
                  reason="Invalid MFA code")
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    MFA_CHALLENGES_TOTAL.labels(result="success").inc()

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    log_audit(db, "LOGIN", actor_username=user.username, actor_role=user.role.name if user.role else "",
              actor_type="USER", actor_id=str(user.id), source_app="UI", reason="User logged in (MFA verified)")
    return TokenResponse(
        access_token=create_access_token(user.id, user.username, user.role.name if user.role else ""),
        refresh_token=create_refresh_token(user.id),
        user=_user_out(user),
    )


@router.post("/mfa/enroll")
def mfa_enroll(current_user: User = Depends(get_current_user)):
    """Starts (or restarts) TOTP enrollment: returns a fresh secret and its
    otpauth:// provisioning URI for an authenticator app. MFA is not yet
    enabled - call /auth/mfa/confirm with a valid code to turn it on. See
    app/core/mfa.py's module docstring for why this is process-memory only
    today, not yet persisted."""
    secret = mfa_store.start_enrollment(current_user.id)
    return {"secret": secret, "provisioning_uri": provisioning_uri(secret, current_user.username)}


class MfaConfirmRequest(BaseModel):
    code: str


@router.post("/mfa/confirm")
def mfa_confirm(payload: MfaConfirmRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    backup_codes = mfa_store.confirm_enrollment(current_user.id, payload.code)
    if backup_codes is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    log_audit(db, "USER_UPDATED", actor_username=current_user.username, actor_type="USER",
              actor_id=str(current_user.id), actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", reason="MFA enabled")
    return {"enabled": True, "backup_codes": backup_codes}


@router.post("/mfa/disable")
def mfa_disable(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mfa_store.disable(current_user.id)
    log_audit(db, "USER_UPDATED", actor_username=current_user.username, actor_type="USER",
              actor_id=str(current_user.id), actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", reason="MFA disabled")
    return {"enabled": False}


@router.get("/mfa/status")
def mfa_status(current_user: User = Depends(get_current_user)):
    return {"enabled": mfa_store.is_enabled(current_user.id)}


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    data = decode_token(payload.refresh_token)
    if (
        not data
        or data.get("ctx") != "consent-auth"
        or data.get("type") != "refresh"
        or data.get("sub_type") != STAFF_SUBJECT_TYPE
        or data.get("aud") != settings.JWT_AUDIENCE
    ):
        # `sub_type` closes the refresh-token laundering path: a token minted
        # for another subject table (e.g. the old org-login bug, which reused
        # this same ctx/type for `organization_users.id`) must not be
        # exchangeable for a fresh staff access token just because `sub`
        # happens to collide with a `users.id`. `aud` fails closed on any
        # token minted before R3-02 (which carried no `aud` at all).
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    from app.core.token_revocation import is_revoked

    if is_revoked(data.get("jti")):
        raise HTTPException(status_code=401, detail="Refresh token has been revoked")

    user = db.get(User, int(data.get("sub", 0)))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return TokenResponse(
        access_token=create_access_token(user.id, user.username, user.role.name if user.role else ""),
        refresh_token=create_refresh_token(user.id),
        user=_user_out(user),
    )


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    settings = get_settings()
    payload = decode_token(credentials.credentials)
    if (
        not payload
        or payload.get("ctx") != AUTH_CONTEXT
        or payload.get("type") != "access"
        or payload.get("sub_type") != STAFF_SUBJECT_TYPE
        or payload.get("aud") != settings.JWT_AUDIENCE
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    from app.core.token_revocation import is_revoked

    if is_revoked(payload.get("jti")):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    user_id = int(payload.get("sub", 0))
    user = db.get(User, user_id)
    if user and user.is_active:
        return _user_out(user)
    # No `OrganizationUser` fallback here: this endpoint resolves `sub`
    # against the `users` table only, and the `sub_type` check above already
    # guarantees the token was minted for that table. Organization users get
    # their profile from `/organizations/*` routes with their own org-access
    # token, not from this endpoint.
    raise HTTPException(status_code=401, detail="User not found or inactive")


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


@router.post("/logout")
def logout(
    payload: Optional[LogoutRequest] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """R3-02/H-02: "token revocation ... honoured on logout". Revokes the
    access token that authenticated this call immediately (added to the
    jti revocation list - see app/core/token_revocation.py), and, if the
    caller includes its own refresh token in the body, that too - both
    become unusable right away rather than staying valid until their
    natural expiry."""
    from app.core.token_revocation import revoke

    if credentials is not None:
        access_payload = decode_token(credentials.credentials)
        if access_payload and access_payload.get("jti") and access_payload.get("exp"):
            ttl = int(access_payload["exp"] - datetime.now(timezone.utc).timestamp())
            revoke(access_payload["jti"], ttl_seconds=max(ttl, 1))

    if payload and payload.refresh_token:
        refresh_payload = decode_token(payload.refresh_token)
        # Same claim set /auth/refresh itself demands (ctx/type/aud on top
        # of sub_type/sub), so "which tokens count as this user's refresh
        # token" has one answer rather than two. Low severity on its own -
        # revoking a jti that was never a staff refresh token costs nothing
        # - but a check that is weaker here than at the accepting endpoint
        # is a check that will be copied somewhere it does matter.
        if (
            refresh_payload
            and refresh_payload.get("ctx") == AUTH_CONTEXT
            and refresh_payload.get("type") == "refresh"
            and refresh_payload.get("aud") == get_settings().JWT_AUDIENCE
            and refresh_payload.get("sub_type") == STAFF_SUBJECT_TYPE
            and refresh_payload.get("sub") == str(current_user.id)
            and refresh_payload.get("jti")
            and refresh_payload.get("exp")
        ):
            ttl = int(refresh_payload["exp"] - datetime.now(timezone.utc).timestamp())
            revoke(refresh_payload["jti"], ttl_seconds=max(ttl, 1))

    log_audit(db, "LOGOUT", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "", source_app="UI",
              actor_type="USER", actor_id=str(current_user.id))
    return {"message": "Logged out"}


@router.get("/users", response_model=list[UserListOut])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    users = db.query(User).order_by(User.id).all()
    # H-05: "audit every PII read ... admin actions". The generic
    # AccessLogMiddleware (app/core/access_log.py) already structured-logs
    # every GET here regardless of route file; this precise, richly-detailed
    # audit_logs row is additionally written because this file owns the
    # exact actor context (current_user), unlike the generic middleware.
    log_audit(db, "USER_LIST_VIEWED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason="Staff user list viewed", metadata={"count": len(users)})
    return [
        UserListOut(
            id=u.id, username=u.username, full_name=u.full_name, email=mask_identifier(u.email),
            role_id=u.role_id, role_name=u.role.name if u.role else "", is_active=u.is_active,
            created_at=u.created_at,
        )
        for u in users
    ]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db),
                current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if find_by_search_digest(db.query(User), User.email_search, payload.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    problems = validate_password_strength(payload.password, username=payload.username)
    if problems:
        raise _password_policy_error(problems)
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        email_search=hmac_digest(payload.email),
        password_hash=hash_password(payload.password),
        role_id=payload.role_id,
        is_active=payload.is_active,
    )
    db.add(user)
    db.flush()
    log_audit(db, "USER_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason=f"User {payload.username} created", metadata={"role_id": payload.role_id})
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
                current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        user.email = payload.email
        user.email_search = hmac_digest(payload.email)
    if payload.password is not None:
        problems = validate_password_strength(payload.password, username=user.username)
        if problems:
            raise _password_policy_error(problems)
        user.password_hash = hash_password(payload.password)
    role_changed = False
    old_role_id = user.role_id
    if payload.role_id is not None and payload.role_id != user.role_id:
        role_changed = True
        user.role_id = payload.role_id
    if payload.is_active is not None:
        user.is_active = payload.is_active
    log_audit(db, "USER_UPDATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app="UI",
              reason=f"User {user.username} updated")
    if role_changed:
        # R3-02/R-04: "emit role-change events" (distinct from the general
        # USER_UPDATED above, so an access reviewer can filter specifically
        # on privilege changes).
        log_audit(db, "ROLE_CHANGED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id), source_app="UI",
                  reason=f"User {user.username}'s role changed",
                  metadata={"user_id": user.id, "old_role_id": old_role_id, "new_role_id": payload.role_id})
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_permission(PERM_USER_MANAGE))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.username == "admin":
        raise HTTPException(status_code=403, detail="Cannot delete the default admin user")
    username = user.username
    db.delete(user)
    db.commit()
    log_audit(db, "USER_DELETED", actor_username=current_user.username, source_app="UI",
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              reason=f"User {username} deleted")
    return {"deleted": True, "user_id": user_id}
