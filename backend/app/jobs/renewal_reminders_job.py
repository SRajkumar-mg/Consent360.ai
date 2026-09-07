from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.entities import Consent, Notification
from app.services.notifications import queue_notification


def run(db: Session) -> dict:
    """R3-06/O-02: queue a RENEWAL_REMINDER notification for every consent
    expiring within 30 days that hasn't already had one queued for this
    same expiry ("this same expiry" - not "this consent, ever" - so a
    renewed consent with a new expires_at gets a fresh reminder for its new
    cycle instead of being silently skipped forever)."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=30)
    due = (
        db.query(Consent)
        .filter(
            Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]),
            Consent.expires_at.isnot(None),
            Consent.expires_at > now,
            Consent.expires_at <= horizon,
        )
        .all()
    )
    queued = 0
    skipped_already_sent = 0
    for consent in due:
        expires_key = consent.expires_at.isoformat()
        # Filtered in Python rather than with a JSON-path SQL comparison
        # (the plain `details` column is untyped JSON, not JSONB, and this
        # per-customer/event_type/source_app result set is always small) -
        # any existing RENEWAL_REMINDER for this exact consent/expiry pair
        # means this cycle's reminder was already queued.
        candidates = (
            db.query(Notification)
            .filter(
                Notification.event_type == "RENEWAL_REMINDER",
                Notification.customer_id == consent.customer_id,
                Notification.source_app == consent.source_app,
            )
            .all()
        )
        already_sent = any(
            (n.details or {}).get("consent_id") == consent.id and (n.details or {}).get("expires_at") == expires_key
            for n in candidates
        )
        if already_sent:
            skipped_already_sent += 1
            continue
        queue_notification(
            db, customer=consent.customer, event_type="RENEWAL_REMINDER", source_app=consent.source_app,
            context={
                "purpose_name": consent.purpose.name if consent.purpose else "",
                "purpose_code": consent.purpose.code if consent.purpose else "",
                "expires_at": expires_key,
                "consent_id": consent.id,
            },
            actor_username="scheduler",
        )
        queued += 1
    return {"consents_due": len(due), "reminders_queued": queued, "already_queued": skipped_already_sent}
