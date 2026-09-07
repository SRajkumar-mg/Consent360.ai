from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_CUSTOMER_VIEW
from app.models.entities import Customer, User
from app.schemas.schemas import CustomerOut
from app.services.tenancy import ANY_TENANT, resolve_customer

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
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer
