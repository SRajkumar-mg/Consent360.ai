"""R3-07: Processor register management endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE
from app.models.entities import Processor, ProcessorAlert, Tenant, User
from app.services.processor_service import (
    check_escalations,
    notify_processors_for_erasure,
    send_cease_processing_alert,
)
from app.core.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/processors", tags=["processors"])


class ProcessorCreate(BaseModel):
    tenant_id: int
    name: str = Field(min_length=1, max_length=256)
    type: str = "PROCESSOR"
    country: str = "IN"
    contact: str = ""
    contract_ref: str | None = None
    contract_start: datetime | None = None
    contract_end: datetime | None = None
    security_clauses: str = ""
    erasure_clause: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""


class ProcessorOut(BaseModel):
    id: int
    tenant_id: int
    name: str
    type: str
    country: str
    contact: str
    contract_ref: str | None
    contract_start: datetime | None
    contract_end: datetime | None
    is_active: bool
    created_at: datetime


class ProcessorAlertOut(BaseModel):
    id: int
    processor_id: int
    alert_type: str
    status: str
    retry_count: int
    sent_at: datetime | None
    acknowledged_at: datetime | None
    escalated_at: datetime | None
    created_at: datetime


class ContractCoverageReport(BaseModel):
    total_processors: int
    covered_processors: int
    coverage_percentage: float


@router.get("", response_model=list[ProcessorOut])
def list_processors(
    tenant_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    q = db.query(Processor).order_by(Processor.id)
    if tenant_id:
        q = q.filter(Processor.tenant_id == tenant_id)
    return q.all()


@router.post("", response_model=ProcessorOut, status_code=201)
def create_processor(
    payload: ProcessorCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    tenant = db.get(Tenant, payload.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    processor = Processor(**payload.model_dump())
    db.add(processor)
    db.commit()
    db.refresh(processor)
    return processor


@router.get("/{processor_id}/alerts", response_model=list[ProcessorAlertOut])
def list_processor_alerts(
    processor_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    return db.query(ProcessorAlert).filter(ProcessorAlert.processor_id == processor_id).order_by(ProcessorAlert.created_at.desc()).all()


@router.post("/notify-erasure")
def notify_erasure(
    customer_id: int,
    processor_ids: list[int],
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    alerts = notify_processors_for_erasure(db, customer_id, processor_ids)
    return {"alerts_sent": len(alerts)}


@router.post("/check-escalations")
def check_processor_escalations(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    escalated = check_escalations(db, settings.PROCESSOR_ALERT_SLA_HOURS)
    return {"escalated": escalated}


@router.get("/reports/contract-coverage", response_model=ContractCoverageReport)
def contract_coverage_report(
    tenant_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    now = datetime.now(timezone.utc)
    q = db.query(Processor).filter(Processor.is_active.is_(True))
    if tenant_id:
        q = q.filter(Processor.tenant_id == tenant_id)
    all_processors = q.all()
    total = len(all_processors)
    covered = sum(
        1 for p in all_processors
        if p.contract_ref and p.contract_start and p.contract_end
        and p.contract_start <= now <= p.contract_end
    )
    return ContractCoverageReport(
        total_processors=total,
        covered_processors=covered,
        coverage_percentage=(covered / total * 100) if total > 0 else 0,
    )
