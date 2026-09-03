"""R1-01 tenant and per-tenant disclosure configuration."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_TENANT_MANAGE
from app.models.entities import Tenant, User

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    dpo_name: str = Field(min_length=1, max_length=256)
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
    dpo_name: str | None = None
    dpo_contact: str | None = None
    withdraw_url: str | None = None
    rights_url: str | None = None
    grievance_url: str | None = None
    board_complaint_url: str | None = None
    grievance_response_days: int | None = None
    default_language: str | None = None
    environment: str | None = None


class TenantOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    code: str
    name: str
    dpo_name: str
    dpo_contact: str
    withdraw_url: str
    rights_url: str
    grievance_url: str
    board_complaint_url: str
    grievance_response_days: int
    default_language: str
    environment: str


router_details = router


@router.get("", response_model=list[TenantOut], dependencies=[Depends(require_permission(PERM_TENANT_MANAGE))])
def list_tenants(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    return db.query(Tenant).order_by(Tenant.code).all()


@router.post("", response_model=TenantOut, dependencies=[Depends(require_permission(PERM_TENANT_MANAGE))])
def create_tenant(body: TenantCreate, db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    if db.query(Tenant).filter(Tenant.code == body.code).first():
        raise HTTPException(status_code=409, detail="Tenant code already exists")
    t = Tenant(
        code=body.code, name=body.name, dpo_name=body.dpo_name, dpo_contact=body.dpo_contact,
        withdraw_url=body.withdraw_url, rights_url=body.rights_url, grievance_url=body.grievance_url,
        board_complaint_url=body.board_complaint_url, grievance_response_days=body.grievance_response_days,
        default_language=body.default_language, environment=body.environment,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/{tenant_id}", response_model=TenantOut, dependencies=[Depends(require_permission(PERM_TENANT_MANAGE))])
def get_tenant(tenant_id: int, db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_TENANT_MANAGE))):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t


@router.patch("/{tenant_id}", response_model=TenantOut, dependencies=[Depends(require_permission(PERM_TENANT_MANAGE))])
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