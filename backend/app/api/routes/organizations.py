"""Organization management routes - CRUD, auth, and org-scoped dashboards."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.api_keys import generate_api_key
from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.api.deps import require_permission
from app.core.security import hash_password, verify_password
from app.models.entities import (
    ApiKey,
    AuditLog,
    Consent,
    Customer,
    Organization,
    OrganizationUser,
    User,
)
from app.schemas.schemas import (
    ApiKeyCreate,
    ApiKeyCreateOut,
    ApiKeyOut,
    CustomerOut,
    MessageOut,
    OrganizationCreate,
    OrganizationDashboardOut,
    OrganizationLoginRequest,
    OrganizationLoginResponse,
    OrganizationOut,
    OrganizationUpdate,
    OrganizationUserCreate,
    OrganizationUserOut,
    TenantSettingsUpdate,
)
from app.services.audit import log_audit

settings = get_settings()
router = APIRouter(prefix="/organizations", tags=["organizations"])

ORG_AUTH_CONTEXT = "org-auth"

# Mirrors STAFF_SUBJECT_TYPE (app/core/security.py): an explicit subject-type
# claim, independent of ctx/type, so `_require_org_user` fails closed rather
# than resolving `sub` against `organization_users` on ctx/type alone.
ORG_SUBJECT_TYPE = "org_user"

VALID_ORG_ROLES = {"org_admin", "org_viewer", "jobhub_admin", "jobhub_viewer", "codex_admin", "codex_viewer", "skilllearn_admin", "skilllearn_viewer"}


def _create_org_token(user_id: int, username: str, org_id: int, role: str) -> str:
    """Mint an organization-portal access token.

    R3-02 applied RFC 8725's "every token carries `iss`/`aud`, every
    verifier checks them explicitly" to the staff and context schemes but
    skipped this one, so org tokens carried neither claim. `jti` is minted
    here for the same reason it is on staff tokens: it is what
    app/core/token_revocation.py revokes, and it makes two tokens issued in
    the same wall-clock second for the same user distinguishable.
    """
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "sub_type": ORG_SUBJECT_TYPE,
        "username": username,
        "org_id": org_id,
        "role": role,
        "type": "org-access",
        "ctx": ORG_AUTH_CONTEXT,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE_ORG,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode_org_token(token: str) -> dict | None:
    """Verify an org token to the same standard as `app.core.security`'s
    `decode_token` plus the staff call sites' own `aud` check.

    `issuer=` is enforced by the library; `options={"verify_aud": False}`
    is required for the same reason it is in `decode_token` - python-jose
    raises JWTClaimsError for any token that CONTAINS an `aud` when the
    caller passes no `audience=`, so leaving it on would make every token
    this function's own minter produces undecodable. The explicit
    `JWT_AUDIENCE_ORG` comparison below is therefore the actual enforcement
    point, exactly as on the staff side.

    Breaking change, deliberate and identical in shape to R3-02's: an org
    token minted before this fix carries no `iss`/`aud` and is now
    rejected, so org users signed in at deploy time must log in again once.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER, options={"verify_aud": False},
        )
        if (
            payload.get("ctx") != ORG_AUTH_CONTEXT
            or payload.get("type") != "org-access"
            or payload.get("sub_type") != ORG_SUBJECT_TYPE
            or payload.get("aud") != settings.JWT_AUDIENCE_ORG
        ):
            return None
        from app.core.token_revocation import is_revoked

        if is_revoked(payload.get("jti")):
            return None
        return payload
    except JWTError:
        return None


