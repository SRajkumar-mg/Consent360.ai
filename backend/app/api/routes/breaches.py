"""R3-08: Breach management endpoints."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE
from app.models.entities import Breach, BreachExtensionRequest, BreachNotification, User
from app.services.breach_service import (
    create_breach,
    generate_board_report,
    notify_principal,
)

router = APIRouter(prefix="/breaches", tags=["breaches"])


class BreachCreate(BaseModel):
    tenant_id: int
    breach_type: str = Field(pattern=r"^(CONFIDENTIALITY|INTEGRITY|AVAILABILITY)$")
    detected_at: datetime
    aware_at: datetime
    nature: str = ""
    extent: str = ""
    timing: str = ""
    location: str = ""
    likely_impact: str = ""
    cause: str = ""
    mitigation: str = ""
    remedial_measures: str = ""
    findings_on_actor: str = ""


class BreachOut(BaseModel):
    id: int
    tenant_id: int
    reference_no: str
    breach_type: str
    detected_at: datetime
    aware_at: datetime
    nature: str
    extent: str
    status: str
    created_at: datetime


class BreachNotificationOut(BaseModel):
    id: int
    breach_id: int
    recipient_type: str
    channel: str
    deadline_at: datetime
    sent_at: datetime | None
    acknowledged_at: datetime | None
    status: str


class ExtensionRequestCreate(BaseModel):
    clock_type: str
    reason: str
    new_deadline_at: datetime | None = None


@router.get("", response_model=list[BreachOut])
def list_breaches(
    tenant_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    q = db.query(Breach).order_by(Breach.created_at.desc())
    if tenant_id:
        q = q.filter(Breach.tenant_id == tenant_id)
    if status:
        q = q.filter(Breach.status == status)
    return q.all()


@router.post("", response_model=BreachOut, status_code=201)
def create_breach_endpoint(
    payload: BreachCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    breach = create_breach(db, **payload.model_dump())
    return breach


@router.get("/{breach_id}/notifications", response_model=list[BreachNotificationOut])
def list_breach_notifications(
    breach_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    return db.query(BreachNotification).filter(BreachNotification.breach_id == breach_id).all()


@router.get("/{breach_id}/board-report")
def get_board_report(
    breach_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    breach = db.get(Breach, breach_id)
    if not breach:
        raise HTTPException(status_code=404, detail="Breach not found")
    return generate_board_report(breach)


@router.post("/{breach_id}/notify-principal")
def notify_principal_endpoint(
    breach_id: int,
    customer_id: int,
    recipient_email: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    breach = db.get(Breach, breach_id)
    if not breach:
        raise HTTPException(status_code=404, detail="Breach not found")
    result = notify_principal(db, breach, customer_id, recipient_email)
    return {"sent": result is not None}


@router.post("/{breach_id}/extension-requests")
def create_extension_request(
    breach_id: int,
    payload: ExtensionRequestCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    breach = db.get(Breach, breach_id)
    if not breach:
        raise HTTPException(status_code=404, detail="Breach not found")
    ext = BreachExtensionRequest(
        breach_id=breach_id,
        clock_type=payload.clock_type,
        reason=payload.reason,
        new_deadline_at=payload.new_deadline_at,
    )
    db.add(ext)
    db.commit()
    return {"created": True, "id": ext.id}


@router.put("/{breach_id}/status")
def update_breach_status(
    breach_id: int,
    new_status: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    breach = db.get(Breach, breach_id)
    if not breach:
        raise HTTPException(status_code=404, detail="Breach not found")
    breach.status = new_status
    db.commit()
    return {"updated": True, "status": new_status}
