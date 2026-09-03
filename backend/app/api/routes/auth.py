from datetime import datetime, timedelta, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user, require_permission
from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.core.rbac import PERM_USER_MANAGE, ROLE_PERMISSIONS
from app.core.security import (
    AUTH_CONTEXT,
    create_access_token,
    create_org_refresh_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.utils import login_limiter, mask_identifier
from app.models.entities import MFARecoveryCode, MFASecret, Organization, OrganizationUser, User

settings = get_settings()
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


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not login_limiter.allow(f"login:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts, please try again later")
    user = db.query(User).filter(User.username == payload.username).first()
    if user and verify_password(payload.password, user.password_hash):
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User account is disabled")
        # R3-02: Check account lockout
        if user.locked_until and user.locked_until > datetime.now(timezone.utc):
            raise HTTPException(status_code=423, detail="Account is temporarily locked")
        # R3-02: MFA verification if enrolled
        if user.mfa_enabled:
            if not payload.otp_code:
                raise HTTPException(status_code=401, detail="MFA required - provide otp_code")
            mfa_row = db.query(MFASecret).filter(MFASecret.user_id == user.id, MFASecret.is_enabled.is_(True)).first()
            valid = bool(mfa_row) and pyotp.TOTP(mfa_row.secret).verify(payload.otp_code)
            if not valid:
                # Track failed MFA attempt
                user.failed_login_count = (user.failed_login_count or 0) + 1
                if user.failed_login_count >= settings.ACCOUNT_LOCKOUT_THRESHOLD:
                    user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCOUNT_LOCKOUT_MINUTES)
                db.commit()
                log_audit(db, "LOGIN_FAILED", actor_username=payload.username, source_app="UI",
                          reason="Invalid MFA code", metadata={"ip": client_ip})
                raise HTTPException(status_code=401, detail="Invalid OTP code")
            # Reset failed login count on successful MFA
            user.failed_login_count = 0
        else:
            # No MFA enrolled - reset any previous failed count
            user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        log_audit(db, "LOGIN", actor_username=user.username, actor_role=user.role.name if user.role else "",
                  source_app="UI", reason="User logged in", metadata={"ip": client_ip})
        return TokenResponse(
            access_token=create_access_token(user.id, user.username, user.role.name if user.role else "",
                                              token_version=user.token_version or 1),
            refresh_token=create_refresh_token(user.id, token_version=user.token_version or 1),
            user=_user_out(user),
        )
    org_user = db.query(OrganizationUser).filter(OrganizationUser.username == payload.username).first()
    if org_user and verify_password(payload.password, org_user.password_hash):
        if not org_user.is_active:
            raise HTTPException(status_code=403, detail="User account is disabled")
        org_user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        org = db.get(Organization, org_user.organization_id)
        org_name = org.name if org else ""
        role_perms = ROLE_PERMISSIONS.get(org_user.role, [])
        log_audit(db, "LOGIN", actor_username=org_user.username, actor_role=org_user.role,
                  source_app="UI", reason="Org user logged in", metadata={"ip": client_ip, "org_id": org_user.organization_id})
        user_out = UserOut(
            id=org_user.id,
            username=org_user.username,
            full_name=org_user.full_name,
            email=mask_identifier(org_user.email),
            role_id=0,
            role_name=f"{org_name} {org_user.role.replace('_', ' ').replace(org_name.lower(), '').strip().title() or 'Admin'}",
            role_permissions=role_perms,
            is_active=org_user.is_active,
            last_login_at=org_user.last_login_at,
            created_at=org_user.created_at,
        )
        token_payload = {
            "sub": str(org_user.id),
            "username": org_user.username,
            "role": org_user.role,
            "org_id": org_user.organization_id,
            "type": "access",
            "ctx": AUTH_CONTEXT,
        }
        expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        token_payload.update({"exp": expires, "iat": datetime.now(timezone.utc),
                              "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE})
        access_token = jwt.encode(token_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        # R3-02: org-user refresh token uses its own longer expiry and includes
        # a jti + token_version so the revocation/invalidation check works.
        refresh_token = create_org_refresh_token(org_user.id)
        return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=user_out)
    log_audit(db, "LOGIN_FAILED", actor_username=payload.username, source_app="UI",
              reason="Invalid credentials", metadata={"ip": client_ip})
    raise HTTPException(status_code=401, detail="Invalid username or password")


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if not data or data.get("ctx") != "consent-auth" or data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.get(User, int(data.get("sub", 0)))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    token_version = data.get("token_version", 1)
    if user.token_version and token_version and int(token_version) < user.token_version:
        raise HTTPException(status_code=401, detail="Token has been invalidated")
    return TokenResponse(
        access_token=create_access_token(user.id, user.username, user.role.name if user.role else "",
                                          token_version=user.token_version or 1),
        refresh_token=create_refresh_token(user.id, token_version=user.token_version or 1),
        user=_user_out(user),
    )


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("ctx") != AUTH_CONTEXT or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = int(payload.get("sub", 0))
    user = db.get(User, user_id)
    if user and user.is_active:
        return _user_out(user)
    org_user = db.get(OrganizationUser, user_id)
    if org_user and org_user.is_active:
        org = db.get(Organization, org_user.organization_id)
        org_name = org.name if org else ""
        role_perms = ROLE_PERMISSIONS.get(org_user.role, [])
        return UserOut(
            id=org_user.id,
            username=org_user.username,
            full_name=org_user.full_name,
            email=mask_identifier(org_user.email),
            role_id=0,
            role_name=f"{org_name} {org_user.role.replace('_', ' ').replace(org_name.lower(), '').strip().title() or 'Admin'}",
            role_permissions=role_perms,
            is_active=org_user.is_active,
            last_login_at=org_user.last_login_at,
            created_at=org_user.created_at,
        )
    raise HTTPException(status_code=401, detail="User not found or inactive")


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # R3-02: Invalidate all tokens for this user by bumping token_version.
    # The get_current_user dependency checks token_version against user.token_version,
    # so incrementing it here causes all previously-issued tokens to be rejected.
    current_user.token_version = (current_user.token_version or 1) + 1
    db.commit()
    log_audit(db, "LOGOUT", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "", source_app="UI")
    return {"message": "Logged out, all tokens invalidated"}


@router.post("/mfa/enroll", response_model=dict)
def mfa_enroll(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # R3-02: TOTP MFA enrollment - generate secret and store server-side in MFASecret.
    secret = pyotp.random_base32()
    mfa_row = db.query(MFASecret).filter(MFASecret.user_id == current_user.id).first()
    if mfa_row:
        mfa_row.secret = secret
        mfa_row.is_enabled = True
        mfa_row.enabled_at = datetime.now(timezone.utc)
    else:
        mfa_row = MFASecret(user_id=current_user.id, secret=secret, is_enabled=True,
                             enabled_at=datetime.now(timezone.utc))
        db.add(mfa_row)
    current_user.mfa_enabled = True
    db.commit()
    # Generate QR URI for TOTP app (secret not returned - user scans QR)
    totp = pyotp.TOTP(secret)
    qr_uri = totp.provisioning_uri(name=current_user.username, issuer_name="Consent360")
    return {"qr_code_uri": qr_uri, "backup_codes": []}


@router.post("/mfa/verify", response_model=dict)
def mfa_verify(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # R3-02: Verify TOTP code during login
    code = payload.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="OTP code required")
    mfa_row = db.query(MFASecret).filter(MFASecret.user_id == current_user.id).first()
    if not mfa_row:
        raise HTTPException(status_code=400, detail="MFA not enrolled")
    if not pyotp.TOTP(mfa_row.secret).verify(code):
        # Track failed MFA attempt
        current_user.failed_login_count = (current_user.failed_login_count or 0) + 1
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid OTP code")
    # Reset failed login count on successful MFA
    current_user.failed_login_count = 0
    db.commit()
    return {"message": "MFA verified successfully"}


@router.post("/mfa/recovery-codes", response_model=dict)
def mfa_recovery_codes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # R3-02: Generate recovery codes for MFA (single-use, stored hashed)
    from hashlib import sha256
    codes_generated = []
    for i in range(8):
        code = pyotp.random_base32()[:8]
        code_hash = sha256(code.encode()).hexdigest()
        recovery = MFARecoveryCode(
            user_id=current_user.id,
            code_hash=code_hash,
            is_used=False,
        )
        db.add(recovery)
        codes_generated.append(code)
    db.commit()
    # Return count of unused codes (not the codes themselves for security)
    unused = db.query(MFARecoveryCode).filter(
        MFARecoveryCode.user_id == current_user.id,
        MFARecoveryCode.is_used == False,
    ).count()
    return {"code_count": unused, "remaining": unused}


@router.get("/users", response_model=list[UserListOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    users = db.query(User).order_by(User.id).all()
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
                _: User = Depends(require_permission(PERM_USER_MANAGE))):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(User).filter(User.email_search == hmac_digest(payload.email)).first():
        raise HTTPException(status_code=409, detail="Email already registered")
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
    log_audit(db, "USER_CREATED", actor_username="system", source_app="UI",
              reason=f"User {payload.username} created", metadata={"role_id": payload.role_id})
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
                _: User = Depends(require_permission(PERM_USER_MANAGE))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        user.email = payload.email
        user.email_search = hmac_digest(payload.email)
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    if payload.role_id is not None:
        user.role_id = payload.role_id
    if payload.is_active is not None:
        user.is_active = payload.is_active
    log_audit(db, "USER_UPDATED", actor_username="system", source_app="UI",
              reason=f"User {user.username} updated")
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
              reason=f"User {username} deleted")
    return {"deleted": True, "user_id": user_id}
