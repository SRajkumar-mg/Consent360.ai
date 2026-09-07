"""R3-06 (O-01, O-02, O-04, C-04): the notification service.

Interim per the task's own constraints: email over SMTP with a console
fallback (app/integrations/notifications/email.py, already built for OTP
delivery and reused here), SMS behind a provider interface with a mock
adapter (app/integrations/notifications/sms.py), in-app notifications that
are simply rows a principal can list/acknowledge via
GET/POST /portal/notifications (app/api/routes/portal.py). Neither SMTP nor
the mock SMS adapter gives a delivery callback/webhook, so - for now - a
successful send is treated as delivered in the same step; `status` still
carries a distinct SENT value for a future provider integration that does
report delivery asynchronously to downgrade from.

Call `queue_notification` to create Notification row(s) for an event
(usually one per configured channel); call `dispatch_pending` from the
scheduled job (app/jobs/notification_dispatch_job.py) to actually attempt
delivery, with retries and exponential backoff for transient failures.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.encryption import hmac_digest
from app.integrations.notifications.email import get_email_sender
from app.integrations.notifications.sms import get_sms_sender
from app.models.entities import (
    NOTIFICATION_CHANNELS,
    NOTIFICATION_EVENT_TYPES,
    Customer,
    Notification,
    NotificationTemplate,
)
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id

logger = logging.getLogger("app.notifications")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Every channel an event type is queued on by default when the caller does
# not name one explicitly. IN_APP is included everywhere - it is free (no
# external transport) and gives every principal a durable, in-portal record
# of the notification even when email/SMS delivery fails.
_DEFAULT_CHANNELS_BY_EVENT: dict[str, list[str]] = {
    "CONSENT_ACKNOWLEDGEMENT": ["EMAIL", "IN_APP"],
    "WITHDRAWAL_CONFIRMATION": ["EMAIL", "IN_APP"],
    "RENEWAL_REMINDER": ["EMAIL", "IN_APP"],
    "PURPOSE_CHANGE_RECONSENT": ["EMAIL", "IN_APP"],
    "POLICY_CHANGE_RECONSENT": ["EMAIL", "IN_APP"],
    "ERASURE_WARNING_48H": ["EMAIL", "SMS", "IN_APP"],
    "BREACH_NOTICE": ["EMAIL", "SMS", "IN_APP"],
    "REQUEST_STATUS": ["EMAIL", "IN_APP"],
    "GRIEVANCE_STATUS": ["EMAIL", "IN_APP"],
    "LEGACY_NOTICE": ["EMAIL", "IN_APP"],
    # R3-07: goes to the tenant's DPO, not to a principal - EMAIL only,
    # because IN_APP notifications are listed through /portal/notifications,
    # which is a data-principal surface.
    "PROCESSOR_ESCALATION": ["EMAIL"],
}

# Fallback (subject, body_template) used when no NotificationTemplate row
# exists yet for (event_type, channel, "en") - keeps the service usable
# before seed.py's default templates are loaded (e.g. a fresh dev DB before
# `python seed.py` has run) and keeps every event type honest about which
# placeholders it fills at render time. `{customer_name}` is always
# available; everything else comes from the caller's own `context` dict
# (see each trigger call site for what it passes).
FALLBACK_TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "CONSENT_ACKNOWLEDGEMENT": {
        "EMAIL": ("Consent recorded for {purpose_name}",
                  "Hello {customer_name}, we have recorded your consent for {purpose_name}. "
                  "You can review or withdraw it any time from your consent portal."),
        "SMS": ("", "Consent360: consent for {purpose_name} recorded. Manage it in your consent portal."),
        "IN_APP": ("Consent recorded", "Your consent for {purpose_name} has been recorded."),
    },
    "WITHDRAWAL_CONFIRMATION": {
        "EMAIL": ("Consent withdrawn for {purpose_name}",
                  "Hello {customer_name}, your consent for {purpose_name} has been withdrawn as requested. "
                  "Processing under this purpose will stop unless another lawful basis applies."),
        "SMS": ("", "Consent360: consent for {purpose_name} withdrawn as requested."),
        "IN_APP": ("Consent withdrawn", "Your consent for {purpose_name} has been withdrawn."),
    },
    "RENEWAL_REMINDER": {
        "EMAIL": ("Your consent for {purpose_name} is expiring soon",
                  "Hello {customer_name}, your consent for {purpose_name} expires on {expires_at}. "
                  "Renew it from your consent portal if you want processing to continue."),
        "SMS": ("", "Consent360: consent for {purpose_name} expires {expires_at}. Renew in your consent portal."),
        "IN_APP": ("Consent expiring soon", "Your consent for {purpose_name} expires on {expires_at}."),
    },
    "PURPOSE_CHANGE_RECONSENT": {
        "EMAIL": ("{purpose_name} has changed and needs your consent again",
                  "Hello {customer_name}, the purpose {purpose_name} was updated to a new version. "
                  "Please review and re-confirm your consent in your consent portal."),
        "SMS": ("", "Consent360: {purpose_name} changed - please re-confirm your consent."),
        "IN_APP": ("Re-consent needed", "{purpose_name} changed to a new version - please review your consent."),
    },
    "POLICY_CHANGE_RECONSENT": {
        "EMAIL": ("Our policy {policy_name} has changed and needs your consent again",
                  "Hello {customer_name}, the policy {policy_name} was updated to a new version. "
                  "Please review and re-confirm your consent in your consent portal."),
        "SMS": ("", "Consent360: {policy_name} changed - please re-confirm your consent."),
        "IN_APP": ("Re-consent needed", "{policy_name} changed to a new version - please review your consent."),
    },
    "ERASURE_WARNING_48H": {
        "EMAIL": ("Your data will be erased in 48 hours",
                  "Hello {customer_name}, your account and personal data are scheduled for erasure "
                  "in 48 hours. Contact us before then if this was not requested by you."),
        "SMS": ("", "Consent360: your data will be erased in 48 hours unless you contact us."),
        "IN_APP": ("Erasure scheduled", "Your data will be erased in 48 hours."),
    },
    "BREACH_NOTICE": {
        "EMAIL": ("Important: a personal data breach notice",
                  "Hello {customer_name}, {details}"),
        "SMS": ("", "Consent360: a data breach notice concerning your account has been issued. Check your email."),
        "IN_APP": ("Data breach notice", "{details}"),
    },
    "REQUEST_STATUS": {
        "EMAIL": ("Update on your data-rights request",
                  "Hello {customer_name}, your request ({request_type}) is now {status}. {details}"),
        "SMS": ("", "Consent360: your request status is now {status}."),
        "IN_APP": ("Request update", "Your request ({request_type}) is now {status}."),
    },
    "GRIEVANCE_STATUS": {
        "EMAIL": ("Update on your grievance",
                  "Hello {customer_name}, your grievance is now {status}. {details}"),
        "SMS": ("", "Consent360: your grievance status is now {status}."),
        "IN_APP": ("Grievance update", "Your grievance is now {status}."),
    },
    "PROCESSOR_ESCALATION": {
        "EMAIL": ("Processor has not acknowledged a {alert_type} instruction",
                  "Processor {processor_name} has not acknowledged instruction {alert_ref} "
                  "({alert_type}) raised at {raised_at}. Its acknowledgement SLA of "
                  "{ack_sla_hours}h elapsed at {due_at}. Under DPDP Act s.6(6) the fiduciary "
                  "must cause its processors to cease processing within a reasonable time - "
                  "chase this processor and record the outcome against the alert."),
        "SMS": ("", "Consent360: processor {processor_name} missed the SLA on instruction {alert_ref}."),
        "IN_APP": ("Processor escalation", "Processor {processor_name} missed the SLA on {alert_ref}."),
    },
    # R2-11/A-08. `{details}` is not a filler here: it is the itemised s.5(1)
    # notice composed per principal by services/legacy_notice.py::
    # compose_notice_body - which purposes, since when, her rights, the live
    # withdrawal link, the DPO and the Board complaint path. The template
    # wraps it so an operator can change the framing through
    # PUT /notifications/templates/{id} without being able to delete the
    # statutory particulars, and the last line is deliberate: a s.5(2) notice
    # that let silence read as agreement would be the exact failure the
    # section exists to prevent.
    "LEGACY_NOTICE": {
        "EMAIL": ("Notice about personal data you consented to before {law_effective_date}",
                  "Hello {customer_name},\n\n"
                  "This is a notice under section 5(2) of the Digital Personal Data Protection "
                  "Act, 2023, about personal data of yours that we process on the basis of consent "
                  "you gave before the Act's notice requirements applied.\n\n"
                  "{details}\n\n"
                  "You do not need to reply to this message, and not replying does not renew or "
                  "confirm your consent."),
        "SMS": ("", "Consent360: a notice about data you consented to before {law_effective_date} "
                    "is in your email and your consent portal. You can withdraw at any time."),
        "IN_APP": ("Notice about data collected before {law_effective_date}", "{details}"),
    },
}


class _SafeDict(dict):
    """Renders a missing placeholder as an empty string instead of raising
    KeyError - a template must never fail to send just because one caller
    forgot to pass an optional context field."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
        return ""


