from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_VIEW
from app.models.entities import AuditLog, Customer, User
from app.schemas.schemas import AuditEventOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
def list_audit_events(
    customer_name: str = Query(default=None),
    purpose_code: str = Query(default=None),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if scope:
        q = q.filter(AuditLog.source_app == scope)
    if customer_name:
        name_lower = f"%{customer_name}%".lower()
        matching_customer_ids = [
            c.id for c in db.query(Customer).all()
            if name_lower in (c.name or "").lower()
        ]
        if not matching_customer_ids:
            return []
        q = q.filter(AuditLog.customer_id.in_(matching_customer_ids))
    if purpose_code:
        q = q.filter(AuditLog.purpose_code == purpose_code)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    events = q.limit(limit).offset(offset).all()
    return [AuditEventOut.model_validate(e) for e in events]


@router.get("/events")
def list_event_types(_: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    from app.models.entities import AUDIT_EVENTS

    return AUDIT_EVENTS


@router.get("/actors")
def list_actors(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    rows = db.query(AuditLog.actor_username).distinct().order_by(AuditLog.actor_username).all()
    return [r[0] for r in rows]
