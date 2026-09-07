"""CRM directory service - standalone backend for the CRM portal (port 8001).

This is the "client-side" service: it maintains the CRM's own customer
directory. Everything consent-related is delegated to the Consent Management
Platform's own logic (app.api.routes.crm) - the popup APIs (consent
preferences, consent context, purge) live there.

`_purge_consent_profile` used to reach that logic over HTTP, authenticated
with the legacy shared `INTEGRATION_API_KEY` - a loopback call to the very
same backend process in the only deployment docs/ARCHITECTURE.md documents as actually
used (`app.main:app` mounts both this router and `app.api.routes.crm`'s), so
the network round trip bought nothing but an extra dependency on the one
credential R3-01 wants eliminated. It now calls the shared purge logic
in-process instead, which needs no API key at all: both routers already
read/write the same database through the same `SessionLocal`. This assumes
`app.crm_app` (the optional standalone entrypoint some deployments use for
just this router) is pointed at that same database - true of every
deployment this repository documents.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import find_by_search_digest, hmac_digest
from app.models.entities import CrmCustomer, Customer
from app.schemas.schemas import CrmCustomerLoginIn, CrmCustomerOut, CrmDirectoryLoginOut
from app.services.context import (
    assert_identifiable_principal,
    create_context_for_customer,
    derive_customer_id,
)
from app.services.tenancy import resolve_customer, resolve_tenant_id

settings = get_settings()
router = APIRouter(prefix="/crm", tags=["crm-directory"])

CRM_SOURCE_APP = "CRM_PORTAL"


def _purge_consent_profile(db: Session, email: str) -> None:
    """Anonymise the customer's consent profile on the Consent Management
    Platform. No consent data is touched by this service - see the module
    docstring for why this is now an in-process call rather than a loopback
    HTTP request.

    Scoped to CRM_FAMILY_SOURCE_APPS, never ANY_TENANT: the route that
    calls this (``DELETE /crm/customers/{customer_id}`` below) takes NO
    credential at all, so — exactly like ``_find_linked_customer`` and
    ``_ensure_consent360_customer`` above — the blast radius of an
    anonymous caller here must be structurally confined to the three demo
    sites this directory actually serves. Before this scope was made
    explicit, the omitted (default) scope silently fell back to
    ``ANY_TENANT`` inside ``purge_customer_by_email_core``, which let an
    unauthenticated caller anonymise a real customer of ANY other,
    unrelated, independently-authenticated tenant merely by knowing their
    email — see the R3 fix report ("require a credential and a tenant to
    purge a customer") for the live exploit this closes.
    """
    if not email:
        return
    from app.api.routes.crm import CRM_FAMILY_SOURCE_APPS, purge_customer_by_email_core

    try:
        purge_customer_by_email_core(
            db, email, actor_username="crm_directory", actor_type="SYSTEM",
            scope=CRM_FAMILY_SOURCE_APPS,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            return
        raise HTTPException(status_code=502, detail="Consent platform rejected the purge request") from exc


@router.post("/login", response_model=CrmDirectoryLoginOut)
def crm_login(payload: CrmCustomerLoginIn, db: Session = Depends(get_db)):
    """Register (or update) a customer in the CRM directory.

    Also ensures a corresponding Customer record exists on the Consent360
    platform so the admin panel reflects the customer immediately.

    This endpoint takes NO credential at all - it is the public signup/login
    flow for three demo frontends (CRM, Codex, SkillLearn) that have no
    server-side secret to hold (see app.api.routes.crm's module comment). The
    trust being assumed is explicit and narrow: anyone can claim to be any
    customer of one of THESE THREE known, fixed sites (an accepted, existing
    weakness of this specific demo - the sites share one directory by design),
    but before R3 the ``source_app`` field was a completely free string, so
    the same anonymous call could claim to be ANY tenant in the whole system
    - including one with its own real, independently-authenticated API key -
    and overwrite that tenant's real customer's name and phone. Constraining
    source_app to CRM_FAMILY_SOURCE_APPS makes the blast radius exactly the
    three sites this endpoint was ever meant to serve, not "every tenant".

    R1-12/B-04: it is also the one customer-creating endpoint with NO
    credential, so it is exactly where a fabricated data principal must be
    refused. ``assert_identifiable_principal`` is the same guard
    ``create_context_for_customer`` applies - called here, not restated, so
    the two cannot drift (see its docstring for the live reproduction that
    showed this path accepting an address the integration API refused)."""
    from app.api.routes.crm import CRM_FAMILY_SOURCE_APPS

    if payload.source_app not in CRM_FAMILY_SOURCE_APPS:
        raise HTTPException(
            status_code=422,
            detail=f"source_app must be one of {sorted(CRM_FAMILY_SOURCE_APPS)}",
        )
    email = payload.email.strip().lower()
    phone = payload.phone.strip() if payload.phone else None
    # Before anything is written: neither the CrmCustomer directory row nor
    # the platform Customer row `_ensure_consent360_customer` derives from it
    # may exist for an unidentified visitor.
    assert_identifiable_principal(email=email, phone=phone)
    # Matched against every retained search-digest key, not just the
    # current one: a miss here is what makes this endpoint create a
    # SECOND CrmCustomer (and, via _ensure_consent360_customer, a second
    # Customer) for a person who already exists - see
    # app/core/encryption.py::_HmacKeyRing.
    customer = find_by_search_digest(db.query(CrmCustomer), CrmCustomer.email_search, email)
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

    The lookup is scoped to CRM_FAMILY_SOURCE_APPS (not ANY_TENANT): this
    preserves the intentional shared identity across the three sibling
    sites, while making it structurally impossible to reach a customer
    belonging to a real, unrelated tenant outside that family - see
    crm_login's docstring and app.api.routes.crm's module comment.
    """
    from app.api.routes.crm import CRM_FAMILY_SOURCE_APPS

    email = (crm_customer.email or "").strip().lower()
    if not email:
        return
    try:
        external_id = derive_customer_id(email=email, phone=crm_customer.phone)
        existing = resolve_customer(db, source_app=CRM_FAMILY_SOURCE_APPS, email=email)
        if not existing:
            existing = resolve_customer(db, source_app=CRM_FAMILY_SOURCE_APPS, external_id=external_id)
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
                tenant_id=resolve_tenant_id(db, source_app),
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
    _purge_consent_profile(db, (customer.email or "").strip().lower())
    db.delete(customer)
    db.commit()
    return {"deleted": True, "customer_id": customer_id}