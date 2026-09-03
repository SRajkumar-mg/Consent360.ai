from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.core.rbac import PERM_CUSTOMER_VIEW
from app.core.utils import get_request_id, log_audit
from app.models.entities import Customer, User
from app.schemas.schemas import CustomerOut

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
        log_audit(db, "BULK_EXPORT", actor_username=user.username,
                  actor_role=user.role.name if user.role else "",
                  source_app="UI", reason=f"Customer list view with search: {search}",
                  request_id=get_request_id(),
                  metadata={"search": search, "returned_count": len(filtered)})
        return filtered[offset:offset + limit]
    q = q.order_by(Customer.external_id).limit(limit).offset(offset)
    log_audit(db, "CUSTOMER_LIST", actor_username=user.username,
              actor_role=user.role.name if user.role else "",
              source_app="UI", reason="Customer list viewed",
              request_id=get_request_id())
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