def render_template(subject_template: str, body_template: str, context: dict) -> tuple[str, str]:
    safe = _SafeDict(**{k: ("" if v is None else v) for k, v in context.items()})
    return subject_template.format_map(safe), body_template.format_map(safe)


def _select_template(
    db: Session, *, tenant_id: Optional[int], event_type: str, channel: str, language: str
) -> tuple[Optional[int], str, str]:
    """Lookup order: this tenant's own override -> platform default
    (tenant_id NULL) -> same tenant/platform row for language "en" -> the
    module-level fallback copy. Mirrors PurposeVersion.translations's own
    per-language-falls-back-to-English behaviour (see docs/ARCHITECTURE.md)."""
    for candidate_tenant in (tenant_id, None):
        for candidate_language in (language, "en"):
            row = (
                db.query(NotificationTemplate)
                .filter(
                    NotificationTemplate.tenant_id == candidate_tenant,
                    NotificationTemplate.event_type == event_type,
                    NotificationTemplate.channel == channel,
                    NotificationTemplate.language == candidate_language,
                    NotificationTemplate.is_active.is_(True),
                )
                .first()
            )
            if row:
                return row.id, row.subject, row.body_template
    fallback = FALLBACK_TEMPLATES.get(event_type, {}).get(channel)
    if fallback:
        return None, fallback[0], fallback[1]
    return None, "", ""


