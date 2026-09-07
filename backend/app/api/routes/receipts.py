"""R1-08/B-09: consent receipts.

Receipts are issued automatically by services/consent.py on every
grant/renew (see services/receipts.py::issue_receipt) - this router is
read-only: a staff view scoped like every other consent-adjacent list
endpoint, and a principal self-service view gated by the same
X-Context-Token used by /portal/*.
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_customer_from_context, get_org_scope, require_permission, verify_context_token
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_VIEW
from app.models.entities import ConsentContext, ConsentReceipt, User
from app.schemas.schemas import ConsentReceiptOut
from app.services.receipts import verify_receipt
from app.services.tenancy import ANY_TENANT, resolve_customer

router = APIRouter(prefix="/receipts", tags=["receipts"])


def _out(r: ConsentReceipt) -> ConsentReceiptOut:
    out = ConsentReceiptOut.model_validate(r)
    out.valid = verify_receipt(r)
    return out


def _resolve_customer_and_context(x_context_token: str, db: Session):
    """Same two-layer tenant check as portal.py's own helper: the JWT claim
    AND the persisted ConsentContext row must agree on source_app, so a
    context ever minted against the wrong tenant's customer cannot be used
    here either."""
    payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, payload)
    context = db.query(ConsentContext).filter(ConsentContext.token == x_context_token).first()
    if not context:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    if context.source_app != customer.source_app:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    return customer, context


@router.get("", response_model=list[ConsentReceiptOut])
def list_receipts(
    customer_external_id: str | None = None,
    consent_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(ConsentReceipt)
    if customer_external_id:
        customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_external_id)
        if not customer:
            return []
        q = q.filter(ConsentReceipt.customer_id == customer.id)
    elif scope:
        # No explicit customer filter: still cannot leak another tenant's
        # receipts to an org-scoped role.
        q = q.filter(ConsentReceipt.source_app == scope)
    if consent_id is not None:
        q = q.filter(ConsentReceipt.consent_id == consent_id)
    return [_out(r) for r in q.order_by(ConsentReceipt.issued_at.desc()).limit(200).all()]


@router.get("/me", response_model=list[ConsentReceiptOut])
def my_receipts(x_context_token: str = Header(..., alias="X-Context-Token"), db: Session = Depends(get_db)):
    customer, _context = _resolve_customer_and_context(x_context_token, db)
    receipts = (
        db.query(ConsentReceipt)
        .filter(ConsentReceipt.customer_id == customer.id, ConsentReceipt.source_app == customer.source_app)
        .order_by(ConsentReceipt.issued_at.desc())
        .all()
    )
    return [_out(r) for r in receipts]


@router.get("/me/{receipt_ref}", response_model=ConsentReceiptOut)
def my_receipt_detail(receipt_ref: str, x_context_token: str = Header(..., alias="X-Context-Token"), db: Session = Depends(get_db)):
    customer, _context = _resolve_customer_and_context(x_context_token, db)
    receipt = (
        db.query(ConsentReceipt)
        .filter(ConsentReceipt.receipt_ref == receipt_ref, ConsentReceipt.customer_id == customer.id)
        .first()
    )
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return _out(receipt)


@router.get("/{receipt_ref}", response_model=ConsentReceiptOut)
def get_receipt(receipt_ref: str, db: Session = Depends(get_db),
                user: User = Depends(require_permission(PERM_CONSENT_VIEW))):
    scope = get_org_scope(user)
    q = db.query(ConsentReceipt).filter(ConsentReceipt.receipt_ref == receipt_ref)
    if scope:
        q = q.filter(ConsentReceipt.source_app == scope)
    receipt = q.first()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return _out(receipt)
