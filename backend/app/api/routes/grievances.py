import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permission, verify_context_token, get_customer_from_context
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE
from app.models.entities import Customer, Grievance, User
from app.services.audit import log_audit

router = APIRouter(prefix="/grievances", tags=["grievances"])

DEFAULT_TENANT_ID = 1
SLA_DAYS = 30


def _generate_reference_no() -> str:
    year = datetime.now(timezone.utc).year
    seq = random.randint(100000, 999999)
    return f"GRV-{year}-{seq}"


class GrievanceIn(BaseModel):
    customer_id: int
    category: str = "general"
    description: str = ""


class GrievanceUpdate(BaseModel):
    resolution_summary: str = ""
    feedback: str = ""


class GrievanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    customer_id: int
    reference_no: str
    category: str
    description: str
    status: str
    received_at: datetime
    acknowledged_at: datetime | None = None
    due_at: datetime | None = None
    escalated_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_summary: str
    feedback: str
    created_at: datetime
    updated_at: datetime


def _grv_out(g: Grievance) -> GrievanceOut:
    return GrievanceOut(
        id=g.id, tenant_id=g.tenant_id, customer_id=g.customer_id,
        reference_no=g.reference_no, category=g.category,
        description=g.description, status=g.status,
        received_at=g.received_at, acknowledged_at=g.acknowledged_at,
        due_at=g.due_at, escalated_at=g.escalated_at,
        resolved_at=g.resolved_at, resolution_summary=g.resolution_summary,
        feedback=g.feedback, created_at=g.created_at, updated_at=g.updated_at,
    )


@router.get("", response_model=list[GrievanceOut])
def list_grievances(
    status: str | None = Query(default=None),
    overdue: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    q = db.query(Grievance).order_by(Grievance.created_at.desc())
    if status:
        q = q.filter(Grievance.status == status.upper())
    if overdue is True:
        now = datetime.now(timezone.utc)
        q = q.filter(Grievance.due_at.isnot(None), Grievance.due_at < now,
                      Grievance.status.notin_(["RESOLVED", "DENIED"]))
    elif overdue is False:
        now = datetime.now(timezone.utc)
        q = q.filter(
            (Grievance.due_at.is_(None)) | (Grievance.due_at >= now) |
            (Grievance.status.in_(["RESOLVED", "DENIED"]))
        )
    return [_grv_out(g) for g in q.limit(200).all()]


@router.get("/{reference_no}", response_model=GrievanceOut)
def get_grievance(reference_no: str, db: Session = Depends(get_db),
                  user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    g = db.query(Grievance).filter(Grievance.reference_no == reference_no).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return _grv_out(g)


@router.post("", response_model=GrievanceOut)
def submit_grievance(
    payload: GrievanceIn,
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    now = datetime.now(timezone.utc)
    ref = _generate_reference_no()
    while db.query(Grievance).filter(Grievance.reference_no == ref).first():
        ref = _generate_reference_no()
    g = Grievance(
        tenant_id=DEFAULT_TENANT_ID,
        customer_id=payload.customer_id,
        reference_no=ref,
        category=payload.category,
        description=payload.description,
        status="RECEIVED",
        received_at=now,
        due_at=now + timedelta(days=SLA_DAYS),
    )
    db.add(g)
    log_audit(db, "CONSENT_CREATED", actor_username="portal", source_app="PORTAL",
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason=f"Grievance {ref} submitted via portal")
    db.commit()
    db.refresh(g)
    return _grv_out(g)


@router.post("/staff", response_model=GrievanceOut)
def staff_submit_grievance(
    payload: GrievanceIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    now = datetime.now(timezone.utc)
    ref = _generate_reference_no()
    while db.query(Grievance).filter(Grievance.reference_no == ref).first():
        ref = _generate_reference_no()
    g = Grievance(
        tenant_id=DEFAULT_TENANT_ID,
        customer_id=payload.customer_id,
        reference_no=ref,
        category=payload.category,
        description=payload.description,
        status="RECEIVED",
        received_at=now,
        due_at=now + timedelta(days=SLA_DAYS),
    )
    db.add(g)
    log_audit(db, "CONSENT_CREATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason=f"Grievance {ref} created by staff")
    db.commit()
    db.refresh(g)
    return _grv_out(g)


@router.put("/{grievance_id}/acknowledge", response_model=GrievanceOut)
def acknowledge_grievance(
    grievance_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    g = db.get(Grievance, grievance_id)
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    g.acknowledged_at = datetime.now(timezone.utc)
    g.status = "ACKNOWLEDGED"
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=g.customer_id, reason=f"Grievance {g.reference_no} acknowledged")
    db.commit()
    db.refresh(g)
    return _grv_out(g)


@router.put("/{grievance_id}/escalate", response_model=GrievanceOut)
def escalate_grievance(
    grievance_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    g = db.get(Grievance, grievance_id)
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    g.escalated_at = datetime.now(timezone.utc)
    g.status = "ESCALATED"
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=g.customer_id,
              reason=f"Grievance {g.reference_no} escalated - DPO notified")
    db.commit()
    db.refresh(g)
    return _grv_out(g)


@router.put("/{grievance_id}/resolve", response_model=GrievanceOut)
def resolve_grievance(
    grievance_id: int,
    payload: GrievanceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    g = db.get(Grievance, grievance_id)
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    g.resolved_at = datetime.now(timezone.utc)
    g.status = "RESOLVED"
    g.resolution_summary = payload.resolution_summary
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=g.customer_id, reason=f"Grievance {g.reference_no} resolved")
    db.commit()
    db.refresh(g)
    return _grv_out(g)


@router.put("/{grievance_id}/feedback", response_model=GrievanceOut)
def grievance_feedback(
    grievance_id: int,
    payload: GrievanceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    g = db.get(Grievance, grievance_id)
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    if g.status != "RESOLVED":
        raise HTTPException(status_code=400, detail="Feedback can only be provided after grievance is resolved")
    g.feedback = payload.feedback
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=g.customer_id, reason=f"Principal feedback added for grievance {g.reference_no}")
    db.commit()
    db.refresh(g)
    return _grv_out(g)
