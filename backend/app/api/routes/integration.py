from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import require_permission, verify_integration_key
from app.api.routes.consents import _consent_out
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import PERM_CONTEXT_USE
from app.core.utils import context_limiter
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentContext,
    ConsentHistory,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
    User,
)
from app.schemas.schemas import (
    AuditEventOut,
    ConsentHistoryOut,
    CustomerContextIn,
    CustomerContextOut,
    CustomerPortalOut,
    MessageOut,
)
from app.services import consent as consent_service
from app.services.audit import log_audit
from app.services.context import create_context_for_customer

settings = get_settings()
router = APIRouter(prefix="/consent", tags=["integration"])


@router.post("/customer-context", response_model=CustomerContextOut, dependencies=[Depends(verify_integration_key)])
def create_customer_context(payload: CustomerContextIn, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not context_limiter.allow(f"ctx:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many context requests")
    request_id = request.headers.get("X-Request-ID")
    return create_context_for_customer(
        db,
        customer_id=payload.customer_id,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        status=payload.status,
        source_app=payload.source_app or "EXTERNAL_APP",
        created_by="integration",
        request_id=request_id,
    )


def _ensure_source_consent_matrix(db: Session, customer: Customer, source_app: str) -> None:
    """Materialize the consent matrix for a customer, owned by the website/app source.

    - A row already recorded through this source is reused.
    - A placeholder row ("" / "SYSTEM" / "UI" - created by admin tooling, not by a real
      website) is adopted into this source so a fresh registration always sees its matrix.
    - A row owned by a DIFFERENT real source is left untouched and a NEW independent row
      is created for this source (per-website consent records).
    """
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    for purpose in purposes:
        pv = consent_service.get_current_purpose_version(purpose)
        cat_ids = pv.data_category_ids or []
        act_ids = pv.processing_activity_ids or []
        categories = db.query(DataCategory).filter(DataCategory.id.in_(cat_ids)).all() if cat_ids else []
        activities = db.query(ProcessingActivity).filter(ProcessingActivity.id.in_(act_ids)).all() if act_ids else []
        for dc in categories:
            for pa in activities:
                same_source = (
                    db.query(Consent)
                    .filter(
                        Consent.customer_id == customer.id,
                        Consent.purpose_id == purpose.id,
                        Consent.data_category_id == dc.id,
                        Consent.processing_activity_id == pa.id,
                        Consent.source_app == source_app,
                    )
                    .first()
                )
                if same_source:
                    continue
                existing = (
                    db.query(Consent)
                    .filter(
                        Consent.customer_id == customer.id,
                        Consent.purpose_id == purpose.id,
                        Consent.data_category_id == dc.id,
                        Consent.processing_activity_id == pa.id,
                    )
                    .first()
                )
                if existing and existing.source_app in ("", "SYSTEM", "UI"):
                    existing.source_app = source_app
                    continue
                consent_service.get_or_create_consent(
                    db, customer, purpose, dc, pa,
                    actor_username="integration", source_app=source_app, exact_source=True,
                )


@router.get("/context/consume/{context_token}", response_model=CustomerPortalOut)
def consume_context(context_token: str, db: Session = Depends(get_db)):
    """Data-subject portal: the context token itself is the credential (no platform auth).

    Returns ONLY the data belonging to this customer AND recorded through this source
    (consents, their history, and related audit events). Everything else is hidden.
    """
    from app.core.security import decode_token

    context = db.query(ConsentContext).filter(ConsentContext.token == context_token).first()
    payload = decode_token(context_token) if context else None
    if not context or not payload:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    if not context.is_active:
        raise HTTPException(status_code=401, detail="Consent context has already been consumed")
    if context.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Consent context has expired")
    customer = db.get(Customer, context.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    source_app = context.source_app
    _ensure_source_consent_matrix(db, customer, source_app)

    consents = (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.source_app == source_app)
        .order_by(Consent.purpose_id.asc(), Consent.data_category_id.asc(), Consent.processing_activity_id.asc())
        .all()
    )
    consent_ids = [c.id for c in consents]
    history = (
        db.query(ConsentHistory)
        .filter(ConsentHistory.consent_id.in_(consent_ids))
        .order_by(ConsentHistory.created_at.desc())
        .all()
        if consent_ids
        else []
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.customer_id == customer.id, AuditLog.source_app == source_app)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
        .all()
    )

    status_counts: dict[str, int] = {}
    for c in consents:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1

    context.is_active = False
    context.consumed_at = datetime.now(timezone.utc)
    log_audit(db, "CONTEXT_CONSUMED", actor_username="integration", source_app=source_app,
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason="Consent context consumed - opening customer portal",
              request_id=context.request_id,
              metadata={"context_id": context.id})
    db.commit()

    return CustomerPortalOut(
        customer=customer,
        source_app=source_app,
        status_counts=status_counts,
        consents=[_consent_out(c) for c in consents],
        history=[ConsentHistoryOut.model_validate(h) for h in history],
        audit=[AuditEventOut.model_validate(e) for e in audit],
    )


@router.get("/context/status/{context_token}", response_model=MessageOut)
def context_status(context_token: str, db: Session = Depends(get_db),
                   _: User = Depends(require_permission(PERM_CONTEXT_USE))):
    context = db.query(ConsentContext).filter(ConsentContext.token == context_token).first()
    if not context:
        raise HTTPException(status_code=404, detail="Context not found")
    if context.consumed_at:
        return MessageOut(message="CONSUMED")
    if context.expires_at < datetime.now(timezone.utc):
        return MessageOut(message="EXPIRED")
    return MessageOut(message="VALID")
