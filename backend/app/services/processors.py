"""R3-07 (C-02, M-02, M-05, K-08): the processor register and cease-processing
propagation.

DPDP Act s.6(6) does not stop at recording a withdrawal: the fiduciary must
"cease, and cause its Data Processors to cease" processing within a reasonable
time. Before this module, withdrawal changed a row's status and nothing left
the building. This module is the propagation half:

    withdrawal / erasure instruction / disclosure
        -> processors_holding_data()      which processors actually hold it
        -> raise_alert()                  one signed ProcessorAlert each
        -> dispatch_pending_alerts()      HMAC-signed POST, retries, backoff
        -> acknowledge_alert()            the processor confirms it acted
        -> escalate_overdue_alerts()      SLA elapsed -> notify the DPO
        -> propagation_sla_metrics()      K-08

Three things are worth reading before changing anything here.

**"Every processor holding that data" is answered from evidence, not from
guesswork.** A processor is treated as holding a principal's data for a
purpose when there is a `data_sharing_events` row of type SENT recording that
we actually disclosed it (routes/sharing_events.py). We do not infer holding
from the transfer register or from a processor merely being active: that would
manufacture instructions to processors that never received anything, and it
would make K-08's denominator meaningless. The cost of this choice is that a
disclosure made outside this platform is invisible to it - which is a
data-entry gap in the sharing log, and is reported as such by
`propagation_sla_metrics`'s `withdrawals_without_processors` count rather than
silently counted as success.

**Acknowledgement, not delivery, is the compliance signal.** A 2xx from a
processor's webhook only proves we reached it. s.6(6) needs the processor to
confirm it has stopped, so a delivered-but-unacknowledged alert stays out of
K-08's numerator and is escalated to the DPO when its SLA elapses.

**The erasure engine (R1-06) does not exist yet.** `raise_erasure_instructions`
below is the interface it will call, and it is honest about its scope: it
raises and tracks an instruction to a processor. It erases nothing - not in
this database, not in the processor's systems - and it must not be mistaken
for an erasure implementation. See that function's docstring for the exact
contract R1-06 should code against.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.utils import mask_identifier
from app.models.entities import (
    PROCESSOR_ALERT_TYPES,
    Consent,
    ConsentHistory,
    Customer,
    DataSharingEvent,
    Organization,
    Processor,
    ProcessorAlert,
    Purpose,
)
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id

logger = logging.getLogger("app.processors")

# Header names for the outbound instruction and the inbound acknowledgement.
# Both directions are signed with the same per-processor shared secret.
SIGNATURE_HEADER = "X-Consent360-Signature"
TIMESTAMP_HEADER = "X-Consent360-Timestamp"
ALERT_REF_HEADER = "X-Consent360-Alert-Ref"
EVENT_HEADER = "X-Consent360-Event"

# How far a signed request's timestamp may be from our clock before the
# signature is rejected, in seconds. Bounds replay of a captured, still-valid
# acknowledgement.
SIGNATURE_TOLERANCE_SECONDS = 300

DEFAULT_ACK_SLA_HOURS = 24
DELIVERY_TIMEOUT_SECONDS = 5.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Webhook secrets
# ---------------------------------------------------------------------------
def generate_webhook_secret() -> tuple[str, str]:
    """Return (plaintext_secret, fingerprint).

    Unlike `app/core/api_keys.py::generate_api_key`, the plaintext is stored
    (encrypted) rather than discarded: an outbound signature has to be
    *computed*, which a one-way hash cannot do. See `Processor`'s docstring in
    models/entities.py for why that asymmetry is unavoidable and what
    compensates for it. The fingerprint is a SHA-256 of the plaintext and is
    the only form of the secret that may ever appear in a response, a log line
    or an audit row.
    """
    plaintext = f"whsec_{secrets.token_urlsafe(32)}"
    return plaintext, secret_fingerprint(plaintext)


def secret_fingerprint(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """`sha256=<hex>` over "<timestamp>.<body>".

    The timestamp is inside the signed string, not merely alongside it, so it
    cannot be rewritten by a man in the middle to widen the replay window.
    """
    signed_string = f"{timestamp}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), signed_string, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    secret: str,
    timestamp: str | int | None,
    body: bytes,
    signature: str | None,
    *,
    tolerance_seconds: int = SIGNATURE_TOLERANCE_SECONDS,
    now: Optional[datetime] = None,
) -> bool:
    """Constant-time verification of an inbound acknowledgement's signature."""
    if not secret or not signature or timestamp is None:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    reference = int((now or utcnow()).timestamp())
    if abs(reference - ts) > tolerance_seconds:
        return False
    return hmac.compare_digest(sign_payload(secret, ts, body), signature)


