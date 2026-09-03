from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token, verify_api_key_hash
from app.models.entities import Customer, Role, User

settings = get_settings()

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class TenantContext:
    """Derived from an authenticated API key, never from request body."""
    tenant_id: int
    tenant_code: str
    source_app: str
    scopes: str


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
    if not payload or payload.get("ctx") != "consent-auth" or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = int(payload.get("sub", "0"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token subject")

    # R3-02: Check token revocation
    jti = payload.get("jti")
    token_version = payload.get("token_version", 1)
    if jti:
        from app.models.entities import TokenRevocation
        revoked = db.query(TokenRevocation).filter(
            TokenRevocation.jti == jti,
            TokenRevocation.user_id == user_id,
        ).first()
        if revoked:
            raise HTTPException(status_code=401, detail="Token has been revoked")

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if hasattr(user, "token_version") and user.token_version and token_version and int(token_version) < user.token_version:
        raise HTTPException(status_code=401, detail="Token has been invalidated")
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


def verify_integration_key(
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> TenantContext:
    """R3-01: Verify a tenant-bound API key with constant-time hash comparison.

    Only tenant-bound keys from the database are accepted. The legacy static
    shared-secret key comparison has been removed to eliminate that
    vulnerability; callers that only need a pass/fail gate (rather than the
    resolved TenantContext) can depend on this the same way as before via
    ``dependencies=[Depends(verify_integration_key)]``.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid integration API key")

    from app.models.entities import ApiKey, Tenant

    api_keys = db.query(ApiKey).filter(ApiKey.is_active.is_(True)).all()
    for ak in api_keys:
        if verify_api_key_hash(x_api_key, ak.key_hash):
            if ak.revoked_at is not None:
                raise HTTPException(status_code=401, detail="API key has been revoked")
            if ak.expires_at and ak.expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="API key has expired")
            # Check grace period for rotated keys
            if ak.rotated_at and settings.API_KEY_GRACE_PERIOD_HOURS:
                grace_cutoff = ak.rotated_at + timedelta(hours=settings.API_KEY_GRACE_PERIOD_HOURS)
                if datetime.now(timezone.utc) > grace_cutoff:
                    continue
            ak.last_used_at = datetime.now(timezone.utc)
            db.commit()
            tenant = db.query(Tenant).filter(Tenant.id == ak.tenant_id).first()
            return TenantContext(
                tenant_id=ak.tenant_id,
                tenant_code=tenant.code if tenant else "",
                source_app=tenant.code.upper() if tenant else "",
                scopes=ak.scopes,
            )

    raise HTTPException(status_code=401, detail="Invalid integration API key")


# Backward-compatible alias: some route modules import this explicit name.
_verify_integration_key_with_db = verify_integration_key


def verify_context_token(token: str) -> dict:
    payload = decode_token(token)
    if not payload or payload.get("ctx") != "integration" or payload.get("type") != "consent-context":
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    return payload


def get_customer_from_context(db: Session, payload: dict) -> Customer:
    customer = db.get(Customer, int(payload.get("sub", 0)))
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


def role_has_permission(role: Role, permission: str) -> bool:
    perms = role.permissions or []
    return permission in perms or "*" in perms