def _recipient_for_channel(customer: Customer, channel: str) -> str:
    if channel == "EMAIL":
        return customer.email or ""
    if channel == "SMS":
        return customer.phone or ""
    return customer.external_id or ""


def queue_notification(
    db: Session,
    *,
    customer: Customer,
    event_type: str,
    source_app: str,
    channels: Optional[list[str]] = None,
    language: Optional[str] = None,
    context: Optional[dict] = None,
    request_id: Optional[str] = None,
    actor_username: str = "system",
) -> list[Notification]:
    """Queue one Notification row per channel for this event. A channel
    requiring a recipient the customer has no value for (e.g. SMS with no
    phone on file) is silently skipped rather than queued to fail forever -
    see the returned list, which only contains what was actually queued."""
    if event_type not in NOTIFICATION_EVENT_TYPES:
        raise ValueError(f"Unknown notification event_type: {event_type}")
    chosen_channels = channels or _DEFAULT_CHANNELS_BY_EVENT.get(event_type, ["IN_APP"])
    for c in chosen_channels:
        if c not in NOTIFICATION_CHANNELS:
            raise ValueError(f"Unknown notification channel: {c}")

    tenant_id = resolve_tenant_id(db, source_app)
    render_context = {"customer_name": customer.name, **(context or {})}
    now = utcnow()
    queued: list[Notification] = []

    for channel in chosen_channels:
        recipient = _recipient_for_channel(customer, channel)
        if channel != "IN_APP" and not recipient:
            logger.info(
                "Skipping %s notification for customer_id=%s event_type=%s: no recipient on file",
                channel, customer.id, event_type,
            )
            continue
        lang = language or "en"
        template_id, subject_template, body_template = _select_template(
            db, tenant_id=tenant_id, event_type=event_type, channel=channel, language=lang
        )
        subject, body = render_template(subject_template, body_template, render_context)
        notification = Notification(
            tenant_id=tenant_id,
            customer_id=customer.id,
            template_id=template_id,
            event_type=event_type,
            channel=channel,
            language=lang,
            recipient=recipient,
            recipient_search=hmac_digest(recipient) if recipient else None,
            subject=subject,
            body=body,
            status="PENDING",
            next_attempt_at=now,
            source_app=source_app,
            actor_username=actor_username,
            request_id=request_id,
            details={k: v for k, v in (context or {}).items() if isinstance(v, (str, int, float, bool, type(None)))},
        )
        db.add(notification)
        db.flush()
        log_audit(
            db, "NOTIFICATION_QUEUED", actor_username=actor_username, source_app=source_app,
            tenant_id=tenant_id, customer_id=customer.id, customer_external_id=customer.external_id,
            reason=f"Notification queued: {event_type} via {channel}",
            request_id=request_id, metadata={"notification_id": notification.id, "channel": channel},
            commit=False,
        )
        queued.append(notification)
    db.commit()
    for n in queued:
        db.refresh(n)
    return queued


