"""R3-10: Consent validation API and Consent Manager API."""
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission, _verify_integration_key_with_db, TenantContext
from app.core.database import get_db
from app.core.rbac import PERM_INTEGRATION
from app.core.security import sign_payload
from app.models.entities import (
    Consent,
    ConsentArtefact,
    ConsentDecisionLog,
    Customer,
    DataCategory,
    FiduciaryOnboarding,
    ProcessingActivity,
    Purpose,
    User,
)
from app.services.decision_engine import evaluate_decision
from app.services.audit import log_audit

router = APIRouter(prefix="/decisions", tags=["decisions"])


class DecisionEvaluateRequest(BaseModel):
    customer_id: int
    purpose_code: str
    data_category_code: str
    processing_activity_code: str


class DecisionEvaluateResponse(BaseModel):
    decision: str
    reason: str
    allowed: bool
    latency_ms: float


class ConsentArtefactCreate(BaseModel):
    customer_id: int
    purpose_code: str
    data_category_code: str
    processing_activity_code: str
    subject: str = ""
    purpose: str = ""
    method: str = "EXPLICIT"
    expiry: datetime | None = None
    data_life: str = "SINGLE_USE"
    frequency: str = "ONE_TIME"
    access_mode: str = "PUSH"
    notification_url: str = ""


class ConsentArtefactOut(BaseModel):
    artefact_id: str
    version: int
    customer_id: int
    purpose_code: str
    data_category_code: str
    processing_activity_code: str
    subject: str
    purpose: str
    method: str
    expiry: datetime | None
    revocable: bool
    data_life: str
    frequency: str
    access_mode: str
    status: str
    payload_hash: str
    signature: str
    created_at: datetime


class FiduciaryOnboardingOut(BaseModel):
    fiduciary_id: str
    name: str
    contact: str
    status: str
    onboarded_at: datetime | None


@router.post("/evaluate", response_model=DecisionEvaluateResponse)
def evaluate_decision_endpoint(
    payload: DecisionEvaluateRequest,
    request: Request,
    db: Session = Depends(get_db),
    tenant_ctx: TenantContext = Depends(_verify_integration_key_with_db),
):
    start_time = time.time()

    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")

    data_category = db.query(DataCategory).filter(DataCategory.code == payload.data_category_code).first()
    if not data_category:
        raise HTTPException(status_code=404, detail="Data category not found")

    processing_activity = db.query(ProcessingActivity).filter(
        ProcessingActivity.code == payload.processing_activity_code
    ).first()
    if not processing_activity:
        raise HTTPException(status_code=404, detail="Processing activity not found")

    result = evaluate_decision(
        db,
        customer,
        purpose,
        data_category,
        processing_activity,
        requested_by="api",
        source_app=tenant_ctx.source_app,
        persist=True,
        request_id=request.headers.get("X-Request-ID"),
    )

    latency_ms = (time.time() - start_time) * 1000

    return DecisionEvaluateResponse(
        decision=result.decision,
        reason=result.reason,
        allowed=result.allowed,
        latency_ms=round(latency_ms, 2),
    )


@router.post("/consent-artefacts", response_model=ConsentArtefactOut, status_code=201)
def create_consent_artefact(
    payload: ConsentArtefactCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_INTEGRATION)),
):
    artefact_id = f"CA-{uuid.uuid4().hex[:16].upper()}"

    payload_data = {
        "artefact_id": artefact_id,
        "customer_id": payload.customer_id,
        "purpose_code": payload.purpose_code,
        "data_category_code": payload.data_category_code,
        "processing_activity_code": payload.processing_activity_code,
    }
    payload_hash = sign_payload(payload_data)
    signature = sign_payload({**payload_data, "hash": payload_hash})

    artefact = ConsentArtefact(
        artefact_id=artefact_id,
        customer_id=payload.customer_id,
        purpose_code=payload.purpose_code,
        data_category_code=payload.data_category_code,
        processing_activity_code=payload.processing_activity_code,
        subject=payload.subject,
        purpose=payload.purpose,
        method=payload.method,
        expiry=payload.expiry,
        data_life=payload.data_life,
        frequency=payload.frequency,
        access_mode=payload.access_mode,
        notification_url=payload.notification_url,
        payload_hash=payload_hash,
        signature=signature,
    )
    db.add(artefact)
    db.commit()
    db.refresh(artefact)

    return ConsentArtefactOut(
        artefact_id=artefact.artefact_id,
        version=artefact.version,
        customer_id=artefact.customer_id,
        purpose_code=artefact.purpose_code,
        data_category_code=artefact.data_category_code,
        processing_activity_code=artefact.processing_activity_code,
        subject=artefact.subject,
        purpose=artefact.purpose,
        method=artefact.method,
        expiry=artefact.expiry,
        revocable=artefact.revocable,
        data_life=artefact.data_life,
        frequency=artefact.frequency,
        access_mode=artefact.access_mode,
        status=artefact.status,
        payload_hash=artefact.payload_hash,
        signature=artefact.signature,
        created_at=artefact.created_at,
    )


