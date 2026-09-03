"""R3-08: Breach management service."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import Breach, BreachAffectedCustomer, BreachNotification
from app.services.audit import log_audit
from app.services.notification import send_notification

logger = logging.getLogger("breach")


def compute_deadlines(detected_at: datetime, aware_at: datetime) -> dict[str, datetime]:
    """Compute the three required notification deadlines."""
    return {
        "CERT_IN": detected_at + timedelta(hours=6),
        "PRINCIPAL": aware_at + timedelta(hours=72),  # "without delay" - conservative default
        "BOARD_INITIAL": aware_at + timedelta(hours=72),
        "BOARD_DETAILED": aware_at + timedelta(hours=72),
    }


def create_breach(
    db: Session,
    *,
    tenant_id: int,
    breach_type: str,
    detected_at: datetime,
    aware_at: datetime,
    nature: str = "",
    extent: str = "",
    timing: str = "",
    location: str = "",
    likely_impact: str = "",
    cause: str = "",
    mitigation: str = "",
    remedial_measures: str = "",
    findings_on_actor: str = "",
) -> Breach:
    """Create a breach record and auto-generate notification schedule."""
    import uuid

    reference_no = f"BR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    breach = Breach(
        tenant_id=tenant_id,
        reference_no=reference_no,
        breach_type=breach_type,
        detected_at=detected_at,
        aware_at=aware_at,
        nature=nature,
        extent=extent,
        timing=timing,
        location=location,
        likely_impact=likely_impact,
        cause=cause,
        mitigation=mitigation,
        remedial_measures=remedial_measures,
        findings_on_actor=findings_on_actor,
        status="DETECTED",
    )
    db.add(breach)
    db.flush()

    deadlines = compute_deadlines(detected_at, aware_at)

    for recipient_type, deadline in deadlines.items():
        notification = BreachNotification(
            breach_id=breach.id,
            recipient_type=recipient_type,
            deadline_at=deadline,
            status="PENDING",
        )
        db.add(notification)

    log_audit(db, "BREACH_CREATED", actor_username="system", source_app="",
              reason=f"Breach {reference_no} created",
              metadata={"breach_type": breach_type, "reference_no": reference_no})

    db.commit()
    db.refresh(breach)
    return breach


def notify_principal(
    db: Session,
    breach: Breach,
    customer_id: int,
    recipient_email: str,
) -> Optional[BreachNotification]:
    """Send principal notification for a breach (5 mandated elements)."""
    notification = (
        db.query(BreachNotification)
        .filter(
            BreachNotification.breach_id == breach.id,
            BreachNotification.recipient_type == "PRINCIPAL",
        )
        .first()
    )
    if not notification:
        return None

    context = {
        "nature": breach.nature or "Data breach",
        "extent": breach.extent or "Under investigation",
        "timing": breach.timing or "Recently detected",
        "likely_consequences": breach.likely_impact or "Under assessment",
        "mitigation": breach.mitigation or "Containment measures in progress",
        "safety_measures": "Please monitor your accounts and report suspicious activity",
        "contact_person": "Data Protection Officer",
        "reference_no": breach.reference_no,
    }

    send_notification(
        db,
        tenant_id=breach.tenant_id,
        event_type="BREACH_NOTICE",
        recipient=recipient_email,
        channel="EMAIL",
        context=context,
        reference_type="breach",
        reference_id=str(breach.id),
    )

    notification.sent_at = datetime.now(timezone.utc)
    notification.status = "SENT"
    db.commit()
    return notification


def generate_board_report(breach: Breach) -> dict:
    """Generate the 6-section Board report from the breach record."""
    return {
        "reference_no": breach.reference_no,
        "sections": {
            "1_updated_facts": f"Breach detected at {breach.detected_at.isoformat()}, awareness at {breach.aware_at.isoformat()}.",
            "2_events_and_reasons": f"Nature: {breach.nature}. Cause: {breach.cause}.",
            "3_mitigation": breach.mitigation,
            "4_findings_on_actor": breach.findings_on_actor,
            "5_remedial_measures": breach.remedial_measures,
            "6_intimations_to_principals": f"Principal notifications: {len([n for n in breach.notifications if n.recipient_type == 'PRINCIPAL' and n.sent_at])} sent",
        },
        "status": breach.status,
        "breach_type": breach.breach_type,
    }
