"""CRM directory service - standalone backend for the CRM portal (port 8001).

This is the "client-side" service: it maintains the CRM's own customer
directory. Everything consent-related is delegated to the Consent Management
Platform APIs (port 8000), which is the only service allowed to touch consent
records - the popup APIs (consent preferences, consent context) live there.
"""

import logging
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.models.entities import CrmCustomer, Customer
from app.schemas.schemas import CrmCustomerLoginIn, CrmCustomerOut, CrmDirectoryLoginOut
from app.services.context import create_context_for_customer, derive_customer_id

settings = get_settings()
router = APIRouter(prefix="/crm", tags=["crm-directory"])

CRM_SOURCE_APP = "CRM_PORTAL"


def _purge_consent_profile(email: str) -> None:
    """Ask the Consent Management Platform to delete the customer's consent
    profile (its own records). No consent data is touched by this service."""
    if not email:
        return
    url = f"{settings.CONSENT_API_URL}/crm/customers/by-email/{quote(email, safe='')}"
    request = Request(url, method="DELETE", headers={"X-API-Key": settings.INTEGRATION_API_KEY})
    try:
        with urlopen(request, timeout=15) as response:
            if response.status >= 400:
                raise HTTPException(status_code=502, detail="Consent platform rejected the purge request")
    except HTTPError as exc:
        if exc.code == 404:
            return
        raise HTTPException(status_code=502, detail=f"Consent platform purge failed ({exc.code})") from exc


@router.post("/login", response_model=CrmDirectoryLoginOut)
def crm_login(payload: CrmCustomerLoginIn, db: Session = Depends(get_db)):
    """Register (or update) a customer in the CRM directory.

    Also ensures a corresponding Customer record exists on the Consent360
    platform so the admin panel reflects the customer immediately."""
    email = payload.email.strip().lower()
    phone = payload.phone.strip() if payload.phone else None
    customer = db.query(CrmCustomer).filter(CrmCustomer.email_search == hmac_digest(email)).first()
    created = False
    if not customer:
        customer = CrmCustomer(
            name=payload.name.strip(),
            email=email,
            email_search=hmac_digest(email),
            phone=phone,
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
        created = True
    else:
        customer.name = payload.name.strip()
        if phone:
            customer.phone = phone
        db.commit()
        db.refresh(customer)

    # Sync to Consent360: ensure a Customer record exists so the admin portal
    # shows this user.  Errors are logged but never block the CRM login.
    _ensure_consent360_customer(db, customer, payload.source_app)

    return CrmDirectoryLoginOut(customer=CrmCustomerOut.model_validate(customer), created=created)


def _ensure_consent360_customer(db: Session, crm_customer: CrmCustomer, source_app: str = CRM_SOURCE_APP) -> None:
    """Ensure a Customer reference exists in Consent360's customers table.

    This is the single source of truth for admin portal visibility. If the
    Customer was never created (e.g. Consent360 was down during a previous
    login), this function creates it.
    """
    email = (crm_customer.email or "").strip().lower()
    if not email:
        return
    try:
        external_id = derive_customer_id(email=email, phone=crm_customer.phone)
        existing = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
        if not existing:
            existing = db.query(Customer).filter(Customer.external_id_search == hmac_digest(external_id)).first()
        if not existing:
            cust = Customer(
                external_id=external_id,
                external_id_search=hmac_digest(external_id),
                name=crm_customer.name,
                email=email,
                email_search=hmac_digest(email),
                phone=crm_customer.phone or "",
                status="ACTIVE",
                source_app=source_app,
            )
            db.add(cust)
            db.commit()
        else:
            # Update if name or phone changed
            changed = False
            if existing.name != crm_customer.name:
                existing.name = crm_customer.name
                changed = True
            if crm_customer.phone and existing.phone != crm_customer.phone:
                existing.phone = crm_customer.phone
                changed = True
            if changed:
                db.commit()
    except Exception as exc:
        logging.getLogger("crm_directory").warning(
            "Failed to sync Customer to Consent360 for %s: %s", email, exc
        )


@router.get("/customers", response_model=list[CrmCustomerOut])
def list_crm_customers(db: Session = Depends(get_db)):
    return db.query(CrmCustomer).order_by(CrmCustomer.created_at.desc()).all()


@router.delete("/customers/{customer_id}")
def delete_crm_customer(customer_id: int, db: Session = Depends(get_db)):
    """Delete a CRM directory record. The customer's consent profile on the
    Consent Management Platform is purged through its API first."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    _purge_consent_profile((customer.email or "").strip().lower())
    db.delete(customer)
    db.commit()
    return {"deleted": True, "customer_id": customer_id}