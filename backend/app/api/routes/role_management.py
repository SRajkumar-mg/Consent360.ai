"""R3-02: Staff authentication hardening - role management, MFA, token revocation."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user, require_permission
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE, ROLE_PERMISSIONS
from app.core.security import (
    create_access_token,
    create_org_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.entities import (
    MFARecoveryCode,
    MFASecret,
    Organization,
    OrganizationUser,
    Role,
    TokenRevocation,
    User,
)
from app.schemas.schemas import (
    OrganizationLoginRequest,
    OrganizationLoginResponse,
    OrganizationUserOut,
    TokenResponse,
    UserOut,
)
from app.services.audit import log_audit

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth-hardening"])


class MFAEnrollResponse(BaseModel):
    secret: str
    otpauth_url: str
    recovery_codes: list[str]


class MFAVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    description: str | None = None
    permissions: list[str] | None = None


class RoleAssignment(BaseModel):
    user_id: int
    role_id: int


# ============================================================================
# Token Revocation (R3-02)
# ============================================================================

@router.post("/logout")
def logout_hardened(
    current_user: User = Depends(get_current_user),
    credentials=Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """R3-02: Logout that actually revokes the token."""
    if credentials:
        payload = decode_token(credentials.credentials)
        if payload and payload.get("jti"):
            jti = payload["jti"]
            exp = datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc)
            revocation = TokenRevocation(
                user_id=current_user.id,
                jti=jti,
                expires_at=exp,
            )
            db.add(revocation)
    # Also bump token version to invalidate all tokens
    current_user.token_version = (current_user.token_version or 1) + 1
    db.commit()
    log_audit(db, "LOGOUT", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "", source_app="UI")
    return {"message": "Logged out"}


# ============================================================================
# Account Lockout (R3-02)
# ============================================================================

@router.post("/login")
def login_hardened(payload: OrganizationLoginRequest, request: Request, db: Session = Depends(get_db)):
    """R3-02: Login with account lockout."""
    client_ip = request.client.host if request.client else "unknown"
    org_user = db.query(OrganizationUser).filter(OrganizationUser.username == payload.username).first()
    if not org_user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Check lockout
    if hasattr(org_user, 'locked_until') and org_user.locked_until:
        if org_user.locked_until > datetime.now(timezone.utc):
            raise HTTPException(status_code=423, detail="Account is temporarily locked")

    if not verify_password(payload.password, org_user.password_hash):
        # Increment failed count
        if hasattr(org_user, 'failed_login_count'):
            org_user.failed_login_count = (org_user.failed_login_count or 0) + 1
            if org_user.failed_login_count >= settings.ACCOUNT_LOCKOUT_THRESHOLD:
                org_user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCOUNT_LOCKOUT_MINUTES)
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Reset failed count on success
    if hasattr(org_user, 'failed_login_count'):
        org_user.failed_login_count = 0
        org_user.locked_until = None
    org_user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    org = db.get(Organization, org_user.organization_id)
    org_name = org.name if org else ""
    role_perms = ROLE_PERMISSIONS.get(org_user.role, [])
    token_payload = {
        "sub": str(org_user.id),
        "username": org_user.username,
        "role": org_user.role,
        "org_id": org_user.organization_id,
        "type": "access",
        "ctx": "consent-auth",
    }
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    from jose import jwt
    token_payload.update({"exp": expires, "iat": datetime.now(timezone.utc),
                          "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE})
    access_token = jwt.encode(token_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    refresh_token = create_org_refresh_token(org_user.id)

    user_out = UserOut(
        id=org_user.id, username=org_user.username, full_name=org_user.full_name,
        email="***", role_id=0,
        role_name=f"{org_name} {org_user.role.replace('_', ' ').title()}",
        role_permissions=role_perms, is_active=org_user.is_active,
        last_login_at=org_user.last_login_at, created_at=org_user.created_at,
    )

    log_audit(db, "LOGIN", actor_username=org_user.username, actor_role=org_user.role,
              source_app="UI", reason="Org user logged in", metadata={"ip": client_ip})

    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=user_out)


# ============================================================================
# MFA (R3-02)
# ============================================================================

@router.post("/mfa/enroll", response_model=MFAEnrollResponse)
def mfa_enroll(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import pyotp
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    otpauth_url = totp.provisioning_uri(
        name=current_user.username,
        issuer_name="Consent360",
    )
    # Store secret (not yet enabled)
    existing = db.query(MFASecret).filter(MFASecret.user_id == current_user.id).first()
    if existing:
        existing.secret = secret
        existing.is_enabled = False
    else:
        mfa = MFASecret(user_id=current_user.id, secret=secret)
        db.add(mfa)

    # Generate recovery codes
    recovery_codes = []
    for _ in range(10):
        import secrets
        code = secrets.token_hex(4)
        recovery_codes.append(code)
        rc = MFARecoveryCode(
            user_id=current_user.id,
            code_hash=hash_password(code),
        )
        db.add(rc)

    db.commit()
    return MFAEnrollResponse(secret=secret, otpauth_url=otpauth_url, recovery_codes=recovery_codes)


@router.post("/mfa/verify")
def mfa_verify(
    payload: MFAVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mfa = db.query(MFASecret).filter(MFASecret.user_id == current_user.id).first()
    if not mfa:
        raise HTTPException(status_code=400, detail="MFA not enrolled")
    import pyotp
    totp = pyotp.TOTP(mfa.secret)
    if totp.verify(payload.code, valid_window=1):
        mfa.is_enabled = True
        mfa.enabled_at = datetime.now(timezone.utc)
        current_user.mfa_enabled = True
        db.commit()
        return {"message": "MFA enabled"}
    raise HTTPException(status_code=401, detail="Invalid MFA code")


# ============================================================================
# Role Management (R3-02)
# ============================================================================

@router.get("/roles")
def list_roles(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    roles = db.query(Role).order_by(Role.id).all()
    return [
        {"id": r.id, "name": r.name, "description": r.description,
         "permissions": r.permissions, "is_system": r.is_system}
        for r in roles
    ]


@router.post("/roles", status_code=201)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    if db.query(Role).filter(Role.name == payload.name).first():
        raise HTTPException(status_code=409, detail="Role already exists")
    role = Role(
        name=payload.name,
        description=payload.description,
        permissions=payload.permissions,
        is_system=False,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    log_audit(db, "ROLE_CHANGED", actor_username="system", source_app="UI",
              reason=f"Role {role.name} created")
    return {"id": role.id, "name": role.name, "description": role.description,
            "permissions": role.permissions, "is_system": role.is_system}


@router.put("/roles/{role_id}")
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="Cannot modify system roles")
    old_perms = role.permissions or []
    if payload.description is not None:
        role.description = payload.description
    if payload.permissions is not None:
        role.permissions = payload.permissions
    db.commit()
    log_audit(db, "ROLE_CHANGED", actor_username="system", source_app="UI",
              reason=f"Role {role.name} updated",
              metadata={"old_permissions": old_perms, "new_permissions": role.permissions})
    return {"updated": True}


@router.post("/roles/assign")
def assign_role(
    payload: RoleAssignment,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    user = db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    role = db.get(Role, payload.role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    old_role = user.role.name if user.role else ""
    user.role_id = payload.role_id
    db.commit()
    log_audit(db, "ROLE_CHANGED", actor_username="system", source_app="UI",
              reason=f"Role assigned to {user.username}",
              metadata={"old_role": old_role, "new_role": role.name, "user_id": user.id})
    return {"assigned": True}


@router.get("/access-review")
def access_review(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    users = db.query(User).filter(User.is_active.is_(True)).all()
    return [
        {"id": u.id, "username": u.username, "role": u.role.name if u.role else "",
         "permissions": u.role.permissions if u.role else [],
         "last_login": u.last_login_at.isoformat() if u.last_login_at else None}
        for u in users
    ]