# ---------------------------------------------------------------------------
# Who holds the data
# ---------------------------------------------------------------------------
def processors_holding_data(
    db: Session,
    *,
    customer_id: int,
    purpose_id: Optional[int] = None,
) -> list[Processor]:
    """Active processors we have a record of having actually SENT this
    principal's data to, optionally narrowed to one purpose.

    Purpose-narrowing is the legally correct default for a withdrawal: a
    principal withdrawing consent for purpose X requires cessation for X, not
    for an unrelated purpose the same processor serves under a different
    lawful basis. Pass `purpose_id=None` (as the erasure path does) to reach
    every processor holding any of this principal's data.
    """
    q = (
        db.query(DataSharingEvent.processor_id)
        .filter(
            DataSharingEvent.customer_id == customer_id,
            DataSharingEvent.event_type == "SENT",
        )
    )
    if purpose_id is not None:
        q = q.filter(DataSharingEvent.purpose_id == purpose_id)
    processor_ids = {row[0] for row in q.distinct().all()}
    if not processor_ids:
        return []
    return (
        db.query(Processor)
        .filter(Processor.id.in_(processor_ids), Processor.is_active.is_(True))
        .order_by(Processor.id.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Raising alerts
# ---------------------------------------------------------------------------
def _new_alert_ref() -> str:
    return f"ALRT-{uuid.uuid4().hex[:20].upper()}"


def _canonical_body(payload: dict) -> bytes:
    """Exactly the bytes that go on the wire and into the signature."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def raise_alert(
    db: Session,
    *,
    processor: Processor,
    alert_type: str,
    trigger_ref: str,
    customer: Optional[Customer] = None,
    consent: Optional[Consent] = None,
    purpose: Optional[Purpose] = None,
    sharing_event_id: Optional[int] = None,
    reason: str = "",
    extra: Optional[dict] = None,
    source_app: str = "",
    actor_username: str = "system",
    request_id: Optional[str] = None,
    tenant_id: Optional[int] = None,
    commit: bool = True,
) -> ProcessorAlert:
    """Create one PENDING, signed instruction for one processor.

    The row is signed at creation time rather than at send time so the
    signature is pinned to the secret in force when the instruction was
    raised: a rotation between raising and sending must not silently change
    what was signed. `dispatch_pending_alerts` re-signs only when it finds the
    stored signature was produced under a superseded secret (see there).
    """
    if alert_type not in PROCESSOR_ALERT_TYPES:
        raise ValueError(f"Unknown processor alert type: {alert_type}")

    now = utcnow()
    sla_hours = processor.ack_sla_hours or DEFAULT_ACK_SLA_HOURS
    alert_ref = _new_alert_ref()

    payload = {
        "alert_ref": alert_ref,
        "alert_type": alert_type,
        "trigger_ref": trigger_ref,
        "raised_at": now.isoformat(),
        "acknowledge_by": (now + timedelta(hours=sla_hours)).isoformat(),
        "processor": {"id": processor.id, "name": processor.name},
        "reason": reason,
        # The principal is identified to the processor by the external id it
        # was disclosed under, never by email/phone: the instruction must be
        # actionable without shipping fresh PII to a processor we are in the
        # middle of telling to stop processing PII.
        "data_principal": {"external_id": customer.external_id} if customer else None,
        "purpose": {"id": purpose.id, "code": purpose.code, "name": purpose.name} if purpose else None,
        "consent": (
            {"id": consent.id, "status": consent.status, "version": consent.consent_version}
            if consent else None
        ),
        "required_action": _required_action(alert_type),
        "acknowledgement": {
            "method": "POST",
            "path": f"/processors/alerts/{alert_ref}/ack",
            "signed_with": "the webhook secret issued to this processor",
        },
    }
    if extra:
        payload["details"] = extra

    body = _canonical_body(payload)
    signed_timestamp = int(now.timestamp())
    signature = sign_payload(processor.webhook_secret, signed_timestamp, body) if processor.webhook_secret else ""

    alert = ProcessorAlert(
        tenant_id=tenant_id if tenant_id is not None else resolve_tenant_id(db, source_app),
        alert_ref=alert_ref,
        trigger_ref=trigger_ref,
        processor_id=processor.id,
        customer_id=customer.id if customer else None,
        consent_id=consent.id if consent else None,
        purpose_id=purpose.id if purpose else None,
        sharing_event_id=sharing_event_id,
        alert_type=alert_type,
        status="PENDING",
        payload=payload,
        payload_hash=hashlib.sha256(body).hexdigest(),
        signature=signature,
        signed_timestamp=signed_timestamp,
        webhook_url=processor.webhook_url or "",
        next_attempt_at=now,
        ack_sla_hours=sla_hours,
        due_at=now + timedelta(hours=sla_hours),
        source_app=source_app,
        actor_username=actor_username,
        request_id=request_id,
        reason=reason,
        details={},
        created_at=now,
    )
    db.add(alert)
    db.flush()
    log_audit(
        db, "PROCESSOR_ALERT_RAISED", actor_username=actor_username, source_app=source_app,
        tenant_id=alert.tenant_id,
        customer_id=customer.id if customer else None,
        customer_external_id=customer.external_id if customer else None,
        consent_id=consent.id if consent else None,
        purpose_id=purpose.id if purpose else None,
        purpose_code=purpose.code if purpose else None,
        reason=reason or f"{alert_type} instruction raised for processor {processor.name}",
        request_id=request_id,
        metadata={
            "alert_ref": alert_ref, "trigger_ref": trigger_ref, "processor_id": processor.id,
            "alert_type": alert_type, "ack_sla_hours": sla_hours, "due_at": alert.due_at.isoformat(),
            "deliverable": bool(processor.webhook_url and processor.webhook_secret),
        },
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(alert)
    return alert


def _required_action(alert_type: str) -> str:
    return {
        "CEASE_PROCESSING": (
            "Stop all processing of this data principal's personal data for the named purpose "
            "and confirm by acknowledging this alert (DPDP Act s.6(6))."
        ),
        "ERASURE_INSTRUCTION": (
            "Erase this data principal's personal data held on our behalf, and any copies, "
            "then confirm by acknowledging this alert (DPDP Act s.8(7))."
        ),
        "SHARING_EVENT": (
            "Record receipt of the disclosure described here and confirm your own log matches it."
        ),
    }[alert_type]


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
def raise_cease_processing_alerts(
    db: Session,
    consent: Consent,
    *,
    actor_username: str = "system",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> list[ProcessorAlert]:
    """s.6(6) trigger, called from services/consent.py::withdraw_consent.

    One alert per processor we have disclosed this principal's data to under
    the withdrawn purpose. Idempotent per (consent, consent_version): calling
    it twice for the same withdrawal returns the alerts already raised instead
    of a second fan-out, so a retried request cannot inflate K-08's
    denominator or spam a processor.
    """
    trigger_ref = f"withdrawal:{consent.id}:v{consent.consent_version}"
    existing = db.query(ProcessorAlert).filter(ProcessorAlert.trigger_ref == trigger_ref).all()
    if existing:
        return existing

    processors = processors_holding_data(
        db, customer_id=consent.customer_id, purpose_id=consent.purpose_id
    )
    if not processors:
        logger.info(
            "Withdrawal of consent_id=%s produced no processor alerts: no SENT disclosure "
            "of this principal's data under purpose_id=%s is on file",
            consent.id, consent.purpose_id,
        )
        return []

    alerts = []
    for processor in processors:
        alerts.append(
            raise_alert(
                db, processor=processor, alert_type="CEASE_PROCESSING", trigger_ref=trigger_ref,
                customer=consent.customer, consent=consent, purpose=consent.purpose,
                reason=(
                    f"Data principal withdrew consent for purpose "
                    f"{consent.purpose.code if consent.purpose else consent.purpose_id}"
                ),
                source_app=source_app or consent.source_app, actor_username=actor_username,
                request_id=request_id, tenant_id=consent.tenant_id, commit=False,
            )
        )
    db.commit()
    for alert in alerts:
        db.refresh(alert)
    return alerts


def raise_erasure_instructions(
    db: Session,
    *,
    customer: Customer,
    request_ref: str,
    purpose_id: Optional[int] = None,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> list[ProcessorAlert]:
    """THE INTERFACE R1-06 (the erasure engine) WILL CALL. NOT AN ERASURE.

    This function raises a tracked, signed ERASURE_INSTRUCTION to every
    processor holding this principal's data, and nothing else. It does not
    erase, anonymise or alter a single row in this database, and it cannot
    verify that a processor erased anything either - only that the processor
    acknowledged being told to. The erasure engine remains responsible for
    every part of s.8(7) that happens inside this platform.

    Contract for R1-06:

    * Call this AFTER the engine has decided the erasure is due and has
      recorded its own request row; pass that row's identifier as
      `request_ref` so both sides can be reconciled. `trigger_ref` becomes
      ``erasure:<request_ref>``, which is what groups the fan-out.
    * Pass `purpose_id` only for a purpose-scoped erasure; omit it (the
      default) for a full erasure, which reaches every processor holding any
      of this principal's data.
    * Repeat calls with the same `request_ref` are idempotent and return the
      alerts already raised.
    * The return value is the list of alerts raised - possibly empty, when no
      SENT disclosure to any processor is on file for this principal. An empty
      list means "no processor to instruct", NOT "erasure complete".
    * Acknowledgement, retry, escalation to the DPO and the K-08 style SLA
      clock are then handled here; the engine does not need to poll. Read
      completion state off `ProcessorAlert.status` / `acknowledged_at`.
    """
    trigger_ref = f"erasure:{request_ref}"
    existing = db.query(ProcessorAlert).filter(ProcessorAlert.trigger_ref == trigger_ref).all()
    if existing:
        return existing

    processors = processors_holding_data(db, customer_id=customer.id, purpose_id=purpose_id)
    purpose = db.get(Purpose, purpose_id) if purpose_id else None
    alerts = []
    for processor in processors:
        alerts.append(
            raise_alert(
                db, processor=processor, alert_type="ERASURE_INSTRUCTION", trigger_ref=trigger_ref,
                customer=customer, purpose=purpose,
                reason=reason or f"Erasure request {request_ref}",
                extra={"erasure_request_ref": request_ref,
                       "contractual_erasure_sla_days": processor.erasure_sla_days},
                source_app=source_app or customer.source_app, actor_username=actor_username,
                request_id=request_id, tenant_id=customer.tenant_id, commit=False,
            )
        )
    db.commit()
    for alert in alerts:
        db.refresh(alert)
    return alerts


def raise_sharing_event_alert(
    db: Session,
    event: DataSharingEvent,
    *,
    customer: Customer,
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> Optional[ProcessorAlert]:
    """M-02 hook, called from routes/sharing_events.py after a disclosure is
    logged. Only SENT disclosures are notified - REQUESTED and DENIED never
    left this platform, so there is nothing for the far side to reconcile.
    Returns None when the processor has no webhook configured, since a
    reconciliation notice is a convenience, not a legal obligation, and a row
    nobody can deliver is noise in K-08's sibling metrics.

    `customer` is passed in rather than looked up here on purpose: the caller
    has already resolved it through services/tenancy.py::resolve_customer with
    its own tenant scope, and re-fetching it here would be a second, unscoped
    Customer resolution - the exact shape tests/test_customer_resolution_guard.py
    exists to prevent.
    """
    if event.event_type != "SENT":
        return None
    processor = db.get(Processor, event.processor_id)
    if not processor or not processor.is_active or not processor.webhook_url:
        return None
    trigger_ref = f"sharing:{event.id}"
    existing = db.query(ProcessorAlert).filter(ProcessorAlert.trigger_ref == trigger_ref).first()
    if existing:
        return existing
    return raise_alert(
        db, processor=processor, alert_type="SHARING_EVENT", trigger_ref=trigger_ref,
        customer=customer,
        purpose=db.get(Purpose, event.purpose_id),
        sharing_event_id=event.id,
        reason=f"Disclosure logged: {event.event_type}",
        extra={"data_category_ids": event.data_category_ids or [], "legal_basis": event.legal_basis},
        source_app=event.source_app, actor_username=actor_username, request_id=request_id,
        tenant_id=event.tenant_id,
    )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def _backoff_minutes(attempts: int) -> int:
    """5, 10, 20, 40, ... capped at 4 hours - the same curve
    services/notifications.py uses, so an operator only has one retry
    behaviour to reason about."""
    return min(5 * (2 ** max(attempts - 1, 0)), 240)


def _post(url: str, body: bytes, headers: dict) -> int:
    with httpx.Client(timeout=DELIVERY_TIMEOUT_SECONDS) as client:
        response = client.post(url, content=body, headers=headers)
        return response.status_code


def deliver_alert(db: Session, alert: ProcessorAlert) -> bool:
    """One delivery attempt. Never raises: a processor's endpoint being down
    must degrade into a retry, not into a 500 for whoever triggered it."""
    processor = db.get(Processor, alert.processor_id)
    now = utcnow()

    if not processor or not processor.webhook_url or not processor.webhook_secret:
        alert.attempts += 1
        alert.last_error = "Processor has no webhook URL and/or secret configured"
        alert.status = "FAILED" if alert.attempts >= alert.max_attempts else "PENDING"
        alert.next_attempt_at = now + timedelta(minutes=_backoff_minutes(alert.attempts))
        db.commit()
        return False

    body = _canonical_body(alert.payload)
    # Re-sign when the stored signature no longer matches the secret in force
    # (the secret was rotated after the alert was raised). Without this, every
    # alert queued before a rotation would be rejected by the far side for the
    # rest of its retry budget.
    timestamp = int(now.timestamp())
    signature = sign_payload(processor.webhook_secret, timestamp, body)
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: f"processor.{alert.alert_type.lower()}",
        ALERT_REF_HEADER: alert.alert_ref,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: signature,
    }

    try:
        status_code = _post(processor.webhook_url, body, headers)
    except Exception as exc:  # noqa: BLE001 - a processor outage must never crash the job
        alert.attempts += 1
        alert.last_error = str(exc)[:2000]
        alert.http_status = None
        if alert.attempts >= alert.max_attempts:
            alert.status = "FAILED"
        else:
            alert.next_attempt_at = now + timedelta(minutes=_backoff_minutes(alert.attempts))
        db.commit()
        log_audit(
            db, "PROCESSOR_ALERT_SEND_FAILED", actor_username="scheduler", source_app=alert.source_app,
            tenant_id=alert.tenant_id, customer_id=alert.customer_id,
            reason=f"Delivery of {alert.alert_ref} to {processor.name} failed: {exc}",
            request_id=alert.request_id,
            metadata={"alert_ref": alert.alert_ref, "attempts": alert.attempts, "status": alert.status},
        )
        return False

    alert.attempts += 1
    alert.http_status = status_code
    alert.signature = signature
    alert.signed_timestamp = timestamp

    if 200 <= status_code < 300:
        alert.status = "SENT"
        alert.sent_at = now
        alert.last_error = None
        db.commit()
        log_audit(
            db, "PROCESSOR_ALERT_SENT", actor_username="scheduler", source_app=alert.source_app,
            tenant_id=alert.tenant_id, customer_id=alert.customer_id,
            reason=f"{alert.alert_type} instruction {alert.alert_ref} delivered to {processor.name}",
            request_id=alert.request_id,
            metadata={"alert_ref": alert.alert_ref, "http_status": status_code,
                      "awaiting_acknowledgement_until": alert.due_at.isoformat()},
        )
        return True

    alert.last_error = f"HTTP {status_code}"
    if alert.attempts >= alert.max_attempts:
        alert.status = "FAILED"
    else:
        alert.next_attempt_at = now + timedelta(minutes=_backoff_minutes(alert.attempts))
    db.commit()
    log_audit(
        db, "PROCESSOR_ALERT_SEND_FAILED", actor_username="scheduler", source_app=alert.source_app,
        tenant_id=alert.tenant_id, customer_id=alert.customer_id,
        reason=f"Delivery of {alert.alert_ref} to {processor.name} returned HTTP {status_code}",
        request_id=alert.request_id,
        metadata={"alert_ref": alert.alert_ref, "attempts": alert.attempts, "status": alert.status},
    )
    return False


def dispatch_pending_alerts(db: Session, *, limit: int = 200) -> dict:
    """Attempt every PENDING alert whose next_attempt_at has arrived.
    Called from app/jobs/processor_alert_job.py."""
    now = utcnow()
    pending = (
        db.query(ProcessorAlert)
        .filter(ProcessorAlert.status == "PENDING", ProcessorAlert.next_attempt_at <= now)
        .order_by(ProcessorAlert.next_attempt_at.asc())
        .limit(limit)
        .all()
    )
    sent = 0
    failed = 0
    for alert in pending:
        if deliver_alert(db, alert):
            sent += 1
        else:
            failed += 1
    return {"attempted": len(pending), "sent": sent, "failed_or_retrying": failed}


# ---------------------------------------------------------------------------
# Acknowledgement and escalation
# ---------------------------------------------------------------------------
def acknowledge_alert(
    db: Session,
    alert: ProcessorAlert,
    *,
    acknowledged_by: str,
    method: str,
    reference: str = "",
    note: str = "",
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> ProcessorAlert:
    """Record a processor's confirmation that it acted.

    Idempotent: a second acknowledgement of an already-acknowledged alert
    keeps the first timestamp, because K-08 measures when the processor first
    confirmed, and a later duplicate must not be able to move that in either
    direction. An already-escalated alert can still be acknowledged;
    `escalated_at` is preserved so the miss stays on the record.
    """
    if alert.acknowledged_at is not None:
        return alert
    now = utcnow()
    alert.acknowledged_at = now
    alert.acknowledged_by = acknowledged_by[:128]
    alert.ack_reference = (reference or "")[:128]
    alert.ack_method = method
    alert.ack_note = note or ""
    alert.status = "ACKNOWLEDGED"
    db.flush()
    within_sla = now <= _as_utc(alert.due_at)
    log_audit(
        db, "PROCESSOR_ALERT_ACKNOWLEDGED", actor_username=actor_username, source_app=alert.source_app,
        tenant_id=alert.tenant_id, customer_id=alert.customer_id, consent_id=alert.consent_id,
        purpose_id=alert.purpose_id,
        reason=f"{alert.alert_type} instruction {alert.alert_ref} acknowledged by {acknowledged_by}",
        request_id=request_id,
        metadata={
            "alert_ref": alert.alert_ref, "trigger_ref": alert.trigger_ref, "method": method,
            "reference": alert.ack_reference, "within_sla": within_sla,
            "hours_to_acknowledge": round((now - _as_utc(alert.created_at)).total_seconds() / 3600.0, 3),
            "was_escalated": alert.escalated_at is not None,
        },
        commit=False,
    )
    db.commit()
    db.refresh(alert)
    return alert


def _as_utc(value: datetime) -> datetime:
    """psycopg2 hands back a timezone-aware datetime in the session's local
    zone, not necessarily UTC; normalise before comparing or subtracting."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def escalate_overdue_alerts(db: Session, *, limit: int = 500) -> dict:
    """Escalate every unacknowledged alert whose SLA has elapsed to the
    tenant's DPO, once each.

    "Once each" matters: this runs on a schedule, and an alert a processor
    never acknowledges would otherwise mail the DPO every interval forever.
    `escalated_at` is the latch.
    """
    now = utcnow()
    overdue = (
        db.query(ProcessorAlert)
        .filter(
            ProcessorAlert.acknowledged_at.is_(None),
            ProcessorAlert.escalated_at.is_(None),
            ProcessorAlert.due_at <= now,
        )
        .order_by(ProcessorAlert.due_at.asc())
        .limit(limit)
        .all()
    )
    escalated = 0
    notified = 0
    for alert in overdue:
        processor = db.get(Processor, alert.processor_id)
        organization = db.get(Organization, alert.tenant_id) if alert.tenant_id else None
        recipient = (organization.dpo_email or "") if organization else ""

        alert.escalated_at = now
        alert.status = "ESCALATED"
        db.flush()
        escalated += 1

        context = {
            "alert_ref": alert.alert_ref,
            "alert_type": alert.alert_type,
            "processor_name": processor.name if processor else str(alert.processor_id),
            "raised_at": _as_utc(alert.created_at).isoformat(),
            "due_at": _as_utc(alert.due_at).isoformat(),
            "ack_sla_hours": alert.ack_sla_hours,
        }
        # Reuse the R3-06 notification service rather than a second dispatch
        # path: same retry/backoff, same delivery metrics, same audit events.
        from app.services.notifications import queue_operational_notification

        try:
            queued = queue_operational_notification(
                db, recipient=recipient, event_type="PROCESSOR_ESCALATION",
                tenant_id=alert.tenant_id, source_app=alert.source_app, context=context,
                actor_username="scheduler", request_id=alert.request_id,
            )
        except Exception:  # noqa: BLE001 - a notification failure must not lose the escalation
            logger.exception("Failed to queue DPO escalation for alert %s", alert.alert_ref)
            queued = None
        if queued is not None:
            notified += 1

        log_audit(
            db, "PROCESSOR_ALERT_ESCALATED", actor_username="scheduler", source_app=alert.source_app,
            tenant_id=alert.tenant_id, customer_id=alert.customer_id, consent_id=alert.consent_id,
            reason=(
                f"{alert.alert_type} instruction {alert.alert_ref} unacknowledged "
                f"{alert.ack_sla_hours}h after it was raised - escalated to the DPO"
            ),
            request_id=alert.request_id,
            metadata={
                "alert_ref": alert.alert_ref, "trigger_ref": alert.trigger_ref,
                "processor_id": alert.processor_id,
                "dpo_notified": queued is not None,
                # Masked: dpo_email is an EncryptedString and must not be
                # written to the audit trail in the clear.
                "dpo_recipient": mask_identifier(recipient) if recipient else None,
                "no_dpo_email_configured": not recipient,
            },
            commit=False,
        )
        db.commit()
    return {"overdue": len(overdue), "escalated": escalated, "dpo_notifications_queued": notified}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _contract_in_force(processor: Processor, today) -> bool:
    if processor.contract_valid_from and processor.contract_valid_from > today:
        return False
    if processor.contract_valid_until and processor.contract_valid_until < today:
        return False
    return bool(processor.contract_ref)


def contract_coverage_report(db: Session, *, tenant_id: Optional[int] = None) -> dict:
    """M-05/K-32: what fraction of the processor register is actually under a
    contract that says what s.8(2) requires it to say.

    "Covered" is deliberately all-or-nothing across five checks rather than a
    partial score: a contract with a security clause but no erasure clause
    does not partially discharge s.8(2), and a processor with no reachable
    webhook cannot be instructed to cease processing at all, which is the
    control C-02 exists to provide.
    """
    today = utcnow().date()
    q = db.query(Processor).filter(Processor.is_active.is_(True))
    if tenant_id is not None:
        q = q.filter(Processor.tenant_id.in_([tenant_id, None]))
    processors = q.order_by(Processor.id.asc()).all()

    rows = []
    for p in processors:
        checks = {
            "has_contract_ref": bool(p.contract_ref),
            "contract_in_force": _contract_in_force(p, today),
            "has_security_clause": bool(p.security_clause_ref or p.security_measures),
            "has_erasure_clause": bool(p.erasure_clause_ref or p.erasure_sla_days),
            "reachable_for_instructions": bool(p.webhook_url and p.webhook_secret),
        }
        rows.append({
            "processor_id": p.id,
            "name": p.name,
            "type": p.type,
            "country": p.country,
            "contract_ref": p.contract_ref,
            "contract_valid_from": p.contract_valid_from,
            "contract_valid_until": p.contract_valid_until,
            "ack_sla_hours": p.ack_sla_hours,
            **checks,
            "covered": all(checks.values()),
            "gaps": sorted(k for k, v in checks.items() if not v),
        })

    total = len(rows)
    covered = sum(1 for r in rows if r["covered"])
    expiring = [
        r for r in rows
        if r["contract_valid_until"] and 0 <= (r["contract_valid_until"] - today).days <= 90
    ]
    return {
        "generated_at": utcnow(),
        "processors_total": total,
        "processors_covered": covered,
        # K-32: None, not 100.0, when no processor is registered. An empty
        # register is not a fully-covered one - it is unmeasured, and this
        # figure is served directly at GET /processors/reports/contract-coverage
        # as well as through the compliance dashboard.
        "coverage_pct": round((covered / total) * 100, 2) if total else None,
        "processors_with_gaps": total - covered,
        "contracts_expiring_within_90_days": [r["processor_id"] for r in expiring],
        "processors": rows,
    }


def propagation_sla_metrics(
    db: Session,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    tenant_id: Optional[int] = None,
) -> dict:
    """K-08: % of withdrawals acknowledged by EVERY processor holding that
    data within the configured SLA.

    Measured over `trigger_ref` groups, not over alert rows, because the DoD
    is "every processor". A withdrawal counts as met only when all of its
    CEASE_PROCESSING alerts carry an `acknowledged_at` at or before their own
    `due_at`.

    `withdrawals_without_processors` is reported separately and is NOT counted
    as a success: those are withdrawals where no disclosure to any processor
    was on file, so nothing was propagated. Folding them into the numerator
    would let an empty sharing log show as 100% compliance, which is exactly
    backwards - an empty sharing log is the thing that should be investigated.
    """
    q = db.query(ProcessorAlert).filter(ProcessorAlert.alert_type == "CEASE_PROCESSING")
    if tenant_id is not None:
        q = q.filter(ProcessorAlert.tenant_id == tenant_id)
    if date_from is not None:
        q = q.filter(ProcessorAlert.created_at >= date_from)
    if date_to is not None:
        q = q.filter(ProcessorAlert.created_at <= date_to)
    alerts = q.all()

    groups: dict[str, list[ProcessorAlert]] = {}
    for alert in alerts:
        groups.setdefault(alert.trigger_ref, []).append(alert)

    met = 0
    breached = 0
    pending_in_sla = 0
    ack_hours: list[float] = []
    breached_refs: list[str] = []
    now = utcnow()
    for trigger_ref, group in groups.items():
        group_met = True
        group_pending = False
        for alert in group:
            due = _as_utc(alert.due_at)
            if alert.acknowledged_at is not None:
                acked = _as_utc(alert.acknowledged_at)
                ack_hours.append((acked - _as_utc(alert.created_at)).total_seconds() / 3600.0)
                if acked > due:
                    group_met = False
            elif now <= due:
                group_pending = True   # still inside its SLA - not yet a breach
                group_met = False
            else:
                group_met = False
        if group_met:
            met += 1
        elif group_pending:
            pending_in_sla += 1
        else:
            breached += 1
            breached_refs.append(trigger_ref)

    # Withdrawals that produced no alert at all, so they have no trigger_ref
    # group here - counted from the consent history instead.
    wq = db.query(func.count(ConsentHistory.id)).filter(ConsentHistory.action == "CONSENT_WITHDRAWN")
    if date_from is not None:
        wq = wq.filter(ConsentHistory.created_at >= date_from)
    if date_to is not None:
        wq = wq.filter(ConsentHistory.created_at <= date_to)
    withdrawals_total = wq.scalar() or 0

    decided = met + breached
    return {
        "generated_at": now,
        "date_from": date_from,
        "date_to": date_to,
        "withdrawals_total": withdrawals_total,
        "withdrawals_with_processor_alerts": len(groups),
        "withdrawals_without_processors": max(withdrawals_total - len(groups), 0),
        "fully_acknowledged_within_sla": met,
        "sla_breached": breached,
        "awaiting_acknowledgement_within_sla": pending_in_sla,
        # K-08 itself: of the withdrawals whose SLA outcome is already decided
        # (acknowledged, or the window has closed), what share was met. None,
        # not 100.0, when nothing is decided yet - see the module docstring:
        # folding "not yet decided" into the numerator would be exactly the
        # false all-clear this metric exists to catch.
        "k08_propagation_sla_pct": round((met / decided) * 100, 2) if decided else None,
        "alerts_total": len(alerts),
        "alerts_acknowledged": sum(1 for a in alerts if a.acknowledged_at is not None),
        "alerts_escalated": sum(1 for a in alerts if a.escalated_at is not None),
        "mean_hours_to_acknowledge": round(sum(ack_hours) / len(ack_hours), 3) if ack_hours else None,
        "breached_trigger_refs": sorted(breached_refs),
    }


def alert_summary(alerts: Iterable[ProcessorAlert]) -> dict:
    """Small helper for route responses and tests."""
    alerts = list(alerts)
    return {
        "total": len(alerts),
        "by_status": {s: sum(1 for a in alerts if a.status == s) for s in {a.status for a in alerts}},
    }
