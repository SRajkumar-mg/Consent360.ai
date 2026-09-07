from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import (
    DECISION_OUTCOMES,
    NOTIFICATION_CHANNELS,
    Consent,
    ConsentDecisionLog,
    ConsentEvidence,
    Notification,
)

_ACTIVE_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

# Kept in sync with app.services.consent._REFERENCE_REQUIRED_ACTIONS: these
# affirmative actions assert some external proof, so a row claiming one of
# them without an affirmative_reference on file is not "complete" evidence -
# it is an unsubstantiated claim.
_REFERENCE_REQUIRED_ACTIONS = ("DOCUMENT", "CALL", "OTP")


def _is_complete(notice_hash: str | None, content_hash: str | None, affirmative_action: str | None, details: dict | None) -> bool:
    """A single evidence row is "complete" only if it actually proves
    something: both the notice the principal was shown and the consent text
    they acted on are hashed and on file, and - when the claimed
    affirmative_action is one that asserts external proof - a reference to
    that proof is present. Previously this only checked two non-nullable
    columns with defaults (affirmative_action, language), which is true of
    every row regardless of whether it proves anything."""
    if not notice_hash or not content_hash:
        return False
    if (affirmative_action or "") in _REFERENCE_REQUIRED_ACTIONS:
        reference = (details or {}).get("affirmative_reference")
        if not reference:
            return False
    return True


def evidence_completeness(db: Session) -> dict:
    total = db.query(func.count(Consent.id)).filter(Consent.status.in_(_ACTIVE_STATUSES)).scalar() or 0

    rows = (
        db.query(
            ConsentEvidence.consent_id,
            ConsentEvidence.notice_hash,
            ConsentEvidence.content_hash,
            ConsentEvidence.affirmative_action,
            ConsentEvidence.details,
        )
        .join(Consent, Consent.id == ConsentEvidence.consent_id)
        .filter(Consent.status.in_(_ACTIVE_STATUSES))
        .all()
    )
    complete_consent_ids = {
        consent_id
        for consent_id, notice_hash, content_hash, affirmative_action, details in rows
        if _is_complete(notice_hash, content_hash, affirmative_action, details)
    }
    with_evidence = len(complete_consent_ids)
    # K-18: None, not 100.0, when there is no active consent to score. Zero
    # active consents is not "every consent has complete evidence" - it is
    # "there is nothing to measure yet", and a compliance readout must not
    # blur those two together. Matches services/breach.py and
    # services/grievance.py's convention for an empty-set ratio.
    pct = round((with_evidence / total) * 100, 2) if total else None
    return {
        "total_active_consents": total,
        "consents_with_complete_evidence": with_evidence,
        "evidence_completeness_pct": pct,
    }


_DELIVERED_LIKE_STATUSES = ("DELIVERED", "ACKNOWLEDGED")


def notification_delivery_metrics(db: Session) -> dict:
    """R3-06/O-04: K-44 (delivery rate) and K-45 (acknowledgement rate),
    overall and per channel. "Attempted" excludes still-PENDING rows (never
    yet dispatched) so the rate reflects outcomes, not the current queue
    depth."""
    total_attempted = (
        db.query(func.count(Notification.id)).filter(Notification.status != "PENDING").scalar() or 0
    )
    delivered = (
        db.query(func.count(Notification.id))
        .filter(Notification.status.in_(_DELIVERED_LIKE_STATUSES))
        .scalar()
        or 0
    )
    failed = db.query(func.count(Notification.id)).filter(Notification.status == "FAILED").scalar() or 0
    acknowledged = (
        db.query(func.count(Notification.id)).filter(Notification.status == "ACKNOWLEDGED").scalar() or 0
    )
    # K-44/K-45: None, not 100.0/0.0, when the respective denominator is
    # empty. "Nothing attempted yet" and "nothing delivered yet" are not
    # findings of full delivery or zero acknowledgement - they are absence of
    # a sample, and reporting a flattering figure for it is the same defect
    # in both directions.
    delivery_rate_pct = round((delivered / total_attempted) * 100, 2) if total_attempted else None
    ack_rate_pct = round((acknowledged / delivered) * 100, 2) if delivered else None

    by_channel: dict[str, dict[str, int]] = {}
    for channel in NOTIFICATION_CHANNELS:
        c_attempted = (
            db.query(func.count(Notification.id))
            .filter(Notification.channel == channel, Notification.status != "PENDING")
            .scalar()
            or 0
        )
        c_delivered = (
            db.query(func.count(Notification.id))
            .filter(Notification.channel == channel, Notification.status.in_(_DELIVERED_LIKE_STATUSES))
            .scalar()
            or 0
        )
        c_failed = (
            db.query(func.count(Notification.id))
            .filter(Notification.channel == channel, Notification.status == "FAILED")
            .scalar()
            or 0
        )
        by_channel[channel] = {"attempted": c_attempted, "delivered": c_delivered, "failed": c_failed}

    return {
        "notifications_total_attempted": total_attempted,
        "notifications_delivered": delivered,
        "notifications_failed": failed,
        "notifications_acknowledged": acknowledged,
        "notification_delivery_rate_pct": delivery_rate_pct,
        "notification_ack_rate_pct": ack_rate_pct,
        "by_channel": by_channel,
    }


def decision_outcome_counts(db: Session) -> dict:
    """R1-11/D-09/S-03: K-12's outcome-count half (count of
    ALLOW/DENY/REQUIRE_CONSENT/WITHDRAWN/EXPIRED). The p95-validation-latency
    half of K-12 is request-timing instrumentation that belongs with the
    rest of R3-04's observability work, not this table - out of this task's
    scope."""
    counts = {outcome: 0 for outcome in DECISION_OUTCOMES}
    rows = (
        db.query(ConsentDecisionLog.decision, func.count(ConsentDecisionLog.id))
        .group_by(ConsentDecisionLog.decision)
        .all()
    )
    for decision, count in rows:
        if decision in counts:
            counts[decision] = count
    return {"decision_outcome_counts": counts}
