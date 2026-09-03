"""Organization management routes - CRUD, auth, and org-scoped dashboards."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.api.deps import require_permission
from app.core.security import hash_password, verify_password
from app.models.entities import (
    AuditLog,
    Consent,
    Customer,
    Organization,
    OrganizationUser,
    User,
)
from app.schemas.schemas import (
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
)

settings = get_settings()
router = APIRouter(prefix="/organizations", tags=["organizations"])

ORG_AUTH_CONTEXT = "org-auth"

VALID_ORG_ROLES = {"org_admin", "org_viewer", "jobhub_admin", "jobhub_viewer", "codex_admin", "codex_viewer", "skilllearn_admin", "skilllearn_viewer"}


def _create_org_token(user_id: int, username: str, org_id: int, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "org_id": org_id,
        "role": role,
        "type": "org-access",
        "ctx": ORG_AUTH_CONTEXT,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode_org_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("ctx") != ORG_AUTH_CONTEXT or payload.get("type") != "org-access":
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



