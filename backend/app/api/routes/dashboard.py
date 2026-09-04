from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_DASHBOARD
from app.models.entities import (
    AuditLog,
    Consent,
    Customer,
    Policy,
    Purpose,
    User,
)
from app.schemas.schemas import (
    DashboardMetrics,
    DashboardResponse,
    PurposeBucket,
    StatusBucket,
)
from app.services import consent as consent_service
from app.services.decision_engine import get_active_policy

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _consent_out(consent: Consent):
    from app.api.routes.consents import _consent_out

    return _consent_out(consent)


@router.get("", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_permission(PERM_DASHBOARD))):
    scope = get_org_scope(user)
    consent_service.expire_consents(db, source_app="SYSTEM")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=30)

    customer_q = db.query(func.count(Customer.id))
    consent_q = db.query(func.count(Consent.id))
    if scope:
        customer_q = customer_q.filter(Customer.source_app == scope)
        consent_q = consent_q.filter(Consent.source_app == scope)

    total_customers = customer_q.scalar() or 0
    total_consents = consent_q.scalar() or 0

    active_statuses = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

    def scoped_consent_count(*filters):
        q = db.query(func.count(Consent.id))
        if scope:
            q = q.filter(Consent.source_app == scope)
        for f in filters:
            q = q.filter(f)
        return q.scalar() or 0

    active_consents = scoped_consent_count(Consent.status.in_(active_statuses))
    pending = scoped_consent_count(Consent.status == "PENDING")
    withdrawn = scoped_consent_count(Consent.status == "WITHDRAWN")
    denied = scoped_consent_count(Consent.status == "DENIED")
    expired = scoped_consent_count(Consent.status == "EXPIRED")
    granted = scoped_consent_count(Consent.status == "GRANTED")
    expiring_soon_count = scoped_consent_count(
        Consent.status.in_(active_statuses),
        Consent.expires_at.isnot(None),
        Consent.expires_at > now,
        Consent.expires_at <= horizon,
    )
    total_purposes = db.query(func.count(Purpose.id)).filter(Purpose.is_active.is_(True)).scalar() or 0
    total_policies = db.query(func.count(Policy.id)).filter(Policy.is_active.is_(True)).scalar() or 0

    metrics = DashboardMetrics(
        total_customers=total_customers,
        total_consents=total_consents,
        active_consents=active_consents,
        pending_consents=pending,
        withdrawn_consents=withdrawn,
        denied_consents=denied,
        expired_consents=expired,
        expiring_soon=expiring_soon_count,
        granted_consents=granted,
        total_purposes=total_purposes,
        total_policies=total_policies,
    )

    status_q = db.query(Consent.status, func.count(Consent.id))
    if scope:
        status_q = status_q.filter(Consent.source_app == scope)
    status_rows = status_q.group_by(Consent.status).all()
    status_distribution = [StatusBucket(status=s, count=c) for s, c in status_rows]

    purpose_q = (
        db.query(Purpose.code, Purpose.name, Consent.status, func.count(Consent.id))
        .join(Consent, Consent.purpose_id == Purpose.id)
    )
    if scope:
        purpose_q = purpose_q.filter(Consent.source_app == scope)
    purpose_rows = purpose_q.group_by(Purpose.code, Purpose.name, Consent.status).all()
    agg: dict[str, dict] = {}
    for code, name, status, count in purpose_rows:
        bucket = agg.setdefault(code, {"code": code, "name": name, "active": 0, "total": 0})
        bucket["total"] += count
        if status in active_statuses:
            bucket["active"] += count
    purpose_distribution = [
        PurposeBucket(purpose_code=b["code"], purpose_name=b["name"], active=b["active"], total=b["total"])
        for b in agg.values()
    ]

    consent_event_q = db.query(AuditLog).filter(AuditLog.event.like("CONSENT_%"), AuditLog.event != "CONSENT_CREATED")
    audit_q = db.query(AuditLog)
    if scope:
        consent_event_q = consent_event_q.filter(AuditLog.source_app == scope)
        audit_q = audit_q.filter(AuditLog.source_app == scope)
    recent_consent = consent_event_q.order_by(AuditLog.created_at.desc()).limit(10).all()
    recent_audit = audit_q.order_by(AuditLog.created_at.desc()).limit(10).all()

    expiring_q = db.query(Consent).filter(
        Consent.status.in_(active_statuses), Consent.expires_at.isnot(None),
        Consent.expires_at > now, Consent.expires_at <= horizon,
    )
    if scope:
        expiring_q = expiring_q.filter(Consent.source_app == scope)
    expiring = expiring_q.order_by(Consent.expires_at).limit(10).all()

    from app.schemas.schemas import AuditEventOut

    return DashboardResponse(
        metrics=metrics,
        status_distribution=status_distribution,
        purpose_distribution=purpose_distribution,
        recent_consent_activity=[AuditEventOut.model_validate(e) for e in recent_consent],
        recent_audit_events=[AuditEventOut.model_validate(e) for e in recent_audit],
        expiring_consents=[_consent_out(c) for c in expiring],
    )
