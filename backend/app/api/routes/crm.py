"""Consent Management Platform - CRM consent APIs.

Serves the consent popups used by the CRM portal (port 8005) and any future
client website: cookie-category preference storage (per customer), mirroring
of those choices onto the platform's consent records, and consent-context
minting. The CRM's own customer directory lives in its separate backend
service (port 8001); consent data is only ever touched here.
"""

from urllib.parse import quote
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import verify_integration_key
from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentContext,
    ConsentEvidence,
    ConsentHistory,
    CrmCustomer,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
)
from app.schemas.schemas import CrmCustomerOut, CrmLoginOut
from app.services import consent as consent_service
from app.services.context import create_context_for_customer, derive_customer_id

settings = get_settings()
router = APIRouter(prefix="/crm", tags=["crm-consent"])

CRM_SOURCE_APP = "CRM_PORTAL"

COOKIE_CATEGORY_TO_PURPOSE = {
    "necessary": "strictly_necessary",
    "functional": "functional",
    "analytics": "analytics",
    "advertising": "advertising",
}


def _link_customer(db: Session, crm_customer: CrmCustomer) -> Customer | None:
    """Find or create the Consent360 Customer reference for a CRM customer.

    If no Customer exists yet (e.g. Consent360 was unreachable at login time),
    this function creates one so consent sync always works.
    """
    email = (crm_customer.email or "").strip().lower()
    if not email:
        return None
    target = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    if not target:
        target = db.query(Customer).filter(Customer.external_id_search == hmac_digest(derive_customer_id(email=email))).first()
    if not target:
        # Create the Customer reference so consent sync works
        external_id = derive_customer_id(email=email, phone=crm_customer.phone)
        target = Customer(
            external_id=external_id,
            external_id_search=hmac_digest(external_id),
            name=crm_customer.name,
            email=email,
            email_search=hmac_digest(email),
            phone=crm_customer.phone or "",
            status="ACTIVE",
            source_app=CRM_SOURCE_APP,
        )
        db.add(target)
        db.flush()
    return target


def _sync_consent_preferences(db: Session, crm_customer: CrmCustomer, categories: dict) -> None:
    """Mirror the CRM cookie-category choices onto the consent platform's consent records
    so the admin portal reflects the customer's preferences (grant / withdraw per purpose)."""
    customer = _link_customer(db, crm_customer)
    if not customer:
        return
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    for purpose in purposes:
        category_key = next((k for k, code in COOKIE_CATEGORY_TO_PURPOSE.items() if code == purpose.code), None)
        if not category_key:
            continue
        enabled = bool(categories.get(category_key, False))
        try:
            purpose_version = consent_service.get_current_purpose_version(purpose)
        except Exception:
            continue
        evidence_reference = f"crm-prefs-{customer.id}-{datetime.now(timezone.utc).timestamp():.0f}"
        for category_id in purpose_version.data_category_ids:
            data_category = db.get(DataCategory, category_id)
            for activity_id in purpose_version.processing_activity_ids:
                activity = db.get(ProcessingActivity, activity_id)
                consent, _ = consent_service.get_or_create_consent(
                    db, customer, purpose, data_category, activity,
                    actor_username="crm", source_app=CRM_SOURCE_APP, collection_method="UI",
                )
                if enabled:
                    if consent.status not in ("GRANTED", "ACTIVE"):
                        consent_service.grant_consent(
                            db, consent, reason="Consent granted via CRM cookie preferences",
                            actor_username="crm", source_app=CRM_SOURCE_APP, collection_method="UI",
                            affirmative_action="CLICK", evidence_reference=evidence_reference,
                        )
                        consent_service.activate_consent(db, consent, reason="Consent granted via CRM cookie preferences",
                                                         actor_username="crm", source_app=CRM_SOURCE_APP)
                elif consent.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED"):
                    consent_service.withdraw_consent(
                        db, consent, reason="Consent withdrawn via CRM cookie preferences",
                        actor_username="crm", source_app=CRM_SOURCE_APP,
                    )


