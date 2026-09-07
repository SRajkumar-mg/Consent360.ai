"""R1-08/C-06: objection register for s.7(a) voluntary-provision purposes.

s.7(a) is a lawful gateway only "where she has not indicated that she does
not consent" - this module is where that indication gets recorded, for both
a staff-assisted channel and the principal's own self-service one (mirroring
the X-Context-Token pattern /portal/* already uses).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_customer_from_context, get_org_scope, require_permission, verify_context_token
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE, PERM_CONSENT_VIEW
from app.models.entities import Consent, ConsentContext, Objection, Purpose, User
from app.schemas.schemas import ObjectionIn, ObjectionOut, ObjectionResolveIn, ObjectionSelfIn
from app.services.audit import log_audit
from app.services.tenancy import ANY_TENANT, resolve_customer, resolve_tenant_id

router = APIRouter(prefix="/objections", tags=["objections"])


def _out(o: Objection) -> ObjectionOut:
    out = ObjectionOut.model_validate(o)
    out.purpose_code = o.purpose.code if o.purpose else ""
    return out


def _require_s7a_purpose(db: Session, purpose_code: str) -> Purpose:
    purpose = db.query(Purpose).filter(Purpose.code == purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    if purpose.legal_basis != "S7_A":
        raise HTTPException(
            status_code=422,
            detail="Objections apply only to purposes processed under s.7(a) (voluntary provision, not objected to)",
        )
    return purpose


def _create(db: Session, customer, purpose: Purpose, reason: str, actor_username: str, source_app: str) -> Objection:
    existing = (
        db.query(Objection)
        .filter(Objection.customer_id == customer.id, Objection.purpose_id == purpose.id, Objection.status == "ACTIVE")
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="An active objection already exists for this customer and purpose")

    consent = (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.purpose_id == purpose.id)
        .order_by(Consent.id.desc())
        .first()
    )
    objection = Objection(
        tenant_id=resolve_tenant_id(db, source_app), customer_id=customer.id, purpose_id=purpose.id,
        consent_id=consent.id if consent else None, reason=reason, status="ACTIVE",
        source_app=source_app, actor_username=actor_username, objected_at=datetime.now(timezone.utc),
    )
    db.add(objection)
    db.flush()
    log_audit(db, "OBJECTION_RECORDED", actor_username=actor_username, source_app=source_app,
              customer_id=customer.id, customer_external_id=customer.external_id,
              consent_id=consent.id if consent else None, purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Objection recorded for purpose {purpose.code} (s.7(a))")
    db.commit()
    db.refresh(objection)
    return objection


@router.post("", response_model=ObjectionOut, status_code=201)
def create_objection(payload: ObjectionIn, db: Session = Depends(get_db),
                     current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=payload.customer_external_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    purpose = _require_s7a_purpose(db, payload.purpose_code)
    objection = _create(db, customer, purpose, payload.reason, current_user.username, customer.source_app)
    return _out(objection)


@router.post("/me", response_model=ObjectionOut, status_code=201)
def create_my_objection(payload: ObjectionSelfIn, x_context_token: str = Header(..., alias="X-Context-Token"), db: Session = Depends(get_db)):
    context_payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, context_payload)
    context = db.query(ConsentContext).filter(ConsentContext.token == x_context_token).first()
    if not context or context.source_app != customer.source_app:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    purpose = _require_s7a_purpose(db, payload.purpose_code)
    objection = _create(db, customer, purpose, payload.reason, "principal", customer.source_app)
    return _out(objection)


@router.get("/me", response_model=list[ObjectionOut])
def my_objections(x_context_token: str = Header(..., alias="X-Context-Token"), db: Session = Depends(get_db)):
    context_payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, context_payload)
    objections = (
        db.query(Objection)
        .filter(Objection.customer_id == customer.id, Objection.source_app == customer.source_app)
        .order_by(Objection.objected_at.desc())
        .all()
    )
    return [_out(o) for o in objections]


@router.get("", response_model=list[ObjectionOut])
def list_objections(
    customer_external_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(Objection)
    if customer_external_id:
        customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_external_id)
        if not customer:
            return []
        q = q.filter(Objection.customer_id == customer.id)
    elif scope:
        q = q.filter(Objection.source_app == scope)
    return [_out(o) for o in q.order_by(Objection.objected_at.desc()).limit(200).all()]


@router.get("/{objection_id}", response_model=ObjectionOut)
def get_objection(objection_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_permission(PERM_CONSENT_VIEW))):
    scope = get_org_scope(user)
    objection = db.get(Objection, objection_id)
    if not objection or (scope and objection.source_app != scope):
        raise HTTPException(status_code=404, detail="Objection not found")
    return _out(objection)


@router.post("/{objection_id}/resolve", response_model=ObjectionOut)
def resolve_objection(objection_id: int, payload: ObjectionResolveIn, db: Session = Depends(get_db),
                      current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    objection = db.get(Objection, objection_id)
    if not objection or (scope and objection.source_app != scope):
        raise HTTPException(status_code=404, detail="Objection not found")
    if objection.status == "RESOLVED":
        raise HTTPException(status_code=409, detail="Objection is already resolved")
    objection.status = "RESOLVED"
    objection.resolved_at = datetime.now(timezone.utc)
    objection.resolved_by = current_user.username
    objection.resolution_note = payload.resolution_note
    log_audit(db, "OBJECTION_RESOLVED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id), source_app=objection.source_app,
              customer_id=objection.customer_id, purpose_id=objection.purpose_id,
              reason=f"Objection {objection.id} resolved: {payload.resolution_note}")
    db.commit()
    db.refresh(objection)
    return _out(objection)
