"""R1-01 tenant and per-tenant disclosure configuration, plus
R3-01 tenant-bound API key management."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import PERM_TENANT_MANAGE
from app.core.security import generate_api_key, hash_api_key
from app.models.entities import ApiKey, Tenant, User
from app.services.audit import log_audit

settings = get_settings()
router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=256)
    domain: str = ""
    dpo_name: str = ""
    dpo_contact: str = ""
    withdraw_url: str = ""
    rights_url: str = ""
    grievance_url: str = ""
    board_complaint_url: str = ""
    grievance_response_days: int = 30
    default_language: str = "en"
    environment: str = "development"


class TenantUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    dpo_name: str | None = None
    dpo_contact: str | None = None
    withdraw_url: str | None = None
    rights_url: str | None = None
    grievance_url: str | None = None
    board_complaint_url: str | None = None
    grievance_response_days: int | None = None
    default_language: str | None = None
    environment: str | None = None
    is_active: bool | None = None


class TenantOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    code: str
    name: str
    domain: str
    dpo_name: str
    dpo_contact: str
    withdraw_url: str
    rights_url: str
    grievance_url: str
    board_complaint_url: str
    grievance_response_days: int
    default_language: str
    environment: str
    is_active: bool
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(default="default", max_length=128)
    scopes: str = "integration.use,context.use"
    expires_in_days: int | None = Field(default=None, ge=1)


class ApiKeyOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    name: str
    scopes: str
    created_at: datetime
    rotated_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    is_active: bool


class ApiKeyCreateResponse(BaseModel):
    key: str
    key_info: ApiKeyOut


@router.get("", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    return db.query(Tenant).order_by(Tenant.code).all()


@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(body: TenantCreate, db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    if db.query(Tenant).filter(Tenant.code == body.code).first():
        raise HTTPException(status_code=409, detail="Tenant code already exists")
    t = Tenant(**body.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: int, db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(tenant_id: int, body: TenantUpdate, db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return t


# --------------------------------------------------------------------------- #
#  R3-01: Tenant-bound API key management
# --------------------------------------------------------------------------- #

@router.get("/{tenant_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(tenant_id: int, db: Session = Depends(get_db),
                  _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return db.query(ApiKey).filter(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc()).all()


@router.post("/{tenant_id}/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
def create_api_key(tenant_id: int, payload: ApiKeyCreate, db: Session = Depends(get_db),
                   _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    raw_key = generate_api_key()
    expires_at = None
    if payload.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
    api_key = ApiKey(
        tenant_id=tenant_id,
        key_hash=hash_api_key(raw_key),
        name=payload.name,
        scopes=payload.scopes,
        expires_at=expires_at,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    log_audit(db, "API_KEY_CREATED", actor_username="system", source_app=tenant.code,
              reason=f"API key '{payload.name}' created for tenant {tenant.code}")
    return ApiKeyCreateResponse(
        key=raw_key,
        key_info=ApiKeyOut.model_validate(api_key),
    )


@router.post("/{tenant_id}/api-keys/{key_id}/rotate", response_model=ApiKeyCreateResponse)
def rotate_api_key(tenant_id: int, key_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    old_key = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id).first()
    if not old_key:
        raise HTTPException(status_code=404, detail="API key not found")
    raw_key = generate_api_key()
    old_key.rotated_at = datetime.now(timezone.utc)
    old_key.is_active = False
    new_key = ApiKey(
        tenant_id=tenant_id,
        key_hash=hash_api_key(raw_key),
        name=old_key.name,
        scopes=old_key.scopes,
        expires_at=old_key.expires_at,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)
    log_audit(db, "API_KEY_ROTATED", actor_username="system", source_app=tenant.code,
              reason=f"API key {key_id} rotated for tenant {tenant.code}")
    return ApiKeyCreateResponse(
        key=raw_key,
        key_info=ApiKeyOut.model_validate(new_key),
    )


@router.delete("/{tenant_id}/api-keys/{key_id}")
def revoke_api_key(tenant_id: int, key_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    api_key = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.revoked_at = datetime.now(timezone.utc)
    api_key.is_active = False
    db.commit()
    log_audit(db, "API_KEY_REVOKED", actor_username="system", source_app=tenant.code,
              reason=f"API key {key_id} revoked for tenant {tenant.code}")
    return {"deleted": True, "key_id": key_id}