def _purge_customer_data(db: Session, target: Customer) -> None:
    """Delete every consent-platform record belonging to a customer.

    R1-10: protected by the statutory retention floor — an administrative purge
    cannot delete records younger than the configured floor. (Principal-initiated
    erasure goes through the erasure engine, which allows the exception.)
    """
    from app.services.retention_floors import assert_above_retention_floor, days_since
    age_days = days_since(target.created_at)
    assert_above_retention_floor(db, "customers", age_days)
    consent_ids = [c.id for c in db.query(Consent).filter(Consent.customer_id == target.id).all()]
    if consent_ids:
        db.query(AuditLog).filter(AuditLog.consent_id.in_(consent_ids)).delete(synchronize_session=False)
        db.query(ConsentEvidence).filter(ConsentEvidence.consent_id.in_(consent_ids)).delete(synchronize_session=False)
        db.query(ConsentHistory).filter(ConsentHistory.consent_id.in_(consent_ids)).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.customer_id == target.id).delete(synchronize_session=False)
    db.query(Consent).filter(Consent.customer_id == target.id).delete(synchronize_session=False)
    db.query(ConsentContext).filter(ConsentContext.customer_id == target.id).delete(synchronize_session=False)
    db.delete(target)


class ConsentPreferencesIn(BaseModel):
    lang: str = "en"
    categories: dict[str, bool] = {}


def _consent_portal_url(context_token: str) -> str:
    """Consent portal URL carrying the context token and the return address."""
    return_url = quote(f"{settings.CRM_PORTAL_URL}/users", safe="")
    return f"{settings.CONSENT_PORTAL_URL}/portal/consent?ctx={context_token}&return_url={return_url}"


def _issue_context(db: Session, customer: CrmCustomer) -> CrmLoginOut:
    ctx = create_context_for_customer(
        db,
        name=customer.name,
        email=customer.email,
        phone=customer.phone or None,
        status="ACTIVE",
        source_app=CRM_SOURCE_APP,
        created_by="crm",
    )
    return CrmLoginOut(
        customer=CrmCustomerOut.model_validate(customer),
        created=False,
        context_token=ctx.context_token,
        consent_portal_url=_consent_portal_url(ctx.context_token),
        expires_in_minutes=ctx.expires_in_minutes,
    )


@router.post("/customers/{customer_id}/consent-context", response_model=CrmLoginOut)
def crm_customer_consent_context(customer_id: int, db: Session = Depends(get_db)):
    """Mint a consent context for an existing CRM customer (Manage Consent flow)."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    return _issue_context(db, customer)


@router.get("/customers/{customer_id}/consent-preferences")
def get_crm_consent_preferences(customer_id: int, db: Session = Depends(get_db)):
    """Return the cookie preferences saved for a CRM customer (per-customer, not browser-wide)."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    return {"preferences": customer.consent_preferences or {}}


@router.put("/customers/{customer_id}/consent-preferences")
def put_crm_consent_preferences(customer_id: int, payload: ConsentPreferencesIn, db: Session = Depends(get_db)):
    """Save cookie preferences for a CRM customer and mirror them onto the
    consent platform so the admin portal reflects the same choices."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    prefs = {"lang": payload.lang, "categories": payload.categories, "at": datetime.now(timezone.utc).isoformat()}
    customer.consent_preferences = prefs
    db.commit()
    _sync_consent_preferences(db, customer, payload.categories)
    return {"saved": True, "preferences": customer.consent_preferences}


@router.delete("/customers/by-email/{email}", dependencies=[Depends(verify_integration_key)])
def purge_customer_by_email(email: str, db: Session = Depends(get_db)):
    """Purge a customer's consent profile (consent records, history, evidence,
    contexts and audit entries) - called by the CRM directory service (or any
    client) with the integration API key when the customer is deleted there."""
    normalized = email.strip().lower()
    target = db.query(Customer).filter(Customer.email_search == hmac_digest(normalized)).first()
    if not target:
        target = db.query(Customer).filter(Customer.external_id_search == hmac_digest(derive_customer_id(email=normalized))).first()
    if not target:
        raise HTTPException(status_code=404, detail="Customer not found")
    external_id = target.external_id
    _purge_customer_data(db, target)
    db.commit()
    return {"deleted": True, "external_id": external_id}