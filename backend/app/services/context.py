"""Shared consent-context creation used by the integration API and the CRM portal.

Both entry points identify-or-create the consent platform's Customer reference
and mint a short-lived context token that lets the customer manage their own
consents in the customer portal without a staff account.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.encryption import hmac_digest
from app.core.security import create_context_token
from app.models.entities import ConsentContext, Customer
from app.schemas.schemas import CustomerContextOut
from app.services.audit import log_audit

settings = get_settings()


def derive_customer_id(*, customer_id: str | None = None, email: str | None = None, phone: str | None = None) -> str:
    """Resolve a stable external customer id when the calling app has no id to send."""
    if customer_id:
        return customer_id
    seed = None
    if email:
        seed = "email:" + email.strip().lower()
    elif phone:
        seed = "phone:" + phone.strip()
    if not seed:
        raise HTTPException(status_code=422, detail="customer_id, email or phone is required")
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
    return f"CUST-{digest}"


def create_context_for_customer(
    db: Session,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    status: str | None = None,
    source_app: str = "EXTERNAL_APP",
    created_by: str = "integration",
    customer_id: str | None = None,
    request_id: str | None = None,
) -> CustomerContextOut:
    """Identify (or create) the consent-platform Customer and mint a context token.

    Every path that produces a portal‑usable token must go through identity
    verification (R3-05).  If the customer record has no ``identity_verified_at``
    timestamp, verification is required and a new token must not be issued.
    """
    if customer_id:
        customer = db.query(Customer).filter(Customer.external_id_search == hmac_digest(customer_id)).first()
    else:
        external_id = derive_customer_id(email=email, phone=phone)
        customer = None
        if email:
            customer = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
        if not customer:
            customer = db.query(Customer).filter(Customer.external_id_search == hmac_digest(external_id)).first()
    if not customer:
        if not customer_id:
            external_id = derive_customer_id(email=email, phone=phone)
        customer = Customer(
            external_id=external_id,
            external_id_search=hmac_digest(external_id),
            name=name,
            email=email or "",
            email_search=hmac_digest(email) if email else None,
            phone=phone or "",
            status=status or "ACTIVE",
            source_app=source_app,
        )
        db.add(customer)
        db.flush()
        log_audit(db, "CUSTOMER_CREATED", actor_username=created_by, source_app=source_app,
                  customer_id=customer.id, customer_external_id=customer.external_id,
                  reason="Customer reference created from integration context")
    else:
        if name:
            customer.name = name
        if email:
            customer.email = email
            customer.email_search = hmac_digest(email)
        if phone:
            customer.phone = phone
        if status:
            customer.status = status
        # R3-05: If customer exists but is not verified, require verification
        # before issuing a portal‑usable token.
        if not customer.identity_verified_at:
            raise HTTPException(
                status_code=403,
                detail="Customer identity must be verified via OTP or fiduciary assertion before a context token can be issued",
            )
        log_audit(db, "CUSTOMER_UPDATED", actor_username=created_by, source_app=source_app,
                  customer_id=customer.id, customer_external_id=customer.external_id,
                  reason="Customer context refreshed from integration")

    token = create_context_token(customer.id, source_app, request_id)
    context = ConsentContext(
        customer_id=customer.id,
        token=token,
        source_app=source_app,
        request_id=request_id,
        created_by=created_by,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES),
    )
    db.add(context)
    db.flush()
    log_audit(db, "CONTEXT_CREATED", actor_username=created_by, source_app=source_app,
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason="Secure consent context created for customer",
              request_id=request_id,
              metadata={"context_id": context.id, "source": created_by})
    db.commit()
    db.refresh(context)

    return CustomerContextOut(
        context_token=token,
        context_id=context.id,
        customer_id=customer.external_id,
        name=customer.name,
        expires_in_minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES,
        source_app=context.source_app,
        ui_url=f"/consent/context/{token}",
        request_id=request_id,
    )
