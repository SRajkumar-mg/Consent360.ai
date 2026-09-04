"""R3-06: Notification service - templates, adapters, dispatch, idempotency."""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import Notification, NotificationTemplate, Tenant
from app.services.audit import log_audit

logger = logging.getLogger("notification")

# Channel adapters
_email_adapter = None
_sms_adapter = None


def _get_email_adapter():
    global _email_adapter
    if _email_adapter is None:
        from app.services.notification_adapters import EmailAdapter
        _email_adapter = EmailAdapter()
    return _email_adapter


def _get_sms_adapter():
    global _sms_adapter
    if _sms_adapter is None:
        from app.services.notification_adapters import SMSAdapter
        _sms_adapter = SMSAdapter()
    return _sms_adapter


ADAPTERS = {
    "EMAIL": lambda: _get_email_adapter(),
    "SMS": lambda: _get_sms_adapter(),
    "IN_APP": lambda: None,  # In-app notifications are just stored in the DB
}

TEMPLATE_FALLBACK_ORDER = ["en"]


def render_template(body: str, context: dict) -> str:
    """Simple template rendering with {variable} substitution."""
    result = body
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def send_notification(
    db: Session,
    *,
    tenant_id: int,
    event_type: str,
    recipient: str,
    channel: str,
    language: str = "en",
    context: dict,
    reference_type: str = "",
    reference_id: str = "",
) -> Optional[Notification]:
    """Single entry point for sending notifications. All R3 tasks call this."""
    from app.core.encryption import encrypt

    # Idempotency check
    existing = (
        db.query(Notification)
        .filter(
            Notification.event_type == event_type,
            Notification.recipient == recipient,
            Notification.reference_type == reference_type,
            Notification.reference_id == reference_id,
            Notification.status.in_(["PENDING", "SENT", "DELIVERED"]),
        )
        .first()
    )
    if existing:
        logger.info("Skipping duplicate notification: %s for %s", event_type, reference_id)
        return existing

    # Template lookup with fallback
    template = None
    for lang in [language] + TEMPLATE_FALLBACK_ORDER:
        template = (
            db.query(NotificationTemplate)
            .filter(
                NotificationTemplate.tenant_id == tenant_id,
                NotificationTemplate.event_type == event_type,
                NotificationTemplate.channel == channel,
                NotificationTemplate.language == lang,
                NotificationTemplate.is_active.is_(True),
            )
            .first()
        )
        if template:
            break

    if not template:
        logger.warning("No template found for %s/%s/%s", event_type, channel, language)
        # Create notification with raw context summary
        subject = f"{event_type} notification"
        body = str(context)
    else:
        subject = render_template(template.subject, context) if template.subject else ""
        body = render_template(template.body, context)

    notification = Notification(
        tenant_id=tenant_id,
        event_type=event_type,
        channel=channel,
        language=language,
        recipient=encrypt(recipient),
        reference_type=reference_type,
        reference_id=reference_id,
        subject=subject,
        body=body,
        status="PENDING",
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    # Dispatch immediately
    try:
        adapter_factory = ADAPTERS.get(channel)
        if adapter_factory:
            adapter = adapter_factory()
            if adapter:
                provider_ref = adapter.send(recipient, subject, body)
                notification.status = "SENT"
                notification.sent_at = datetime.now(timezone.utc)
                notification.provider_ref = provider_ref
            else:
                notification.status = "SENT"
                notification.sent_at = datetime.now(timezone.utc)
        else:
            notification.status = "SENT"
            notification.sent_at = datetime.now(timezone.utc)
    except Exception as e:
        logger.error("Notification delivery failed: %s", e)
        notification.status = "FAILED"
        notification.failed_at = datetime.now(timezone.utc)
        notification.error_message = str(e)[:500]
        notification.retry_count += 1

    db.commit()
    return notification


def retry_pending_notifications(db: Session, max_retries: int = 3) -> int:
    """Retry pending/failed notifications (called by dispatch job)."""
    pending = (
        db.query(Notification)
        .filter(
            Notification.status.in_(["PENDING", "FAILED"]),
            Notification.retry_count < max_retries,
        )
        .all()
    )
    retried = 0
    for notification in pending:
        try:
            adapter_factory = ADAPTERS.get(notification.channel)
            if adapter_factory:
                adapter = adapter_factory()
                if adapter:
                    adapter.send(notification.recipient, notification.subject, notification.body)
            notification.status = "SENT"
            notification.sent_at = datetime.now(timezone.utc)
            notification.retry_count += 1
            retried += 1
        except Exception as e:
            notification.retry_count += 1
            if notification.retry_count >= max_retries:
                notification.status = "FAILED"
                notification.failed_at = datetime.now(timezone.utc)
                notification.error_message = str(e)[:500]
    db.commit()
    return retried
