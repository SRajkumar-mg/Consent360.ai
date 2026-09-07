from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission, user_has_permission
from app.core.database import get_db
from app.core.rbac import PERM_CUSTOMER_CONTACT_VIEW, PERM_CUSTOMER_VIEW
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
    # Contact masking is decided HERE, in the response layer, not in the ORM:
    # `customer.view` gets the directory, `customer.contact.view` gets the
    # real email address and telephone number in it. See
    # CustomerOut.for_staff and the note beside PERM_CUSTOMER_CONTACT_VIEW.
    contact_visible = user_has_permission(user, PERM_CUSTOMER_CONTACT_VIEW)
    scope = get_org_scope(user)
    q = db.query(Customer)
    if scope:
        q = q.filter(Customer.source_app == scope)
    if search:
        # The search runs over the REAL values (these are still ORM rows;
        # masking happens on the way out), so a caller without
        # `customer.contact.view` can still find a principal by their email
        # address even though the response will not show it back to them.
        # That is intentional: the console has one search box and staff have
        # to be able to use it, and confirming an address you already have is
        # a far smaller disclosure than handing over every principal's
        # contact details in one listing.
        #
        # `needle`, not `f"%{search}%"` - an incidental fix. This was a SQL
        # `LIKE` until the customer columns became encrypted (encrypted
        # columns cannot be filtered by equality or LIKE, see
        # app/core/encryption.py), at which point the filter moved into Python
        # but kept the SQL wildcards. Python's `in` treats `%` as an ordinary
        # character, so every term was searched for wrapped in percent signs
        # and `GET /customers?search=...` could never match anything at all.
        # Fixed here rather than left alone because a masking control on a
        # branch that can never return a row protects nothing.
        needle = search.lower()
        all_customers = q.order_by(Customer.external_id).all()
        q = None
        filtered = [
            c for c in all_customers
            if needle in (c.external_id or "").lower()
            or needle in (c.name or "").lower()
            or needle in (c.email or "").lower()
        ]
        return [
            CustomerOut.for_staff(c, contact_visible=contact_visible)
            for c in filtered[offset:offset + limit]
        ]
    q = q.order_by(Customer.external_id).limit(limit).offset(offset)
    return [CustomerOut.for_staff(c, contact_visible=contact_visible) for c in q.all()]


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_permission(PERM_CUSTOMER_VIEW))):
    scope = get_org_scope(user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return CustomerOut.for_staff(
        customer,
        contact_visible=user_has_permission(user, PERM_CUSTOMER_CONTACT_VIEW),
    )
