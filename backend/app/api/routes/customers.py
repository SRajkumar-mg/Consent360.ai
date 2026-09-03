from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.core.rbac import PERM_CUSTOMER_VIEW, PERM_CUSTOMER_MANAGE
from app.models.entities import Customer, User
from app.schemas.schemas import CustomerOut, CustomerUpdate
from app.services.audit import log_audit

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
def list_customers(
    search: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CUSTOMER_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(Customer)
    if scope:
        q = q.filter(Customer.source_app == scope)
    if search:
        like = f"%{search}%".lower()
        all_customers = q.order_by(Customer.external_id).all()
        q = None
        filtered = [
            c for c in all_customers
            if like in (c.external_id or "").lower()
            or like in (c.name or "").lower()
            or like in (c.email or "").lower()
        ]
        return filtered[offset:offset + limit]
    q = q.order_by(Customer.external_id).limit(limit).offset(offset)
    return q.all()


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_permission(PERM_CUSTOMER_VIEW))):
    scope = get_org_scope(user)
    customer = db.query(Customer).filter(Customer.external_id_search == hmac_digest(customer_id)).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if scope and customer.source_app != scope:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: str,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CUSTOMER_MANAGE)),
):
    scope = get_org_scope(user)
    customer = db.query(Customer).filter(Customer.external_id_search == hmac_digest(customer_id)).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if scope and customer.source_app != scope:
        raise HTTPException(status_code=404, detail="Customer not found")

    updates = payload.model_dump(exclude_unset=True)
    if "email" in updates and updates["email"] is not None:
        email = updates["email"].strip().lower()
        existing = db.query(Customer).filter(
            Customer.email_search == hmac_digest(email),
            Customer.id != customer.id,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use by another customer")
        updates["email_search"] = hmac_digest(email)
    if "status" in updates and updates["status"] is not None:
        updates["status"] = updates["status"].upper()

    for field, value in updates.items():
        setattr(customer, field, value)

    log_audit(db, "CUSTOMER_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason=f"Customer {customer_id} updated")
    db.commit()
    db.refresh(customer)
    return customer
