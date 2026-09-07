import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

AUTH_CONTEXT = "consent-auth"
INTEGRATION_CONTEXT = "integration"
# R3-02 MFA: a short-lived, structurally distinct context for the token
# issued between "password verified" and "MFA code verified" - see
# create_mfa_pending_token and app/api/routes/auth.py's login()/
# mfa_verify_login(). Deliberately never accepted by get_current_user or
# /auth/refresh (different ctx AND type), so it cannot be used as a
# substitute access token even if it leaked.
MFA_PENDING_CONTEXT = "mfa-pending"

# Explicit subject-type claim carried by every staff token, independent of
# `ctx`/`type`. `get_current_user` (app/api/deps.py) and `/auth/refresh` both
# require this claim before resolving `sub` against the `users` table, so a
# token minted with the right `ctx`/`type` by mistake (the shape of the
# org-login privilege-escalation bug this closes: `organization_users` and
# `users` have independently-assigned, colliding ids) still fails closed
# instead of silently resolving to whichever staff row happens to share that
# numeric id.
STAFF_SUBJECT_TYPE = "staff_user"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(user_id: int, username: str, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "sub_type": STAFF_SUBJECT_TYPE,
        "username": username,
        "role": role,
        "type": "access",
        "ctx": AUTH_CONTEXT,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        # R3-02: "token revocation list (jti) honoured on logout and
        # deactivation". Deactivation is already enforced independently of
        # this (get_current_user re-checks `user.is_active` against the DB
        # on every request); this `jti` is what /auth/logout revokes - see
        # app/core/token_revocation.py.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "sub_type": STAFF_SUBJECT_TYPE,
        "type": "refresh",
        "ctx": AUTH_CONTEXT,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_context_token(customer_id: int, source_app: str, request_id: Optional[str] = None) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(customer_id),
        "source_app": source_app,
        "type": "consent-context",
        "ctx": INTEGRATION_CONTEXT,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE_CONTEXT,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "request_id": request_id,
        # JWT `exp`/`iat` are serialised as second-granularity NumericDate
        # values, so two mints for the same customer/source_app/request_id
        # within the same wall-clock second were otherwise byte-for-byte
        # identical tokens - and `consent_contexts.token` is unique, so the
        # second INSERT raised an IntegrityError (a 500) instead of minting
        # a second, independent context. A random per-token nonce guarantees
        # uniqueness regardless of timing; it carries no meaning on its own
        # and is not validated on decode.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_mfa_pending_token(user_id: int) -> str:
    """Issued by /auth/login once a password verifies for a user who has
    MFA enabled, in place of real access/refresh tokens. Exchanged at
    POST /auth/mfa/verify-login for the real pair once the TOTP/backup
    code also verifies. 5-minute expiry - long enough to type a code, short
    enough that a leaked pending token is not a standing risk."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    payload = {
        "sub": str(user_id),
        "sub_type": STAFF_SUBJECT_TYPE,
        "type": "mfa-pending",
        "ctx": MFA_PENDING_CONTEXT,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a token minted by this module.

    Enforces `iss` (RFC 8725: every verifier should check issuer/audience
    explicitly rather than trusting the signature alone) - every token this
    module mints carries the same `iss`, so it is safe to check here
    unconditionally. `aud` is deliberately NOT checked here: staff and
    context tokens use two different audiences (JWT_AUDIENCE vs
    JWT_AUDIENCE_CONTEXT) and this single function serves both, so each
    caller (get_current_user / /auth/refresh / /auth/me for staff;
    verify_context_token for context) checks its own expected `aud`
    explicitly instead - see those call sites.

    `options={"verify_aud": False}` is required here, NOT optional:
    python-jose raises `JWTClaimsError` (a `JWTError` subclass, so it is
    swallowed by the `except` below into a silent `None`) whenever a token
    CONTAINS an `aud` claim and the caller does not also pass a matching
    `audience=` to `jwt.decode` - "don't check it here" otherwise means
    "every token with an `aud` claim fails to decode at all", not "accept
    any audience", which would have made every access/refresh/context token
    this module mints undecodable and broken the per-call-site `aud` checks
    below before they ever ran. Disabling the library's own audience
    verification is what makes those explicit downstream checks the actual
    (and only) enforcement point, which is the intended design.

    A token missing `iss` entirely, or carrying the wrong one, still fails
    closed here for everyone.
    """
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM], issuer=settings.JWT_ISSUER,
            options={"verify_aud": False},
        )
    except JWTError:
        return None


# --------------------------------------------------------------------------- #
# R3-02: password policy
# --------------------------------------------------------------------------- #

_COMMON_PASSWORDS = {
    "password", "password1", "password123", "admin123", "12345678", "123456789",
    "qwerty123", "letmein123", "welcome123", "changeme123", "consent360",
}


