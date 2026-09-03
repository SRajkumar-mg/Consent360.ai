from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_customer_from_context, verify_context_token
from app.core.database import get_db
from app.models.entities import Consent, DataCategory, ProcessingActivity, Purpose
from app.schemas.schemas import PortalActionIn, PortalActionOut, PortalOverview, PortalPurposeOut
from app.services import consent as consent_service

router = APIRouter(prefix="/portal", tags=["customer-portal"])

ACTIVE_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")


def _resolve_customer(x_context_token: str, db: Session):
    payload = verify_context_token(x_context_token)
    return get_customer_from_context(db, payload)


def _is_active(consent: Consent, now: datetime) -> bool:
    if consent.status not in ACTIVE_STATUSES:
        return False
    if consent.expires_at is not None and consent.expires_at <= now:
        return False
    return True


def _ensure_consent_matrix(db: Session, customer, actor_username: str = "system") -> None:
    """Lazily materialize NOT_REQUESTED consent rows for every active purpose x category x activity."""
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    for purpose in purposes:
        try:
            pv = consent_service.get_current_purpose_version(purpose)
        except Exception:
            continue
        cat_ids = pv.data_category_ids or []
        act_ids = pv.processing_activity_ids or []
        categories = db.query(DataCategory).filter(DataCategory.id.in_(cat_ids)).all() if cat_ids else []
        activities = db.query(ProcessingActivity).filter(ProcessingActivity.id.in_(act_ids)).all() if act_ids else []
        for dc in categories:
            for pa in activities:
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
                if not existing:
                    consent_service.get_or_create_consent(
                        db, customer, purpose, dc, pa, actor_username=actor_username, source_app="PORTAL"
                    )


@router.get("/overview", response_model=PortalOverview)
def portal_overview(
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer = _resolve_customer(x_context_token, db)
    _ensure_consent_matrix(db, customer, actor_username=customer.name)
    now = datetime.now(timezone.utc)

    consents = db.query(Consent).filter(Consent.customer_id == customer.id).all()
    by_purpose: dict[int, list[Consent]] = {}
    for c in consents:
        by_purpose.setdefault(c.purpose_id, []).append(c)

    purposes = (
        db.query(Purpose)
        .filter(Purpose.is_active.is_(True), Purpose.requires_consent.is_(True))
        .order_by(Purpose.name)
        .all()
    )
    items: list[PortalPurposeOut] = []
    for purpose in purposes:
        pv = consent_service.get_current_purpose_version(purpose)
        cs = by_purpose.get(purpose.id, [])
        total = len(cs)
        granted = sum(1 for c in cs if _is_active(c, now))
        if total and granted == total:
            status = "GRANTED"
        elif granted > 0:
            status = "PARTIAL"
        else:
            status = "NOT_GRANTED"
        items.append(
            PortalPurposeOut(
                code=purpose.code,
                name=purpose.name,
                description=pv.description or purpose.description,
                legal_basis=pv.legal_basis,
                requires_consent=pv.requires_consent,
                retention_period_days=pv.retention_period_days,
                consent_text=pv.consent_text,
                translations=pv.translations or {},
                status=status,
                granted_count=granted,
                total_count=total,
            )
        )
    return PortalOverview(customer=customer, purposes=items)


@router.post("/grant", response_model=PortalActionOut)
def portal_grant(
    payload: PortalActionIn,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer = _resolve_customer(x_context_token, db)
    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail=f"Purpose {payload.purpose_code} not found")

    _ensure_consent_matrix(db, customer, actor_username=customer.name)
    consents = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
        )
        .all()
    )
    affected = 0
    for c in consents:
        if _is_active(c, datetime.now(timezone.utc)):
            continue
        granted = consent_service.grant_consent(
            db,
            c,
            reason=f"Granted by customer {customer.name} via self-service portal",
            actor_username=customer.name,
            source_app="PORTAL",
            collection_method="PORTAL",
        )
        activated = consent_service.activate_consent(
            db,
            granted,
            actor_username=customer.name,
            source_app="PORTAL",
        )
        if activated:
            affected += 1
    return PortalActionOut(
        purpose_code=purpose.code,
        action="granted",
        affected=affected,
        message=f"Granted consent for {purpose.name} ({affected} records)",
    )


@router.post("/withdraw", response_model=PortalActionOut)
def portal_withdraw(
    payload: PortalActionIn,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer = _resolve_customer(x_context_token, db)
    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail=f"Purpose {payload.purpose_code} not found")

    consents = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
        )
        .all()
    )
    affected = 0
    for c in consents:
        if not _is_active(c, datetime.now(timezone.utc)):
            continue
        consent_service.withdraw_consent(
            db,
            c,
            reason=f"Withdrawn by customer {customer.name} via self-service portal",
            actor_username=customer.name,
            source_app="PORTAL",
        )
        affected += 1
    return PortalActionOut(
        purpose_code=purpose.code,
        action="withdrawn",
        affected=affected,
        message=f"Withdrawn consent for {purpose.name} ({affected} records)",
    )