def queue_operational_notification(
    db: Session,
    *,
    recipient: str,
    event_type: str,
    tenant_id: Optional[int],
    source_app: str = "",
    channel: str = "EMAIL",
    language: str = "en",
    context: Optional[dict] = None,
    request_id: Optional[str] = None,
    actor_username: str = "system",
) -> Optional[Notification]:
    """Queue a notification whose recipient is an internal role (a DPO, an
    operations mailbox), not a data principal.

    `queue_notification` above resolves the recipient from a `Customer` and is
    therefore unusable for R3-07's DPO escalation. Rather than build a second
    dispatch path, this queues a row in the same `notifications` table with
    `customer_id=NULL` and an explicit recipient, so `dispatch_pending` below
    delivers it with the same retry/backoff behaviour and it lands in the same
    K-44/K-45 delivery metrics. Returns None (having logged why) when there is
    no address to send to - an escalation with nowhere to go must not become a
    row that fails forever.
    """
    if event_type not in NOTIFICATION_EVENT_TYPES:
        raise ValueError(f"Unknown notification event_type: {event_type}")
    if channel not in NOTIFICATION_CHANNELS:
        raise ValueError(f"Unknown notification channel: {channel}")
    if not recipient:
        logger.warning(
            "Cannot queue %s operational notification: no recipient address configured", event_type
        )
        return None

    template_id, subject_template, body_template = _select_template(
        db, tenant_id=tenant_id, event_type=event_type, channel=channel, language=language
    )
    subject, body = render_template(subject_template, body_template, context or {})
    notification = Notification(
        tenant_id=tenant_id,
        customer_id=None,
        template_id=template_id,
        event_type=event_type,
        channel=channel,
        language=language,
        recipient=recipient,
        recipient_search=hmac_digest(recipient),
        subject=subject,
        body=body,
        status="PENDING",
        next_attempt_at=utcnow(),
        source_app=source_app,
        actor_username=actor_username,
        request_id=request_id,
        details={k: v for k, v in (context or {}).items() if isinstance(v, (str, int, float, bool, type(None)))},
    )
    db.add(notification)
    db.flush()
    log_audit(
        db, "NOTIFICATION_QUEUED", actor_username=actor_username, source_app=source_app,
        tenant_id=tenant_id,
        reason=f"Operational notification queued: {event_type} via {channel}",
        request_id=request_id,
        metadata={"notification_id": notification.id, "channel": channel, "internal_recipient": True},
        commit=False,
    )
    db.commit()
    db.refresh(notification)
    return notification


def _backoff_minutes(retry_count: int) -> int:
    # 5, 10, 20, 40, ... capped at 4 hours.
    return min(5 * (2 ** max(retry_count - 1, 0)), 240)


