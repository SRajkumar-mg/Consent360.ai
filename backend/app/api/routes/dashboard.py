from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_DASHBOARD
from app.models.entities import (
    AuditLog,
    Consent,
    Customer,
    KpiSnapshot,
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


ACTIVE_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")


def _compute_kpis(db: Session, scope: str | None = None) -> dict:
    consent_service.expire_consents(db, source_app="SYSTEM")
    now = datetime.now(timezone.utc)

    customer_q = db.query(Customer)
    if scope:
        customer_q = customer_q.filter(Customer.source_app == scope)
    total_customers = customer_q.count()

    consent_q = db.query(Consent)
    if scope:
        consent_q = consent_q.filter(Consent.source_app == scope)
    total_consents = consent_q.count()

    active_q = db.query(func.count(Consent.id)).filter(Consent.status.in_(ACTIVE_STATUSES))
    withdrawn_q = db.query(func.count(Consent.id)).filter(Consent.status == "WITHDRAWN")
    expiring_q = db.query(func.count(Consent.id)).filter(
        Consent.status.in_(ACTIVE_STATUSES),
        Consent.expires_at.isnot(None),
        Consent.expires_at > now,
        Consent.expires_at <= now + timedelta(days=30),
    )
    if scope:
        active_q = active_q.filter(Consent.source_app == scope)
        withdrawn_q = withdrawn_q.filter(Consent.source_app == scope)
        expiring_q = expiring_q.filter(Consent.source_app == scope)

    active_consents = active_q.scalar() or 0
    withdrawn_consents = withdrawn_q.scalar() or 0
    expiring_soon = expiring_q.scalar() or 0

    freshness_q = db.query(func.count(Consent.id)).filter(
        Consent.status.in_(ACTIVE_STATUSES),
        (Consent.expires_at.is_(None)) | (Consent.expires_at > now),
    )
    if scope:
        freshness_q = freshness_q.filter(Consent.source_app == scope)
    fresh_consents = freshness_q.scalar() or 0

    overdue_q = db.query(func.count(Consent.id)).filter(
        Consent.status.in_(ACTIVE_STATUSES),
        Consent.expires_at.isnot(None),
        Consent.expires_at < now,
    )
    if scope:
        overdue_q = overdue_q.filter(Consent.source_app == scope)
    expired_count = overdue_q.scalar() or 0

    return {
        "K-11": {"value": active_consents, "metric": "consent_coverage",
                 "name": "Consent Coverage", "details": {"active_consents": active_consents, "total_consents": total_consents}},
        "K-13": {"value": fresh_consents, "metric": "data_freshness",
                 "name": "Data Freshness", "details": {"fresh_consents": fresh_consents, "total_consents": total_consents}},
        "K-14": {"value": expiring_soon, "metric": "expiring_soon",
                 "name": "Consents Expiring Soon", "details": {"days": 30}},
        "K-17": {"value": withdrawn_consents, "metric": "withdrawal_rate",
                 "name": "Withdrawal Rate", "details": {"withdrawn": withdrawn_consents, "total": total_consents}},
        "K-18": {"value": expired_count, "metric": "expired",
                 "name": "Expired Consents", "details": {"expired": expired_count}},
        "K-19": {"value": total_customers, "metric": "total_customers",
                 "name": "Total Customers", "details": {"total": total_customers}},
        "K-20": {"value": total_consents, "metric": "total_consents",
                 "name": "Total Consents", "details": {"total": total_consents}},
    }


@router.get("/kpis")
def kpis(db: Session = Depends(get_db), user: User = Depends(require_permission(PERM_DASHBOARD))):
    scope = get_org_scope(user)
    kpi_data = _compute_kpis(db, scope=scope)
    return {"kpis": kpi_data, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.post("/kpi-rollup")
def kpi_rollup(db: Session = Depends(get_db), user: User = Depends(require_permission(PERM_DASHBOARD))):
    scope = get_org_scope(user)
    kpi_data = _compute_kpis(db, scope=scope)
    now = datetime.now(timezone.utc)
    period = now.strftime("%Y-%m")
    period_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    saved = []
    for kpi_id, data in kpi_data.items():
        snapshot = KpiSnapshot(
            tenant_id=1,
            kpi_id=kpi_id,
            kpi_name=data["name"],
            period=period,
            period_start=period_start,
            period_end=now,
            value=int(data["value"]),
            details=data["details"],
        )
        db.add(snapshot)
        saved.append(kpi_id)
    db.commit()
    return {"message": "KPI rollup computed and stored", "period": period, "kpis_saved": saved, "saved_at": now.isoformat()}


@router.get("/kpi-trends")
def kpi_trends(
    kpi_id: str = Query(default=None),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_DASHBOARD)),
):
    q = db.query(KpiSnapshot)
    if kpi_id:
        q = q.filter(KpiSnapshot.kpi_id == kpi_id)
    else:
        q = q.filter(KpiSnapshot.kpi_id.in_(["K-11", "K-13", "K-14", "K-17"]))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = q.filter(KpiSnapshot.period_start >= since).order_by(KpiSnapshot.period_start)
    rows = q.limit(2000).all()
    return {
        "kpi_id": kpi_id or "all",
        "days": days,
        "count": len(rows),
        "points": [
            {
                "kpi_id": r.kpi_id,
                "kpi_name": r.kpi_name,
                "period": r.period,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "value": r.value,
                "details": r.details or {},
            }
            for r in rows
        ],
    }
