from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE
from app.models.entities import TenantSettings, User
from app.services.audit import log_audit

router = APIRouter(prefix="/tenant-settings", tags=["tenant-settings"])
public_router = APIRouter(prefix="/public", tags=["tenant-settings"])

DEFAULT_TENANT_ID = 1


class TenantSettingsIn(BaseModel):
    tenant_code: str
    dpo_name: str = ""
    dpo_contact: str = ""
    withdraw_url: str = ""
    rights_url: str = ""
    grievance_url: str = ""
    board_complaint_url: str = ""
    grievance_response_days: int = 30
    default_language: str = "en"


class TenantSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_code: str
    dpo_name: str
    dpo_contact: str
    withdraw_url: str
    rights_url: str
    grievance_url: str
    board_complaint_url: str
    grievance_response_days: int
    default_language: str
    created_at: datetime
    updated_at: datetime


class PrivacyContactOut(BaseModel):
    dpo_name: str
    dpo_contact: str
    grievance_url: str
    board_complaint_url: str


class RightsInfoOut(BaseModel):
    rights_url: str
    grievance_url: str
    grievance_response_days: int


def _get_or_404(db: Session, tenant_code: str) -> TenantSettings:
    ts = db.query(TenantSettings).filter(TenantSettings.tenant_code == tenant_code).first()
    if not ts:
        raise HTTPException(status_code=404, detail=f"Tenant settings for '{tenant_code}' not found")
    return ts


@router.get("", response_model=list[TenantSettingsOut])
def list_tenant_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    return db.query(TenantSettings).order_by(TenantSettings.tenant_code).all()


@router.get("/{tenant_code}", response_model=TenantSettingsOut)
def get_tenant_settings_public(tenant_code: str, db: Session = Depends(get_db)):
    return _get_or_404(db, tenant_code)


@public_router.get("/{tenant_code}/privacy-contact", response_model=PrivacyContactOut)
def get_privacy_contact(tenant_code: str, db: Session = Depends(get_db)):
    ts = _get_or_404(db, tenant_code)
    return PrivacyContactOut(
        dpo_name=ts.dpo_name,
        dpo_contact=ts.dpo_contact,
        grievance_url=ts.grievance_url,
        board_complaint_url=ts.board_complaint_url,
    )


@public_router.get("/{tenant_code}/rights", response_model=RightsInfoOut)
def get_rights_info(tenant_code: str, db: Session = Depends(get_db)):
    ts = _get_or_404(db, tenant_code)
    return RightsInfoOut(
        rights_url=ts.rights_url,
        grievance_url=ts.grievance_url,
        grievance_response_days=ts.grievance_response_days,
    )


@router.post("", response_model=TenantSettingsOut)
def upsert_tenant_settings(
    payload: TenantSettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    existing = db.query(TenantSettings).filter(TenantSettings.tenant_code == payload.tenant_code).first()
    if existing:
        for field in payload.model_fields:
            setattr(existing, field, getattr(payload, field))
        log_audit(db, "TENANT_SETTINGS_UPDATED", actor_username=user.username,
                  actor_role=user.role.name if user.role else "", source_app="UI",
                  reason=f"Tenant settings updated for {payload.tenant_code}")
        db.commit()
        db.refresh(existing)
        return existing
    ts = TenantSettings(**payload.model_dump())
    db.add(ts)
    log_audit(db, "TENANT_SETTINGS_CREATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              reason=f"Tenant settings created for {payload.tenant_code}")
    db.commit()
    db.refresh(ts)
    return ts
