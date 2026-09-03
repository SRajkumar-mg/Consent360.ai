import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.entities import (
    CONSENT_STATUSES,
    CONSENT_TRANSITIONS,
    Consent,
    ConsentEvidence,
    ConsentHistory,
    Customer,
    DataCategory,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.services.audit import log_audit


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_policy_version(db: Session) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (policy_id, policy_version_id, policy_version_number) of active policy current version."""
    from app.services.decision_engine import get_active_policy

    policy = get_active_policy(db)
    if not policy:
        return None, None, None
    for v in sorted(policy.versions, key=lambda x: x.version_number, reverse=True):
        if v.is_current:
            return policy.id, v.id, v.version_number
    return policy.id, None, None


def get_current_purpose_version(purpose: Purpose) -> PurposeVersion:
    for v in purpose.versions:
        if v.is_current:
            return v
    raise HTTPException(status_code=500, detail=f"Purpose {purpose.code} has no current version")


def get_or_create_consent(
    db: Session,
    customer: Customer,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    *,
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
    exact_source: bool = False,
) -> tuple[Consent, bool]:
    """Find existing consent for the identity; create a NOT_REQUESTED one if missing.

    By default the lookup is source-agnostic (one row per purpose x category x activity).
    When ``exact_source`` is True the lookup is restricted to the given ``source_app`` so
    that independent consent records can exist per website/app source.
    """
    base = db.query(Consent).filter(
        Consent.customer_id == customer.id,
        Consent.purpose_id == purpose.id,
        Consent.data_category_id == data_category.id,
        Consent.processing_activity_id == processing_activity.id,
        Consent.status.in_(["NOT_REQUESTED", "REQUESTED", "PENDING", "GRANTED", "ACTIVE", "DENIED", "WITHDRAWN", "EXPIRED"]),
    )
    if exact_source:
        base = base.filter(Consent.source_app == source_app)
    existing = base.order_by(Consent.consent_version.desc()).first()
    if existing:
        return existing, False

    pv = get_current_purpose_version(purpose)
    policy_id, policy_version_id, policy_version_number = _resolve_policy_version(db)
    consent = Consent(
        customer_id=customer.id,
        purpose_id=purpose.id,
        purpose_version_id=pv.id,
        data_category_id=data_category.id,
        processing_activity_id=processing_activity.id,
        policy_id=policy_id,
        policy_version_id=policy_version_id,
        consent_version=1,
        status="NOT_REQUESTED",
        collection_method=collection_method,
        source_app=source_app,
        actor_username=actor_username,
        consent_text=pv.consent_text,
    )
    db.add(consent)
    db.flush()

    history = ConsentHistory(
        consent_id=consent.id,
        action="CONSENT_CREATED",
        from_status=None,
        to_status="NOT_REQUESTED",
        reason="Consent record initialized",
        consent_version=1,
        policy_version_id=policy_version_id,
        actor_username=actor_username,
        source_app=source_app,
        request_id=request_id,
    )
    db.add(history)
    log_audit(
        db,
        "CONSENT_CREATED",
        actor_username=actor_username,
        source_app=source_app,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        consent_id=consent.id,
        purpose_id=purpose.id,
        purpose_code=purpose.code,
        policy_id=policy_id,
        policy_code=None,
        old_status=None,
        new_status="NOT_REQUESTED",
        consent_version=1,
        policy_version=policy_version_number,
        reason="Consent record created for customer",
        request_id=request_id,
        commit=False,
    )
    db.commit()
    db.refresh(consent)
    return consent, True


def _validate_transition(from_status: str, to_status: str, action: str) -> None:
    allowed = CONSENT_TRANSITIONS.get(from_status, [])
    if from_status not in CONSENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid consent status: {from_status}")
    if to_status not in CONSENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid target consent status: {to_status}")
    if to_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid consent lifecycle transition {from_status} -> {to_status} for action {action}",
        )


def _record_transition(
    db: Session,
    consent: Consent,
    *,
    action: str,
    to_status: str,
    from_status: Optional[str] = None,
    reason: str,
    actor_username: str,
    source_app: str,
    request_id: Optional[str] = None,
    policy_version_id: Optional[int] = None,
    policy_version_number: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    history = ConsentHistory(
        consent_id=consent.id,
        action=action,
        from_status=from_status or consent.status,
        to_status=to_status,
        reason=reason,
        consent_version=consent.consent_version,
        policy_version_id=policy_version_id,
        actor_username=actor_username,
        source_app=source_app,
        request_id=request_id,
        details=metadata or {},
    )
    db.add(history)
    log_audit(
        db,
        action,
        actor_username=actor_username,
        actor_role="",
        source_app=source_app,
        customer_id=consent.customer_id,
        customer_external_id=consent.customer.external_id,
        consent_id=consent.id,
        purpose_id=consent.purpose_id,
        purpose_code=consent.purpose.code,
        policy_id=consent.policy_id,
        policy_code=consent.policy.code if consent.policy else None,
        old_status=from_status or consent.status,
        new_status=to_status,
        consent_version=consent.consent_version,
        policy_version=policy_version_number,
        reason=reason,
        request_id=request_id,
        metadata=metadata or {},
        commit=False,
    )


def _create_evidence(
    db: Session,
    consent: Consent,
    *,
    collected_by: str,
    collection_method: str,
    source_app: str,
    request_id: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> ConsentEvidence:
    pv = consent.purpose_version
    evidence = ConsentEvidence(
        consent_id=consent.id,
        evidence_ref=f"EV-{uuid.uuid4().hex[:16].upper()}",
        collected_at=utcnow(),
        collected_by=collected_by,
        collection_method=collection_method,
        source_app=source_app,
        consent_text=consent.consent_text or pv.consent_text,
        consent_version=consent.consent_version,
        purpose_version=pv.version_number,
        policy_version=consent.policy_version.version_number if consent.policy_version else None,
        request_id=request_id,
        details={
            **(extra_metadata or {}),
            "purpose_code": consent.purpose.code,
            "data_category_code": consent.data_category.code,
            "processing_activity_code": consent.processing_activity.code,
        },
    )
    db.add(evidence)
    db.flush()
    return evidence


def _apply_status_timestamps(consent: Consent, to_status: str, now: datetime) -> None:
    if to_status == "GRANTED" or to_status == "ACTIVE":
        consent.granted_at = consent.granted_at or now
        consent.denied_at = None
        consent.withdrawn_at = None
    elif to_status == "DENIED":
        consent.denied_at = now
        consent.withdrawn_at = None
    elif to_status == "WITHDRAWN":
        consent.withdrawn_at = now
    elif to_status == "RENEWED":
        consent.renewed_at = now
    elif to_status == "REQUESTED":
        consent.requested_at = now


def request_consent(
    db: Session,
    consent: Consent,
    *,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
) -> Consent:
    _validate_transition(consent.status, "REQUESTED", "request")
    from_status = consent.status
    consent.status = "REQUESTED"
    _apply_status_timestamps(consent, "REQUESTED", utcnow())
    _record_transition(db, consent, action="CONSENT_REQUESTED", to_status="REQUESTED", from_status=from_status, reason=reason or "Consent requested from data principal",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None)
    db.commit()
    db.refresh(consent)
    return consent


def grant_consent(
    db: Session,
    consent: Consent,
    *,
    expires_in_days: Optional[int] = None,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    consent_text: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Consent:
    if consent.status not in ["REQUESTED", "PENDING", "DENIED", "WITHDRAWN", "EXPIRED", "NOT_REQUESTED"]:
        raise HTTPException(status_code=400, detail=f"Cannot grant consent from status {consent.status}")
    from_status = consent.status
    now = utcnow()
    consent.status = "GRANTED"
    consent.granted_at = now
    consent.denied_at = None
    consent.withdrawn_at = None
    consent.renewed_at = None
    consent.requested_at = consent.requested_at or now
    if expires_in_days is not None:
        consent.expires_at = now + timedelta(days=expires_in_days)
    elif consent.purpose and consent.purpose.retention_period_days:
        consent.expires_at = now + timedelta(days=consent.purpose.retention_period_days)
    if consent_text is not None:
        consent.consent_text = consent_text
    _record_transition(db, consent, action="CONSENT_GRANTED", to_status="GRANTED", from_status=from_status, reason=reason or "Consent granted",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       metadata={"expires_at": consent.expires_at.isoformat() if consent.expires_at else None})
    evidence = _create_evidence(db, consent, collected_by=actor_username, collection_method=collection_method,
                                source_app=source_app, request_id=request_id,
                                extra_metadata={"expires_at": consent.expires_at.isoformat() if consent.expires_at else None})
    consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    db.commit()
    db.refresh(consent)
    return consent


def activate_consent(db: Session, consent: Consent, *, reason: str = "", actor_username: str = "system",
                     source_app: str = "", request_id: Optional[str] = None) -> Consent:
    if consent.status == "GRANTED":
        from_status = consent.status
        consent.status = "ACTIVE"
        _record_transition(db, consent, action="CONSENT_ACTIVATED", to_status="ACTIVE", from_status=from_status,
                           reason=reason or "Consent became active",
                           actor_username=actor_username, source_app=source_app, request_id=request_id)
        db.commit()
        db.refresh(consent)
    return consent


def deny_consent(
    db: Session,
    consent: Consent,
    *,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
) -> Consent:
    if consent.status not in ["REQUESTED", "PENDING", "NOT_REQUESTED"]:
        raise HTTPException(status_code=400, detail=f"Cannot deny consent from status {consent.status}")
    from_status = consent.status
    consent.status = "DENIED"
    consent.denied_at = utcnow()
    consent.granted_at = None
    consent.withdrawn_at = None
    _record_transition(db, consent, action="CONSENT_DENIED", to_status="DENIED", from_status=from_status, reason=reason or "Consent denied",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None)
    db.commit()
    db.refresh(consent)
    return consent


def withdraw_consent(
    db: Session,
    consent: Consent,
    *,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> Consent:
    _validate_transition(consent.status, "WITHDRAWN", "withdraw")
    from_status = consent.status
    consent.status = "WITHDRAWN"
    consent.withdrawn_at = utcnow()
    _record_transition(db, consent, action="CONSENT_WITHDRAWN", to_status="WITHDRAWN", from_status=from_status,
                       reason=reason or "Consent withdrawn by data principal",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None)
    db.commit()
    db.refresh(consent)
    return consent


def renew_consent(
    db: Session,
    consent: Consent,
    *,
    expires_in_days: Optional[int] = None,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
) -> Consent:
    if consent.status not in ["GRANTED", "ACTIVE", "RENEWED", "UPDATED", "EXPIRED", "WITHDRAWN"]:
        raise HTTPException(status_code=400, detail=f"Cannot renew consent from status {consent.status}")
    from_status = consent.status
    now = utcnow()
    consent.status = "RENEWED"
    consent.renewed_at = now
    consent.granted_at = now
    consent.withdrawn_at = None
    consent.denied_at = None
    if expires_in_days is not None:
        consent.expires_at = now + timedelta(days=expires_in_days)
    elif consent.purpose and consent.purpose.retention_period_days:
        consent.expires_at = now + timedelta(days=consent.purpose.retention_period_days)
    consent.consent_version += 1
    _record_transition(db, consent, action="CONSENT_RENEWED", to_status="RENEWED", from_status=from_status,
                       reason=reason or "Consent renewed", actor_username=actor_username,
                       source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       metadata={"new_expires_at": consent.expires_at.isoformat() if consent.expires_at else None})
    evidence = _create_evidence(db, consent, collected_by=actor_username, collection_method=collection_method,
                                source_app=source_app, request_id=request_id,
                                extra_metadata={"renewed": True, "expires_at": consent.expires_at.isoformat() if consent.expires_at else None})
    consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    db.commit()
    db.refresh(consent)
    return consent


def expire_consents(db: Session, *, actor_username: str = "system", source_app: str = "SYSTEM") -> int:
    now = utcnow()
    expiring = (
        db.query(Consent)
        .filter(Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]))
        .all()
    )
    count = 0
    for consent in expiring:
        if consent.expires_at is not None and consent.expires_at <= now:
            from_status = consent.status
            consent.status = "EXPIRED"
            _record_transition(db, consent, action="CONSENT_EXPIRED", to_status="EXPIRED", from_status=from_status,
                               reason="Consent expiry date reached", actor_username=actor_username,
                               source_app=source_app,
                               policy_version_id=consent.policy_version_id,
                               policy_version_number=consent.policy_version.version_number if consent.policy_version else None)
            db.commit()
            count += 1
    return count


def update_consent_for_purpose_version(
    db: Session, consent: Consent, new_pv: PurposeVersion, *, reason: str = "",
    actor_username: str = "system", source_app: str = "", request_id: Optional[str] = None,
) -> Consent:
    """Re-associate consent with a new purpose version and mark UPDATED."""
    if consent.status not in ["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]:
        return consent
    from_status = consent.status
    consent.purpose_version_id = new_pv.id
    consent.consent_text = new_pv.consent_text or consent.consent_text
    consent.status = "UPDATED"
    _record_transition(db, consent, action="CONSENT_UPDATED", to_status="UPDATED", from_status=from_status,
                       reason=reason or f"Purpose version updated to v{new_pv.version_number}",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       metadata={"purpose_version": new_pv.version_number})
    db.commit()
    db.refresh(consent)
    return consent
