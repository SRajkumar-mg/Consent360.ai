from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.entities import Customer, Role, User

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
    user = db.get(User, user_id)
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


def verify_integration_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not x_api_key or x_api_key != settings.INTEGRATION_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid integration API key")
    return None


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