@router.get("/consent-artefacts/{artefact_id}", response_model=ConsentArtefactOut)
def read_consent_artefact(
    artefact_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_INTEGRATION)),
):
    artefact = db.query(ConsentArtefact).filter(ConsentArtefact.artefact_id == artefact_id).first()
    if not artefact:
        raise HTTPException(status_code=404, detail="Consent artefact not found")
    return ConsentArtefactOut(
        artefact_id=artefact.artefact_id,
        version=artefact.version,
        customer_id=artefact.customer_id,
        purpose_code=artefact.purpose_code,
        data_category_code=artefact.data_category_code,
        processing_activity_code=artefact.processing_activity_code,
        subject=artefact.subject,
        purpose=artefact.purpose,
        method=artefact.method,
        expiry=artefact.expiry,
        revocable=artefact.revocable,
        data_life=artefact.data_life,
        frequency=artefact.frequency,
        access_mode=artefact.access_mode,
        status=artefact.status,
        payload_hash=artefact.payload_hash,
        signature=artefact.signature,
        created_at=artefact.created_at,
    )


@router.post("/consent-artefacts/{artefact_id}/withdraw")
def withdraw_consent_artefact(
    artefact_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_INTEGRATION)),
):
    artefact = db.query(ConsentArtefact).filter(ConsentArtefact.artefact_id == artefact_id).first()
    if not artefact:
        raise HTTPException(status_code=404, detail="Consent artefact not found")
    if artefact.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Artefact is not active")
    artefact.status = "WITHDRAWN"
    artefact.withdrawn_at = datetime.now(timezone.utc)
    db.commit()
    return {"withdrawn": True, "artefact_id": artefact_id}


@router.get("/consent-artefacts", response_model=list[ConsentArtefactOut])
def list_consent_artefacts(
    customer_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_INTEGRATION)),
):
    q = db.query(ConsentArtefact).order_by(ConsentArtefact.created_at.desc())
    if customer_id:
        q = q.filter(ConsentArtefact.customer_id == customer_id)
    if status:
        q = q.filter(ConsentArtefact.status == status)
    artefacts = q.all()
    return [
        ConsentArtefactOut(
            artefact_id=a.artefact_id, version=a.version, customer_id=a.customer_id,
            purpose_code=a.purpose_code, data_category_code=a.data_category_code,
            processing_activity_code=a.processing_activity_code, subject=a.subject,
            purpose=a.purpose, method=a.method, expiry=a.expiry, revocable=a.revocable,
            data_life=a.data_life, frequency=a.frequency, access_mode=a.access_mode,
            status=a.status, payload_hash=a.payload_hash, signature=a.signature,
            created_at=a.created_at,
        )
        for a in artefacts
    ]


@router.get("/fiduciaries", response_model=list[FiduciaryOnboardingOut])
def list_fiduciaries(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_INTEGRATION))):
    return db.query(FiduciaryOnboarding).order_by(FiduciaryOnboarding.id).all()


@router.get("/disclosures")
def get_disclosures(tenant_id: int, db: Session = Depends(get_db)):
    from app.models.entities import Disclosure
    disc = db.query(Disclosure).filter(Disclosure.tenant_id == tenant_id, Disclosure.is_active.is_(True)).first()
    return disc.content if disc else {"promoters": [], "directors": [], "shareholders_2pct": []}