def validate_password_strength(password: str, *, username: Optional[str] = None) -> list[str]:
    """R3-02 password policy. Returns a list of human-readable violations
    (empty means the password is acceptable). Applied only when a password
    is being SET (staff user create/update in app/api/routes/auth.py) -
    never at login/verify time, so it can never lock an already-hashed
    existing password out (the seeded admin/Admin@1234 account is
    unaffected: its stored hash is never re-validated against this)."""
    problems: list[str] = []
    min_length = get_settings().PASSWORD_MIN_LENGTH
    if len(password) < min_length:
        problems.append(f"Password must be at least {min_length} characters")
    if not re.search(r"[a-z]", password):
        problems.append("Password must include a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("Password must include an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("Password must include a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("Password must include a special (non-alphanumeric) character")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("Password is too common")
    if username and username.lower() in password.lower():
        problems.append("Password must not contain the username")
    return problems


# --------------------------------------------------------------------------- #
# R3-02 / R3-04: failed-login counting shared by account lockout and alerting
# --------------------------------------------------------------------------- #

def recent_failed_login_count(db, username: str, *, window_minutes: int) -> int:
    """Count `LOGIN_FAILED` audit rows for `username` in the trailing
    `window_minutes`. Shared source of truth for R3-04's "repeated failed
    logins" alert; R3-02's account lockout uses the more precise
    `failed_logins_since_last_success` below. Both read the same
    append-only audit trail rather than keeping their own counter, so no
    new `failed_login_count`/`locked_until` column is needed on `users`
    (entities.py is outside this lane's scope) - it is durable and correct
    without one: it survives a restart and is consistent across every
    worker process, since it is backed by Postgres rather than per-process
    memory.
    """
    from app.models.entities import AuditLog

    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.event == "LOGIN_FAILED",
            AuditLog.actor_username == username,
            AuditLog.created_at >= since,
        )
        .count()
    )


def failed_logins_since_last_success(
    db, username: str, *, window_minutes: int, client_ip: str | None = None
) -> int:
    """Count `LOGIN_FAILED` rows for `username` since whichever is more
    recent: their last successful `LOGIN`, or `window_minutes` ago.

    This is what app.api.routes.auth::login uses for account lockout
    (R3-02/H-02): a single successful login immediately clears the count
    (rather than making the account wait out the full time window even
    after proving the correct password), while the window still bounds how
    far back a never-yet-successful username is checked.

    `client_ip` narrows the count to failures from ONE source address, by
    matching the `ip` key `login()` already records in the audit row's
    `details` JSON (a plain, unencrypted column, so this is a SQL filter
    rather than a scan). That is what turns the hard lockout from an
    unauthenticated denial-of-service against any known username into a
    per-origin block - see `login()`'s own comment for the reasoning and
    the residual risk.
    """
    from app.models.entities import AuditLog

    window_start = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    last_success_q = db.query(AuditLog.created_at).filter(
        AuditLog.event == "LOGIN", AuditLog.actor_username == username
    )
    # A successful login clears the count only for the SAME origin when the
    # count is per-origin; otherwise one attacker's success elsewhere (or
    # the victim's own legitimate login) would reset another origin's
    # budget and hand a guesser five fresh attempts every time the real
    # user signs in.
    if client_ip is not None:
        last_success_q = last_success_q.filter(AuditLog.details["ip"].as_string() == client_ip)
    last_success = last_success_q.order_by(AuditLog.created_at.desc()).first()
    since = max(window_start, last_success[0]) if last_success else window_start
    query = db.query(AuditLog).filter(
        AuditLog.event == "LOGIN_FAILED",
        AuditLog.actor_username == username,
        AuditLog.created_at > since,
    )
    if client_ip is not None:
        query = query.filter(AuditLog.details["ip"].as_string() == client_ip)
    return query.count()


def login_failure_delay_seconds(failure_count: int, *, threshold: int, cap_seconds: float) -> float:
    """Progressive per-username delay applied AFTER a failed password check.

    Doubles per failure past `threshold` (1s, 2s, 4s, ...) up to
    `cap_seconds`, and is exactly 0 below it. Deliberately a delay and not a
    lock: a delay costs an online guesser real wall-clock time per attempt
    while the legitimate account holder, who supplies the correct password,
    is never slowed at all - `login()` applies this only on the failure
    branch. That is the property a username-wide hard lock cannot have, and
    it is why the username-wide brake here is a delay while the hard 423
    lock is scoped to one (username, IP) pair.
    """
    if failure_count < threshold:
        return 0.0
    return min(float(2 ** (failure_count - threshold)), float(cap_seconds))