def _attempt_send(db: Session, notification: Notification) -> bool:
    try:
        if notification.channel == "EMAIL":
            get_email_sender().send(to=notification.recipient, subject=notification.subject, body=notification.body)
            provider_ref = f"email:{notification.id}:{int(utcnow().timestamp())}"
        elif notification.channel == "SMS":
            provider_ref = get_sms_sender().send(to=notification.recipient, message=notification.body)
        elif notification.channel == "IN_APP":
            provider_ref = "in-app"
        else:  # pragma: no cover - guarded by the CHECK constraint/queue_notification
            raise ValueError(f"Unknown channel {notification.channel}")
    except Exception as exc:  # noqa: BLE001 - a provider failure must never crash the dispatch job
        notification.retry_count += 1
        notification.last_error = str(exc)[:2000]
        if notification.retry_count >= notification.max_retries:
            notification.status = "FAILED"
        else:
            notification.next_attempt_at = utcnow() + timedelta(minutes=_backoff_minutes(notification.retry_count))
        db.commit()
        log_audit(
            db, "NOTIFICATION_SEND_FAILED", actor_username="scheduler", source_app=notification.source_app,
            tenant_id=notification.tenant_id, customer_id=notification.customer_id,
            reason=f"Notification {notification.id} ({notification.channel}) send failed: {exc}",
            request_id=notification.request_id,
            metadata={"notification_id": notification.id, "retry_count": notification.retry_count,
                      "status": notification.status},
        )
        return False

    now = utcnow()
    notification.provider_ref = provider_ref
    notification.sent_at = now
    # Interim: neither SMTP nor the mock SMS adapter reports delivery
    # asynchronously (no webhook/callback exists), so a successful send is
    # treated as delivered in this same step - see module docstring.
    notification.delivered_at = now
    notification.status = "DELIVERED"
    db.commit()
    log_audit(
        db, "NOTIFICATION_DELIVERED", actor_username="scheduler", source_app=notification.source_app,
        tenant_id=notification.tenant_id, customer_id=notification.customer_id,
        reason=f"Notification {notification.id} ({notification.channel}) delivered",
        request_id=notification.request_id,
        metadata={"notification_id": notification.id, "provider_ref": provider_ref},
    )
    return True


def dispatch_pending(db: Session, *, limit: int = 500, event_type: Optional[str] = None) -> dict:
    """Attempt delivery of every PENDING notification whose next_attempt_at
    has arrived. Called from app/jobs/notification_dispatch_job.py.

    `event_type` narrows the sweep to one event. The scheduled job never
    passes it (nothing may be starved of retries by a filter), but a staff
    action that means "send the legacy notices I just queued, now" must not
    also flush an unrelated backlog under that operator's name - see
    routes/reconsent.py::dispatch_legacy_notices.
    """
    now = utcnow()
    q = db.query(Notification).filter(
        Notification.status == "PENDING", Notification.next_attempt_at <= now
    )
    if event_type:
        q = q.filter(Notification.event_type == event_type)
    pending = q.order_by(Notification.next_attempt_at.asc()).limit(limit).all()
    delivered = 0
    failed = 0
    for notification in pending:
        if _attempt_send(db, notification):
            delivered += 1
        else:
            failed += 1
    return {"attempted": len(pending), "delivered": delivered, "failed_or_retrying": failed}


def acknowledge_notification(db: Session, notification: Notification, *, actor_username: str) -> Notification:
    if notification.status not in ("SENT", "DELIVERED"):
        raise ValueError(f"Cannot acknowledge a notification in status {notification.status}")
    notification.status = "ACKNOWLEDGED"
    notification.acknowledged_at = utcnow()
    db.commit()
    log_audit(
        db, "NOTIFICATION_ACKNOWLEDGED", actor_username=actor_username, source_app=notification.source_app,
        tenant_id=notification.tenant_id, customer_id=notification.customer_id,
        reason=f"Notification {notification.id} acknowledged by recipient",
        metadata={"notification_id": notification.id},
    )
    db.refresh(notification)
    return notification
