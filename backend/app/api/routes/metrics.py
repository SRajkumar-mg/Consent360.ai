"""R3-04: Prometheus metrics endpoint."""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.utils import get_request_id
from app.models.entities import Consent, User, AuditLog, Notification, ProcessorAlert, Breach

router = APIRouter(tags=["metrics"])

# Track request counts per hour using a simple approach - increment on each /metrics call
# In production, this should be backed by Redis or a dedicated metrics system
_metrics_data = {
    "requests_total": 0,
    "requests_error": 0,
}


def _prometheus_format(metrics: dict[str, float]) -> str:
    lines = []
    for name, value in sorted(metrics.items()):
        lines.append(f"# HELP consent360_{name} Consent360 {name.replace('_', ' ')}")
        lines.append(f"# TYPE consent360_{name} gauge")
        lines.append(f"consent360_{name} {value}")
    return "\n".join(lines) + "\n"


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db)):
    total_consents = db.query(func.count(Consent.id)).scalar() or 0
    active_consents = db.query(func.count(Consent.id)).filter(Consent.status.in_("ACTIVE", "GRANTED", "RENEWED")).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_audit_events = db.query(func.count(AuditLog.id)).scalar() or 0
    pending_notifications = db.query(func.count(Notification.id)).filter(Notification.status == "PENDING").scalar() or 0
    pending_processor_alerts = db.query(func.count(ProcessorAlert.id)).filter(ProcessorAlert.status.in_(["PENDING", "FAILED"])).scalar() or 0
    active_breaches = db.query(func.count(Breach.id)).filter(Breach.status.in_("DETECTED", "CONTAINED")).scalar() or 0

    # Increment request counter
    _metrics_data["requests_total"] = _metrics_data.get("requests_total", 0) + 1

    data = {
        "total_consents": float(total_consents),
        "active_consents": float(active_consents),
        "total_users": float(total_users),
        "total_audit_events": float(total_audit_events),
        "pending_notifications": float(pending_notifications),
        "pending_processor_alerts": float(pending_processor_alerts),
        "active_breaches": float(active_breaches),
        "requests_total": float(_metrics_data["requests_total"]),
        "requests_error": float(_metrics_data["requests_error"]),
    }

    return _prometheus_format(data)
