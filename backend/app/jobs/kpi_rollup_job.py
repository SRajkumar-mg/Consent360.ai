from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import Consent, Customer, KpiSnapshot
from app.services.kpi import decision_outcome_counts, evidence_completeness, notification_delivery_metrics
from app.services.tenancy import platform_tenant_id


def run(db: Session) -> dict:
    total_customers = db.query(func.count(Customer.id)).scalar() or 0
    total_consents = db.query(func.count(Consent.id)).scalar() or 0
    active_consents = (
        db.query(func.count(Consent.id))
        .filter(Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]))
        .scalar()
        or 0
    )
    metrics = {
        "total_customers": total_customers,
        "total_consents": total_consents,
        "active_consents": active_consents,
        **evidence_completeness(db),
        **notification_delivery_metrics(db),
        **decision_outcome_counts(db),
    }
    snapshot = KpiSnapshot(
        captured_at=datetime.now(timezone.utc), metrics=metrics, tenant_id=platform_tenant_id(db)
    )
    db.add(snapshot)
    db.commit()
    return {"snapshot_id": snapshot.id, **metrics}
