import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permission, verify_context_token, get_customer_from_context
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE
from app.models.entities import Consent, Customer, RightsRequest, User
from app.services.audit import log_audit

router = APIRouter(prefix="/rights-requests", tags=["rights-requests"])

DEFAULT_TENANT_ID = 1
RIGHT_REQUEST_TYPES = ["ACCESS", "CORRECTION", "ERASURE", "NOMINATION"]
RIGHT_REQUEST_STATUSES = ["RECEIVED", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "CLOSED", "DENIED"]
SLA_DAYS = 30


class RightsRequestIn(BaseModel):
    customer_id: int
    type: str
    assignee_user_id: int | None = None


class RightsRequestUpdate(BaseModel):
    resolution: str = ""
    assignee_user_id: int | None = None


class RightsRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    customer_id: int
    type: str
    status: str
    received_at: datetime
    acknowledged_at: datetime | None = None
    due_at: datetime | None = None
    closed_at: datetime | None = None
    identity_verified_at: datetime | None = None
    assignee_user_id: int | None = None
    resolution: str
    evidence_ref: str
    created_at: datetime
    updated_at: datetime


def _rr_out(rr: RightsRequest) -> RightsRequestOut:
    return RightsRequestOut(
        id=rr.id, tenant_id=rr.tenant_id, customer_id=rr.customer_id,
        type=rr.type, status=rr.status, received_at=rr.received_at,
        acknowledged_at=rr.acknowledged_at, due_at=rr.due_at,
        closed_at=rr.closed_at, identity_verified_at=rr.identity_verified_at,
        assignee_user_id=rr.assignee_user_id, resolution=rr.resolution,
        evidence_ref=rr.evidence_ref, created_at=rr.created_at, updated_at=rr.updated_at,
    )


@router.get("", response_model=list[RightsRequestOut])
def list_rights_requests(
    type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    overdue: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    q = db.query(RightsRequest).order_by(RightsRequest.created_at.desc())
    if type:
        q = q.filter(RightsRequest.type == type.upper())
    if status:
        q = q.filter(RightsRequest.status == status.upper())
    if overdue is True:
        now = datetime.now(timezone.utc)
        q = q.filter(RightsRequest.due_at.isnot(None), RightsRequest.due_at < now,
                      RightsRequest.status.notin_(["RESOLVED", "CLOSED", "DENIED"]))
    elif overdue is False:
        now = datetime.now(timezone.utc)
        q = q.filter(
            (RightsRequest.due_at.is_(None)) | (RightsRequest.due_at >= now) |
            (RightsRequest.status.in_(["RESOLVED", "CLOSED", "DENIED"]))
        )
    return [_rr_out(rr) for rr in q.limit(200).all()]


@router.get("/{rr_id}", response_model=RightsRequestOut)
def get_rights_request(rr_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    rr = db.get(RightsRequest, rr_id)
    if not rr:
        raise HTTPException(status_code=404, detail="Rights request not found")
    return _rr_out(rr)


@router.post("", response_model=RightsRequestOut)
def create_rights_request(
    payload: RightsRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    if payload.type.upper() not in RIGHT_REQUEST_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of {RIGHT_REQUEST_TYPES}")
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    now = datetime.now(timezone.utc)
    rr = RightsRequest(
        tenant_id=DEFAULT_TENANT_ID,
        customer_id=payload.customer_id,
        type=payload.type.upper(),
        status="RECEIVED",
        received_at=now,
        due_at=now + timedelta(days=SLA_DAYS),
        assignee_user_id=payload.assignee_user_id,
    )
    db.add(rr)
    log_audit(db, "CONSENT_CREATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason=f"Rights request ({payload.type}) created for customer {customer.id}")
    db.commit()
    db.refresh(rr)
    return _rr_out(rr)


@router.post("/intake", response_model=RightsRequestOut)
def intake_rights_request(
    payload: RightsRequestIn,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    ctx_payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, ctx_payload)
    if payload.customer_id and payload.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Context token does not match customer")
    if payload.type.upper() not in RIGHT_REQUEST_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of {RIGHT_REQUEST_TYPES}")
    now = datetime.now(timezone.utc)
    rr = RightsRequest(
        tenant_id=DEFAULT_TENANT_ID,
        customer_id=customer.id,
        type=payload.type.upper(),
        status="RECEIVED",
        received_at=now,
        due_at=now + timedelta(days=SLA_DAYS),
    )
    db.add(rr)
    log_audit(db, "CONSENT_CREATED", actor_username=customer.name, source_app="PORTAL",
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason=f"Rights request ({payload.type}) submitted via portal intake")
    db.commit()
    db.refresh(rr)
    return _rr_out(rr)


@router.put("/{rr_id}/acknowledge", response_model=RightsRequestOut)
def acknowledge_rights_request(
    rr_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    rr = db.get(RightsRequest, rr_id)
    if not rr:
        raise HTTPException(status_code=404, detail="Rights request not found")
    rr.acknowledged_at = datetime.now(timezone.utc)
    rr.status = "ACKNOWLEDGED"
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=rr.customer_id, reason=f"Rights request {rr_id} acknowledged")
    db.commit()
    db.refresh(rr)
    return _rr_out(rr)


@router.put("/{rr_id}/resolve", response_model=RightsRequestOut)
def resolve_rights_request(
    rr_id: int,
    payload: RightsRequestUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    rr = db.get(RightsRequest, rr_id)
    if not rr:
        raise HTTPException(status_code=404, detail="Rights request not found")
    now = datetime.now(timezone.utc)
    rr.status = "RESOLVED"
    rr.closed_at = now
    rr.resolution = payload.resolution
    if rr.type == "ACCESS":
        customer = db.get(Customer, rr.customer_id)
        if customer:
            consents = db.query(Consent).filter(Consent.customer_id == customer.id).all()
            package = {
                "customer_id": customer.id,
                "external_id": customer.external_id,
                "name": customer.name,
                "email": customer.email,
                "consent_count": len(consents),
                "consents": [
                    {
                        "id": c.id, "purpose_code": c.purpose.code if c.purpose else "",
                        "status": c.status, "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                    }
                    for c in consents
                ],
            }
            rr.resolution = f"Access request resolved. Package assembled: {len(consents)} consent records. {payload.resolution}"
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=rr.customer_id, reason=f"Rights request {rr_id} resolved")
    db.commit()
    db.refresh(rr)
    return _rr_out(rr)


@router.put("/{rr_id}/deny", response_model=RightsRequestOut)
def deny_rights_request(
    rr_id: int,
    payload: RightsRequestUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    rr = db.get(RightsRequest, rr_id)
    if not rr:
        raise HTTPException(status_code=404, detail="Rights request not found")
    rr.status = "DENIED"
    rr.closed_at = datetime.now(timezone.utc)
    rr.resolution = payload.resolution or "Denied"
    log_audit(db, "CONSENT_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=rr.customer_id, reason=f"Rights request {rr_id} denied")
    db.commit()
    db.refresh(rr)
    return _rr_out(rr)
