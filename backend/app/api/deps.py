from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import STAFF_SUBJECT_TYPE, decode_token
from app.models.entities import Customer, Role, User
from app.services.tenancy import resolve_customer

settings = get_settings()

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if (
        not payload
        or payload.get("ctx") != "consent-auth"
        or payload.get("type") != "access"
        or payload.get("sub_type") != STAFF_SUBJECT_TYPE
        # RFC 8725 `aud` check (R3-02): decode_token only verifies `iss`
        # (shared by every token this app mints); the intended-audience
        # check is each call site's own job because staff and context
        # tokens use different audiences. A token minted for some OTHER
        # audience (or missing `aud` entirely - the shape of every token
        # minted before this change) fails closed here.
        or payload.get("aud") != settings.JWT_AUDIENCE
    ):
        # The `sub_type` check is independent of `ctx`/`type`: it fails closed
        # if a token is ever minted with the right context/type but for a
        # different subject table (e.g. `organization_users`, whose ids
        # collide with `users` ids), rather than letting `db.get(User, ...)`
        # silently resolve to whichever staff row happens to share that id.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from app.core.token_revocation import is_revoked

    if is_revoked(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = int(payload.get("sub", "0"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token subject")
    user = db.get(User, user_id)
    # R3-02: real-time revocation on deactivation. This re-checks
    # `is_active` against the DB on EVERY request rather than trusting a
    # claim baked into the token at login time, so disabling a staff
    # account takes effect on that user's very next request regardless of
    # how much longer their already-issued access token has left to live -
    # no token-revocation-list entry is needed for this half of "token
    # revocation ... honoured on ... deactivation" (see
    # tests/test_auth_hardening.py::test_deactivation_revokes_an_already_issued_token).
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_permission(permission: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        perms = user.role.permissions if user.role else []
        if permission not in perms and "*" not in perms:
            raise HTTPException(status_code=403, detail=f"Permission denied: {permission} required")
        return user

    return checker


def get_actor(user: User) -> str:
    return user.username


def get_org_scope(user: User) -> str | None:
    """Return the source_app scope for org-scoped roles, or None for full access."""
    role_name = user.role.name if user.role else ""
    from app.core.rbac import ORG_SCOPE_MAP
    return ORG_SCOPE_MAP.get(role_name)


def get_role_name(user: User) -> str:
    return user.role.name if user.role else ""


@dataclass
class ResolvedApiKey:
    tenant_code: Optional[str]
    tenant_id: Optional[int]
    scopes: list[str] = field(default_factory=list)


_INVALID_KEY_DETAIL = "Invalid integration API key"


def verify_integration_key(
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> ResolvedApiKey:
    import secrets

    from app.core.api_keys import hash_api_key, looks_like_api_key
    from app.models.entities import ApiKey, Organization

    if not x_api_key:
        raise HTTPException(status_code=401, detail=_INVALID_KEY_DETAIL)

    if looks_like_api_key(x_api_key):
        record = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(x_api_key)).first()
        if not record or record.revoked_at is not None:
            raise HTTPException(status_code=401, detail=_INVALID_KEY_DETAIL)
        if record.expires_at is not None and record.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail=_INVALID_KEY_DETAIL)
        tenant = db.get(Organization, record.tenant_id)
        if not tenant or not tenant.is_active:
            raise HTTPException(status_code=401, detail=_INVALID_KEY_DETAIL)
        record.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return ResolvedApiKey(tenant_code=tenant.code, tenant_id=tenant.id, scopes=list(record.scopes or []))

    if settings.ALLOW_LEGACY_INTEGRATION_KEY and secrets.compare_digest(x_api_key, settings.INTEGRATION_API_KEY):
        return ResolvedApiKey(tenant_code=None, tenant_id=None, scopes=[])

    raise HTTPException(status_code=401, detail=_INVALID_KEY_DETAIL)


def require_scope(resolved_key: ResolvedApiKey, scope: str) -> None:
    """Enforce that the caller was actually granted `scope`.

    A tenant-bound key's own `scopes` list is authoritative. The legacy,
    unbound `INTEGRATION_API_KEY` (``resolved_key.tenant_code is None``) is
    NOT exempt from this check — it used to return here unconditionally,
    and that blanket exemption is exactly what let the legacy key purge ANY
    tenant's customer despite never having been granted
    `SCOPE_CUSTOMER_PURGE` (see the R3 fix report for the live exploit this
    closes: `DELETE /crm/customers/by-email/{email}` with the shared legacy
    key succeeded with zero scopes). It now implicitly carries exactly
    `SCOPE_INTEGRATION_WRITE` — the one baseline capability every
    integration caller needs, and the same default a freshly-created
    tenant-bound key with no explicit scopes gets (see
    `app.core.api_keys`'s module docstring). Every OTHER scope
    (`SCOPE_CUSTOMER_PURGE`, `SCOPE_FIDUCIARY_ASSERT`, and any future one)
    is a deliberate, per-tenant opt-in that a credential shared by every
    caller cannot prove was ever granted for one specific tenant, so none
    of them is ever implicitly available to it.

    Breaks, by design: any caller relying on the legacy key to purge a
    customer or to assert fiduciary identity — both now require a real,
    tenant-bound, explicitly-scoped API key (see ``seed_api_keys.py``).
    """
    from app.core.api_keys import SCOPE_INTEGRATION_WRITE

    effective_scopes = resolved_key.scopes if resolved_key.tenant_code is not None else [SCOPE_INTEGRATION_WRITE]
    if scope not in (effective_scopes or []):
        raise HTTPException(
            status_code=403,
            detail=f"This API key is not scoped for '{scope}'",
        )


def verify_context_token(token: str) -> dict:
    payload = decode_token(token)
    if (
        not payload
        or payload.get("ctx") != "integration"
        or payload.get("type") != "consent-context"
        # See get_current_user's identical note: decode_token only checks
        # `iss`; `aud` is each call site's own job since staff and context
        # tokens use different audiences (JWT_AUDIENCE vs
        # JWT_AUDIENCE_CONTEXT).
        or payload.get("aud") != settings.JWT_AUDIENCE_CONTEXT
    ):
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    return payload


def get_customer_from_context(db: Session, payload: dict) -> Customer:
    # Scoped to the token's own signed `source_app` claim (set once, at mint
    # time - see create_context_token) via the resolve_customer choke point:
    # a customer belonging to a DIFFERENT tenant than the one the token
    # claims is indistinguishable from "does not exist", exactly like every
    # other resolve_customer call site. This is independent of how the
    # context was minted - even if a context ever got minted pointing at
    # the wrong tenant's customer (a bug in create_context_for_customer's
    # scoping, or any future minting path), it must not be usable here.
    token_source_app = payload.get("source_app")
    customer = resolve_customer(db, source_app=token_source_app, customer_pk=int(payload.get("sub", 0))) \
        if token_source_app else None
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


def role_has_permission(role: Role, permission: str) -> bool:
    perms = role.permissions or []
    return permission in perms or "*" in perms