def _require_org_user(request: Request, db: Session = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_org_token(auth[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.get(OrganizationUser, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")
    return user


# ── Organization-scoped dashboard (read-only) ──────────────────────────────
# NOTE: This MUST be before /{org_id} routes so FastAPI doesn't match "portal" as an ID.

@router.get("/portal/dashboard", response_model=OrganizationDashboardOut)
def org_portal_dashboard(
    db: Session = Depends(get_db),
    user: OrganizationUser = Depends(_require_org_user),
):
    org = db.get(Organization, user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    users = db.query(OrganizationUser).filter(
        OrganizationUser.organization_id == org.id
    ).all()

    source_app = org.code.upper()
    total_customers = db.query(func.count(Customer.id)).filter(
        Customer.source_app == source_app
    ).scalar() or 0

    total_consents = db.query(func.count(Consent.id)).filter(
        Consent.source_app == source_app
    ).scalar() or 0

    active_consents = db.query(func.count(Consent.id)).filter(
        Consent.source_app == source_app,
        Consent.status.in_(["GRANTED", "ACTIVE"]),
    ).scalar() or 0

    status_rows = (
        db.query(Consent.status, func.count(Consent.id))
        .filter(Consent.source_app == source_app)
        .group_by(Consent.status)
        .all()
    )
    consent_summary = [{"status": s, "count": c} for s, c in status_rows]

    recent = (
        db.query(AuditLog)
        .filter(AuditLog.source_app == source_app)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )

    portal_users = db.query(Customer).filter(
        Customer.source_app == source_app
    ).order_by(Customer.created_at.desc()).all()

    return OrganizationDashboardOut(
        organization=OrganizationOut.model_validate(org),
        users=[OrganizationUserOut.model_validate(u) for u in users],
        portal_users=[CustomerOut.model_validate(c) for c in portal_users],
        total_customers=total_customers,
        total_consents=total_consents,
        active_consents=active_consents,
        consent_summary=consent_summary,
        recent_activity=recent,
    )


# ── Admin-only org dashboard (no auth required — admin session) ─────────────

@router.get("/{org_id}/dashboard", response_model=OrganizationDashboardOut)
def org_admin_dashboard(
    org_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    users = db.query(OrganizationUser).filter(
        OrganizationUser.organization_id == org.id
    ).all()

    source_app = org.code.upper()
    total_customers = db.query(func.count(Customer.id)).filter(
        Customer.source_app == source_app
    ).scalar() or 0

    total_consents = db.query(func.count(Consent.id)).filter(
        Consent.source_app == source_app
    ).scalar() or 0

    active_consents = db.query(func.count(Consent.id)).filter(
        Consent.source_app == source_app,
        Consent.status.in_(["GRANTED", "ACTIVE"]),
    ).scalar() or 0

    status_rows = (
        db.query(Consent.status, func.count(Consent.id))
        .filter(Consent.source_app == source_app)
        .group_by(Consent.status)
        .all()
    )
    consent_summary = [{"status": s, "count": c} for s, c in status_rows]

    recent = (
        db.query(AuditLog)
        .filter(AuditLog.source_app == source_app)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )

    portal_users = db.query(Customer).filter(
        Customer.source_app == source_app
    ).order_by(Customer.created_at.desc()).all()

    return OrganizationDashboardOut(
        organization=OrganizationOut.model_validate(org),
        users=[OrganizationUserOut.model_validate(u) for u in users],
        portal_users=[CustomerOut.model_validate(c) for c in portal_users],
        total_customers=total_customers,
        total_consents=total_consents,
        active_consents=active_consents,
        consent_summary=consent_summary,
        recent_activity=recent,
    )


# ── Portal users for an org ────────────────────────────────────────────────

@router.get("/{org_id}/portal-users")
def list_portal_users(
    org_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    source_app = org.code.upper()
    customers = db.query(Customer).filter(
        Customer.source_app == source_app
    ).order_by(Customer.created_at.desc()).all()

    total_customers = len(customers)

    status_rows = (
        db.query(Consent.status, func.count(Consent.id))
        .filter(Consent.source_app == source_app)
        .group_by(Consent.status)
        .all()
    )
    consent_summary = [{"status": s, "count": c} for s, c in status_rows]

    return {
        "customers": [CustomerOut.model_validate(c) for c in customers],
        "total_customers": total_customers,
        "consent_summary": consent_summary,
    }


# ── Admin CRUD ──────────────────────────────────────────────────────────────

@router.get("", response_model=list[OrganizationOut])
def list_organizations(
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    return db.query(Organization).order_by(Organization.name).all()


@router.post("", response_model=OrganizationOut)
def create_organization(
    payload: OrganizationCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    if db.query(Organization).filter(Organization.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Organization code already exists")
    org = Organization(
        name=payload.name, code=payload.code, domain=payload.domain,
        description=payload.description, logo_url=payload.logo_url,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@router.put("/{org_id}", response_model=OrganizationOut)
def update_organization(
    org_id: int,
    payload: OrganizationUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    db.commit()
    db.refresh(org)
    return org


@router.put("/{org_id}/settings", response_model=OrganizationOut)
def update_tenant_settings(
    org_id: int,
    payload: TenantSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    from app.services.audit import log_audit

    log_audit(db, "ORGANIZATION_SETTINGS_UPDATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", reason=f"Tenant settings updated for {org.code}")
    db.commit()
    db.refresh(org)
    return org


@router.get("/{org_id}", response_model=OrganizationOut)
def get_organization(
    org_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


# ── Org user management ─────────────────────────────────────────────────────

@router.get("/{org_id}/users", response_model=list[OrganizationUserOut])
def list_org_users(
    org_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    return db.query(OrganizationUser).filter(
        OrganizationUser.organization_id == org_id
    ).order_by(OrganizationUser.username).all()


@router.post("/{org_id}/users", response_model=OrganizationUserOut)
def create_org_user(
    org_id: int,
    payload: OrganizationUserCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if db.query(OrganizationUser).filter(OrganizationUser.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    role = payload.role or f"{org.code.lower()}_viewer"
    if role not in VALID_ORG_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role '{role}'. Must be one of: {sorted(VALID_ORG_ROLES)}")
    user = OrganizationUser(
        organization_id=org_id,
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        email_search=hmac_digest(payload.email.strip().lower()),
        password_hash=hash_password(payload.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/{org_id}/roles")
def list_org_roles(
    org_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    code = org.code.lower()
    return [
        {"value": f"{code}_admin", "label": f"{org.name} Admin", "description": "Full access to view users and consents, manage org users"},
        {"value": f"{code}_viewer", "label": f"{org.name} Viewer", "description": "Read-only access to view users and consents"},
        {"value": "org_admin", "label": "Organization Admin (Legacy)", "description": "Generic org admin"},
        {"value": "org_viewer", "label": "Organization Viewer (Legacy)", "description": "Generic org viewer"},
    ]


# ── Organization login ──────────────────────────────────────────────────────

@router.post("/auth/login", response_model=OrganizationLoginResponse)
def org_login(payload: OrganizationLoginRequest, db: Session = Depends(get_db)):
    user = db.query(OrganizationUser).filter(
        OrganizationUser.username == payload.username
    ).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    org = db.get(Organization, user.organization_id)
    token = _create_org_token(user.id, user.username, org.id, user.role)

    return OrganizationLoginResponse(
        access_token=token,
        user=OrganizationUserOut.model_validate(user),
        organization=OrganizationOut.model_validate(org),
    )


# ── API key management ───────────────────────────────────────────────────────

@router.get("/{org_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(
    org_id: int, db: Session = Depends(get_db), _user: User = Depends(require_permission("user.manage")),
):
    return db.query(ApiKey).filter(ApiKey.tenant_id == org_id).order_by(ApiKey.created_at.desc()).all()


@router.post("/{org_id}/api-keys", response_model=ApiKeyCreateOut)
def create_api_key(
    org_id: int,
    payload: ApiKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("user.manage")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    existing_key = db.query(ApiKey).filter(ApiKey.tenant_id == org.id).first()
    if not existing_key and not payload.confirm_preexisting_data:
        # R3 "squat-then-claim": this org has never had a key before - the
        # moment a name genuinely becomes "owned". `resolve_tenant_id` (see
        # app/services/tenancy.py) auto-provisions an Organization row the
        # first time ANY traffic names an unseen source_app, including the
        # legacy integration key's caller-chosen one (see
        # integration.py::_reject_legacy_key_claiming_an_owned_tenant, which
        # logs TENANT_AUTO_PROVISIONED_VIA_LEGACY_KEY when that happens) - so
        # an org with no key yet may already have customers a squatter
        # planted under this name. A normal, legitimate first-key issuance
        # (seed_api_keys.py, or an admin creating the org via
        # POST /organizations and issuing its first key right after) never
        # has customers attached at this point, so this never fires for the
        # ordinary case. Refuse by default rather than silently handing the
        # new key owner whatever was already there; confirm_preexisting_data
        # is an explicit, conscious override once the pre-existing data has
        # been reviewed.
        preexisting = db.query(Customer).filter(Customer.source_app == org.code).count()
        if preexisting:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Organization '{org.code}' already has {preexisting} customer record(s) "
                    f"attached before its first API key - if this is unexpected (e.g. not from "
                    f"seed_orgs.py/seed_api_keys.py), investigate before issuing a key. Pass "
                    f"confirm_preexisting_data=true to proceed knowingly."
                ),
            )
    plaintext, prefix, key_hash = generate_api_key(org.code)
    key = ApiKey(
        tenant_id=org.id, name=payload.name, key_prefix=prefix, key_hash=key_hash,
        scopes=payload.scopes, expires_at=payload.expires_at,
    )
    db.add(key)
    db.flush()
    log_audit(db, "API_KEY_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app=org.code, reason=f"API key '{payload.name}' created for {org.code}",
              metadata={"key_id": key.id, "key_prefix": key.key_prefix})
    db.commit()
    db.refresh(key)
    return ApiKeyCreateOut(**ApiKeyOut.model_validate(key).model_dump(), plaintext_key=plaintext)


@router.post("/{org_id}/api-keys/{key_id}/rotate", response_model=ApiKeyCreateOut)
def rotate_api_key(
    org_id: int,
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("user.manage")),
):
    key = db.get(ApiKey, key_id)
    if not key or key.tenant_id != org_id:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Cannot rotate a revoked key")
    org = db.get(Organization, org_id)
    plaintext, prefix, key_hash = generate_api_key(org.code)
    key.key_prefix = prefix
    key.key_hash = key_hash
    key.rotated_at = datetime.now(timezone.utc)
    log_audit(db, "API_KEY_ROTATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app=org.code, reason=f"API key '{key.name}' rotated for {org.code}",
              metadata={"key_id": key.id, "key_prefix": key.key_prefix})
    db.commit()
    db.refresh(key)
    return ApiKeyCreateOut(**ApiKeyOut.model_validate(key).model_dump(), plaintext_key=plaintext)


@router.delete("/{org_id}/api-keys/{key_id}", response_model=MessageOut)
def revoke_api_key(
    org_id: int,
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("user.manage")),
):
    key = db.get(ApiKey, key_id)
    if not key or key.tenant_id != org_id:
        raise HTTPException(status_code=404, detail="API key not found")
    org = db.get(Organization, org_id)
    key.revoked_at = datetime.now(timezone.utc)
    log_audit(db, "API_KEY_REVOKED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app=org.code if org else "", reason=f"API key '{key.name}' revoked",
              metadata={"key_id": key.id, "key_prefix": key.key_prefix})
    db.commit()
    return MessageOut(message="API key revoked")



