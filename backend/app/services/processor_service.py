"""R3-07: Processor register and webhook delivery service."""
import hashlib
import hmac as _hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.entities import Processor, ProcessorAlert, Tenant
from app.services.audit import log_audit
from app.services.notification import send_notification

logger = logging.getLogger("processor")


def compute_webhook_signature(payload: dict, secret: str) -> str:
    """HMAC-SHA256 signature for webhook delivery."""
    message = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def send_webhook(url: str, payload: dict, signature: str) -> bool:
    """Send a signed webhook to a processor."""
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"X-Consent360-Signature": signature, "Content-Type": "application/json"},
            timeout=30,
        )
        return response.status_code < 400
    except Exception as e:
        logger.error("Webhook delivery failed to %s: %s", url, e)
        return False


def send_cease_processing_alert(db: Session, processor: Processor, consent_id: int, customer_id: int) -> ProcessorAlert:
    """Send a CEASE_PROCESSING alert to a processor."""
    payload = {
        "alert_type": "CEASE_PROCESSING",
        "processor_id": processor.id,
        "consent_id": consent_id,
        "customer_id": customer_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    signature = compute_webhook_signature(payload, processor.webhook_secret)

    alert = ProcessorAlert(
        processor_id=processor.id,
        tenant_id=processor.tenant_id,
        alert_type="CEASE_PROCESSING",
        reference_type="consent",
        reference_id=str(consent_id),
        payload=payload,
        status="PENDING",
    )
    db.add(alert)
    db.flush()

    if processor.webhook_url:
        success = send_webhook(processor.webhook_url, payload, signature)
        if success:
            alert.status = "SENT"
            alert.sent_at = datetime.now(timezone.utc)
        else:
            alert.status = "FAILED"

    db.commit()
    return alert


def send_erasure_instruction(db: Session, processor: Processor, customer_id: int) -> ProcessorAlert:
    """Send an ERASURE_INSTRUCTION alert to a processor."""
    payload = {
        "alert_type": "ERASURE_INSTRUCTION",
        "processor_id": processor.id,
        "customer_id": customer_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    signature = compute_webhook_signature(payload, processor.webhook_secret)

    alert = ProcessorAlert(
        processor_id=processor.id,
        tenant_id=processor.tenant_id,
        alert_type="ERASURE_INSTRUCTION",
        reference_type="customer",
        reference_id=str(customer_id),
        payload=payload,
        status="PENDING",
    )
    db.add(alert)
    db.flush()

    if processor.webhook_url:
        success = send_webhook(processor.webhook_url, payload, signature)
        if success:
            alert.status = "SENT"
            alert.sent_at = datetime.now(timezone.utc)
        else:
            alert.status = "FAILED"

    db.commit()
    return alert


def notify_processors_for_erasure(db: Session, customer_id: int, processor_ids: list[int]) -> list[ProcessorAlert]:
    """Send ERASURE_INSTRUCTION to specified processors."""
    alerts = []
    for pid in processor_ids:
        processor = db.get(Processor, pid)
        if processor and processor.is_active:
            alert = send_erasure_instruction(db, processor, customer_id)
            alerts.append(alert)
    return alerts


def check_escalations(db: Session, sla_hours: int = 24) -> int:
    """Escalate unacknowledged processor alerts past SLA."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=sla_hours)
    unacked = (
        db.query(ProcessorAlert)
        .filter(
            ProcessorAlert.status == "SENT",
            ProcessorAlert.sent_at.isnot(None),
            ProcessorAlert.sent_at < cutoff,
            ProcessorAlert.escalated_at.is_(None),
        )
        .all()
    )
    escalated = 0
    for alert in unacked:
        alert.escalated_at = datetime.now(timezone.utc)
        alert.status = "ESCALATED"
        # Send DPO notification
        processor = db.get(Processor, alert.processor_id)
        if processor:
            tenant = db.get(Tenant, processor.tenant_id)
            send_notification(
                db,
                tenant_id=processor.tenant_id,
                event_type="PROCESSOR_ALERT_ESCALATED",
                recipient=processor.contact or "dpo@" + (tenant.domain if tenant else "consent360.io"),
                channel="EMAIL",
                context={
                    "processor_name": processor.name,
                    "alert_type": alert.alert_type,
                    "alert_id": str(alert.id),
                },
                reference_type="processor_alert",
                reference_id=str(alert.id),
            )
        escalated += 1
    db.commit()
    return escalated
