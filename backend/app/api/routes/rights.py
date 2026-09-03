"""R1-06 / R1-11 — rights management: objections, erasure jobs, rights requests."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_customer_from_context, require_permission, verify_context_token
from app.core.database import get_db
from app.core.rbac import PERM_RIGHTS_MANAGE
from app.models.entities import Consent, Customer, ErasureJob, Objection, Tenant, User

router = APIRouter(prefix="/rights", tags=["rights"])


def _resolve_tenant_id(db: Session, customer: Customer) -> int | None:
    """Resolve a tenant for a customer, falling back to source_app match then first tenant."""
    if customer.tenant_id is not None:
        return customer.tenant_id
    if getattr(customer, "source_app", None):
        tenant = db.query(Tenant).filter(Tenant.code == customer.source_app).first()
        if tenant:
            return tenant.id
    tenant = db.query(Tenant).order_by(Tenant.id).first()
    if tenant:
        return tenant.id
    return None


class ObjectionCreate(BaseModel):
    customer_id: int
    purpose_id: int
    source: str = "portal"


class ObjectionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    customer_id: int
    purpose_id: int
    source: str
    status: str
    objected_at: object


class ErasureJobOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    customer_id: int
    trigger: str
    status: str
    scheduled_for: object
    notice_sent_at: object | None
    executed_at: object | None
    evidence_hash: str


@router.post("/objections", response_model=ObjectionOut, dependencies=[Depends(require_permission(PERM_RIGHTS_MANAGE))])
def file_objection(body: ObjectionCreate, db: Session = Depends(get_db)):
    customer = db.get(Customer, body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    obj = Objection(customer_id=body.customer_id, purpose_id=body.purpose_id, source=body.source,
                    tenant_id=_resolve_tenant_id(db, customer))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/objections", response_model=list[ObjectionOut], dependencies=[Depends(require_permission(PERM_RIGHTS_MANAGE))])
def list_objections(
    customer_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Objection).order_by(Objection.id.desc())
    if customer_id:
        q = q.filter(Objection.customer_id == customer_id)
    return q.limit(100).all()


@router.get("/erasure-jobs", response_model=list[ErasureJobOut], dependencies=[Depends(require_permission(PERM_RIGHTS_MANAGE))])
def list_erasure_jobs(
    customer_id: int = Query(default=None),
    status: str = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(ErasureJob).order_by(ErasureJob.id.desc())
    if customer_id:
        q = q.filter(ErasureJob.customer_id == customer_id)
    if status:
        q = q.filter(ErasureJob.status == status)
    return q.limit(100).all()


@router.post("/erasure-jobs/{job_id}/cancel", dependencies=[Depends(require_permission(PERM_RIGHTS_MANAGE))])
def cancel_erasure_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ErasureJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Erasure job not found")
    if job.status not in ["PENDING", "NOTICE_SENT"]:
        raise HTTPException(status_code=400, detail=f"Cannot cancel job in status {job.status}")
    job.status = "CANCELLED"
    db.commit()
    return {"message": "Erasure job cancelled"}


@router.post("/portal/request")
def portal_rights_request(
    body: ObjectionCreate,
    token_payload: dict = Depends(verify_context_token),
    db: Session = Depends(get_db),
):
    customer = get_customer_from_context(db, token_payload)
    obj = Objection(customer_id=customer.id, purpose_id=body.purpose_id, source="portal",
                    tenant_id=_resolve_tenant_id(db, customer))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return ObjectionOut.model_validate(obj)