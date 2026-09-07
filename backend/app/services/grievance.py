"""R2-06 (G-01..G-04): grievance redressal - reference issue, the response
clock, escalation to the DPO, resolution and complainant feedback.

Every state change in a grievance's life goes through this module. Routes
validate input and authorisation; they never assign `Grievance.status`
directly, exactly as `services/consent.py` owns every consent transition.
Each transition here does four things in one unit of work:

  1. validate the move against `GRIEVANCE_TRANSITIONS`;
  2. stamp the timestamps that make the SLA provable;
  3. append a `GrievanceEvent` (the per-complaint timeline the queue and the
     complainant's own view render); and
  4. write an `audit_logs` row via `log_audit`.

(4) is the record a regulator sees, and `audit_logs` is hash-chained and
append-only: a row is written once and never touched again, so every call
here passes the final values, never a placeholder it means to update later.

The response period
-------------------
`Organization.grievance_response_days` is the single source of truth and a
database CHECK constraint already caps it at 90. `response_days_for_tenant`
reads it and clamps defensively; nothing in this module hardcodes a period.
The value is snapshotted onto the grievance at receipt so a later change to
the tenant's published period cannot move the deadline of a complaint
already in flight.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.grievance import (
    DEFAULT_GRIEVANCE_RESPONSE_DAYS,
    GRIEVANCE_TERMINAL_STATUSES,
    GRIEVANCE_TRANSITIONS,
    MAX_GRIEVANCE_RESPONSE_DAYS,
    Grievance,
    GrievanceEvent,
)
from app.services import notifications as notification_service
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id

logger = logging.getLogger("app.grievance")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """A datetime that has been flushed but not reloaded still carries
    whatever the caller set; Postgres hands back tz-aware values. Normalise
    before any comparison or arithmetic so the two never mix."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
#  Reference numbers
# --------------------------------------------------------------------------- #

# Crockford-style base32 minus the characters people mistranscribe when
# reading a reference off a screen and into a Board complaint form: I, L, O,
# U and the digits 0 and 1. 30 symbols, 12 characters -> ~58 bits of entropy,
# which is not guessable and leaves collisions astronomically unlikely even
# before the uniqueness check below.
_REFERENCE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_REFERENCE_LENGTH = 12
_REFERENCE_PREFIX = "GRV"


def _random_reference(now: datetime) -> str:
    body = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(_REFERENCE_LENGTH))
    return f"{_REFERENCE_PREFIX}-{now.year}-{body[:4]}-{body[4:8]}-{body[8:]}"


def generate_reference_no(db: Session, *, now: Optional[datetime] = None, attempts: int = 8) -> str:
    """A unique, non-guessable reference for a new grievance.

    Three properties, all of them requirements rather than preferences:

    * **Non-sequential.** This is the identifier a complainant quotes to the
      Data Protection Board, and it is handed to someone outside the
      organisation. Deriving it from the primary key (or any counter) would
      publish how many complaints the register holds and let anyone holding
      one reference walk to their neighbours' by decrementing it.
    * **CSPRNG.** `secrets`, never `random`: `random`'s Mersenne Twister is
      fully reconstructible from a few hundred outputs, so a reference drawn
      from it is predictable to anyone who has collected some - which, for a
      grievance reference, includes anyone who has filed a few complaints.
    * **Unique.** Enforced by the column's unique index; this loop only
      avoids surfacing that as a 500 on the astronomically rare collision.

    The year segment is cosmetic (it makes a reference readable and sortable
    by a human handling paper) and carries no entropy assumption.
    """
    when = now or utcnow()
    for _ in range(attempts):
        candidate = _random_reference(when)
        if db.query(Grievance.id).filter(Grievance.reference_no == candidate).first() is None:
            return candidate
    # Practically unreachable: 8 consecutive collisions at ~58 bits each.
    raise RuntimeError("Could not generate a unique grievance reference number")


# --------------------------------------------------------------------------- #
#  The response period
# --------------------------------------------------------------------------- #

def response_days_for_tenant(db: Session, tenant_id: Optional[int]) -> int:
    """The tenant's own published grievance response period, in days.

    Read from `organizations.grievance_response_days` - the single source of
    truth, the same value `GET /public/{tenant_code}/rights` publishes to the
    world. The clamp is defensive only (a tenant row auto-provisioned by
    `resolve_tenant_id` takes the column default, and a row predating the
    <= 90 CHECK constraint could in principle hold anything); it must never
    be read as a second source of truth for the period itself.
    """
    from app.models.entities import Organization

    organization = db.get(Organization, tenant_id) if tenant_id is not None else None
    configured = getattr(organization, "grievance_response_days", None)
    if not isinstance(configured, int) or configured <= 0:
        logger.warning(
            "Tenant %s has no usable grievance_response_days (%r); falling back to %d days",
            tenant_id, configured, DEFAULT_GRIEVANCE_RESPONSE_DAYS,
        )
        return DEFAULT_GRIEVANCE_RESPONSE_DAYS
    if configured > MAX_GRIEVANCE_RESPONSE_DAYS:
        logger.warning(
            "Tenant %s publishes grievance_response_days=%d, above the %d-day ceiling; clamping",
            tenant_id, configured, MAX_GRIEVANCE_RESPONSE_DAYS,
        )
        return MAX_GRIEVANCE_RESPONSE_DAYS
    return configured


def due_date_for(received_at: datetime, response_days: int) -> datetime:
    return as_utc(received_at) + timedelta(days=response_days)


def tenant_contact(db: Session, tenant_id: Optional[int]) -> tuple[str, str, str]:
    """(dpo_name, dpo_email, board_complaint_url) for a tenant, or blanks."""
    from app.models.entities import Organization

    organization = db.get(Organization, tenant_id) if tenant_id is not None else None
    if organization is None:
        return "", "", ""
    return (
        organization.dpo_name or "",
        organization.dpo_email or "",
        organization.board_complaint_url or "",
    )


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #

# The DPO escalation is the one grievance notification whose recipient is NOT
# a data principal, so it wants its own event type and its own copy. That
# constant lives in `entities.py`'s NOTIFICATION_EVENT_TYPES, which this lane
# does not own; until it is added there, escalation falls back to the
# principal-facing GRIEVANCE_STATUS type (same table, same dispatcher, same
# retry/backoff and delivery metrics - only the wording is generic). The
# fallback template below is registered with `setdefault`, so it defines copy
# for the new key and never overwrites an existing one.
ESCALATION_EVENT_TYPE = "GRIEVANCE_ESCALATION"
PRINCIPAL_EVENT_TYPE = "GRIEVANCE_STATUS"

notification_service.FALLBACK_TEMPLATES.setdefault(
    ESCALATION_EVENT_TYPE,
    {
        "EMAIL": (
            "Overdue grievance {reference_no} escalated to you",
            "Grievance {reference_no} ({category}) was received on {received_at} and its "
            "published response period of {response_days} days elapsed on {due_at} without a "
            "resolution being recorded.\n\n"
            "Under DPDP Act s.13 the Data Principal is owed a response within the period this "
            "organisation publishes, and s.10(2)(a) makes the Data Protection Officer the point "
            "of contact for grievance redressal. Please take ownership of this complaint in the "
            "grievance queue and record the resolution against reference {reference_no}.",
        ),
        "SMS": ("", "Consent360: grievance {reference_no} is overdue and has been escalated to you."),
        "IN_APP": ("Grievance overdue", "Grievance {reference_no} passed its response deadline on {due_at}."),
    },
)


def _escalation_event_type() -> str:
    from app.models.entities import NOTIFICATION_EVENT_TYPES

    if ESCALATION_EVENT_TYPE in NOTIFICATION_EVENT_TYPES:
        return ESCALATION_EVENT_TYPE
    return PRINCIPAL_EVENT_TYPE


def _notify_principal(
    db: Session,
    grievance: Grievance,
    customer,
    *,
    status_text: str,
    details: str,
    actor_username: str,
) -> list[int]:
    """Queue a GRIEVANCE_STATUS notification to the complainant.

    Never lets a transport problem undo a state change: the grievance
    transition is the legally significant act and it has already been
    committed by the caller; a notification that could not be queued is
    logged (and visible as a missing row in `GET /notifications`) rather than
    rolled back on top of it.
    """
    if customer is None:
        return []
    try:
        queued = notification_service.queue_notification(
            db,
            customer=customer,
            event_type=PRINCIPAL_EVENT_TYPE,
            source_app=grievance.source_app,
            context={
                "status": status_text,
                "details": details,
                "reference_no": grievance.reference_no,
                "category": grievance.category,
                "due_at": as_utc(grievance.due_at).date().isoformat(),
                "response_days": grievance.response_days,
            },
            actor_username=actor_username,
        )
        return [n.id for n in queued]
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception(
            "Could not queue the grievance notification for %s", grievance.reference_no
        )
        return []


# --------------------------------------------------------------------------- #
#  Transitions
# --------------------------------------------------------------------------- #

class GrievanceTransitionError(ValueError):
    """An illegal move in the grievance lifecycle (see GRIEVANCE_TRANSITIONS)."""


def _append_event(
    db: Session,
    grievance: Grievance,
    *,
    event: str,
    from_status: str = "",
    to_status: str = "",
    note: str = "",
    actor_username: str = "system",
    visible_to_principal: bool = True,
) -> GrievanceEvent:
    row = GrievanceEvent(
        grievance_id=grievance.id,
        event=event,
        from_status=from_status,
        to_status=to_status,
        note=note,
        actor_username=actor_username,
        visible_to_principal=visible_to_principal,
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _transition(
    db: Session,
    grievance: Grievance,
    *,
    to_status: str,
    event: str,
    audit_event: str,
    reason: str,
    note: str = "",
    actor_username: str = "system",
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    actor_role: str = "",
    visible_to_principal: bool = True,
    metadata: Optional[dict] = None,
) -> Grievance:
    """The one place `Grievance.status` is assigned. Validates the move,
    appends the timeline entry and writes the audit row - all inside the
    caller's transaction, which the caller commits."""
    from_status = grievance.status
    if to_status != from_status and to_status not in GRIEVANCE_TRANSITIONS.get(from_status, []):
        raise GrievanceTransitionError(
            f"Cannot move grievance {grievance.reference_no} from {from_status} to {to_status}"
        )
    grievance.status = to_status
    grievance.updated_at = utcnow()
    db.flush()
    _append_event(
        db, grievance, event=event, from_status=from_status, to_status=to_status,
        note=note, actor_username=actor_username, visible_to_principal=visible_to_principal,
    )
    log_audit(
        db,
        audit_event,
        actor_username=actor_username,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        source_app=grievance.source_app,
        tenant_id=grievance.tenant_id,
        customer_id=grievance.customer_id,
        purpose_id=grievance.purpose_id,
        consent_id=grievance.consent_id,
        old_status=from_status,
        new_status=to_status,
        reason=reason,
        # Never the narrative: a grievance description is encrypted at rest,
        # and the audit ledger is exported wholesale to auditors and
        # regulators. Only the reference, the category and the SLA facts.
        metadata={
            "reference_no": grievance.reference_no,
            "category": grievance.category,
            "due_at": as_utc(grievance.due_at).isoformat(),
            "response_days": grievance.response_days,
            **(metadata or {}),
        },
        commit=False,
    )
    return grievance


# --------------------------------------------------------------------------- #
#  Lifecycle
# --------------------------------------------------------------------------- #

def create_grievance(
    db: Session,
    *,
    customer,
    category: str,
    description: str,
    subject: str = "",
    channel: str = "PORTAL",
    source_app: str,
    purpose_id: Optional[int] = None,
    consent_id: Optional[int] = None,
    received_at: Optional[datetime] = None,
    actor_username: str = "principal",
    actor_type: str = "PRINCIPAL",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> tuple[Grievance, list[int]]:
    """Register a grievance and acknowledge it in the same unit of work.

    The DoD is that a grievance gets a reference number *and* an
    acknowledgement on submission, so acknowledgement is not a separate
    manual step a handler might forget: the row is created RECEIVED, the
    complainant is notified, and it moves to ACKNOWLEDGED before this
    returns. Both states are recorded, so the trail still shows the moment of
    receipt distinctly from the moment of acknowledgement.

    Returns the grievance and the ids of the Notification rows queued for the
    complainant.
    """
    tenant_id = resolve_tenant_id(db, source_app)
    when = as_utc(received_at) if received_at else utcnow()
    response_days = response_days_for_tenant(db, tenant_id)

    grievance = Grievance(
        tenant_id=tenant_id,
        reference_no=generate_reference_no(db, now=when),
        customer_id=customer.id,
        consent_id=consent_id,
        purpose_id=purpose_id,
        category=category,
        channel=channel,
        subject=subject,
        description=description,
        status="RECEIVED",
        received_at=when,
        response_days=response_days,
        due_at=due_date_for(when, response_days),
        source_app=source_app,
        created_by=actor_username,
    )
    db.add(grievance)
    db.flush()

    # R1-06/G-02: lodging a grievance IS the data principal exercising her
    # rights, which is one of the two acts DPDP Rules 2025 R.8(1) read with
    # the Third Schedule measures its three-year inactivity clock from. A
    # principal who complains but never logs in again has plainly not
    # abandoned her relationship with the fiduciary, and her data must not be
    # erased out from under an open complaint.
    if actor_type == "PRINCIPAL":
        from app.services.erasure import touch_last_interaction

        touch_last_interaction(db, customer, channel="grievance")

    _append_event(
        db, grievance, event="GRIEVANCE_RECEIVED", to_status="RECEIVED",
        note=f"Grievance received via {channel}", actor_username=actor_username,
    )
    log_audit(
        db, "GRIEVANCE_RECEIVED", actor_username=actor_username, actor_type=actor_type,
        actor_id=actor_id, actor_role=actor_role, source_app=source_app, tenant_id=tenant_id,
        customer_id=customer.id, purpose_id=purpose_id, consent_id=consent_id,
        new_status="RECEIVED",
        reason=f"Grievance {grievance.reference_no} received ({category}) via {channel}",
        metadata={
            "reference_no": grievance.reference_no, "category": category, "channel": channel,
            "response_days": response_days, "due_at": as_utc(grievance.due_at).isoformat(),
        },
        commit=False,
    )
    db.commit()
    db.refresh(grievance)

    notification_ids = _notify_principal(
        db, grievance, customer,
        status_text="received and acknowledged",
        details=(
            f"Your grievance has been registered under reference {grievance.reference_no}. "
            f"We will respond within {response_days} days, by "
            f"{as_utc(grievance.due_at).date().isoformat()}."
        ),
        actor_username=actor_username,
    )

    grievance.acknowledged_at = utcnow()
    _transition(
        db, grievance, to_status="ACKNOWLEDGED", event="GRIEVANCE_ACKNOWLEDGED",
        audit_event="GRIEVANCE_ACKNOWLEDGED",
        reason=(
            f"Grievance {grievance.reference_no} acknowledged to the complainant; "
            f"response due {as_utc(grievance.due_at).date().isoformat()} "
            f"({response_days}-day published period)"
        ),
        note=f"Acknowledgement sent; response due by {as_utc(grievance.due_at).date().isoformat()}",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id, actor_role=actor_role,
        metadata={"notification_ids": notification_ids},
    )
    db.commit()
    db.refresh(grievance)
    return grievance, notification_ids


def update_grievance(
    db: Session,
    grievance: Grievance,
    *,
    assigned_to: Optional[str] = None,
    to_status: Optional[str] = None,
    note: str = "",
    visible_to_principal: bool = False,
    actor_username: str,
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> Grievance:
    """Triage: assignment, a move into IN_PROGRESS, and/or a handler note."""
    if assigned_to is not None:
        grievance.assigned_to = assigned_to
    if to_status:
        _transition(
            db, grievance, to_status=to_status, event="GRIEVANCE_UPDATED",
            audit_event="GRIEVANCE_UPDATED",
            reason=f"Grievance {grievance.reference_no} moved to {to_status}"
                   + (f"; assigned to {assigned_to}" if assigned_to else ""),
            note=note, actor_username=actor_username, actor_type=actor_type,
            actor_id=actor_id, actor_role=actor_role,
            visible_to_principal=visible_to_principal,
            metadata={"assigned_to": grievance.assigned_to},
        )
    else:
        grievance.updated_at = utcnow()
        db.flush()
        _append_event(
            db, grievance, event="GRIEVANCE_UPDATED", from_status=grievance.status,
            to_status=grievance.status, note=note, actor_username=actor_username,
            visible_to_principal=visible_to_principal,
        )
        log_audit(
            db, "GRIEVANCE_UPDATED", actor_username=actor_username, actor_type=actor_type,
            actor_id=actor_id, actor_role=actor_role, source_app=grievance.source_app,
            tenant_id=grievance.tenant_id, customer_id=grievance.customer_id,
            reason=f"Grievance {grievance.reference_no} updated"
                   + (f"; assigned to {assigned_to}" if assigned_to else ""),
            metadata={"reference_no": grievance.reference_no, "assigned_to": grievance.assigned_to},
            commit=False,
        )
    db.commit()
    db.refresh(grievance)
    return grievance


def resolve_grievance(
    db: Session,
    grievance: Grievance,
    customer,
    *,
    resolution_summary: str,
    actor_username: str,
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> tuple[Grievance, list[int]]:
    """Record the resolution and tell the complainant.

    `within_period` is computed here and written into the audit row, not
    derived later from timestamps: it is the DoD's own measure ("closed
    within the published period") and it must be settled against the period
    that was actually published to this complainant, which is the snapshot on
    the row rather than whatever the tenant publishes today.
    """
    now = utcnow()
    within_period = now <= as_utc(grievance.due_at)
    grievance.resolved_at = now
    grievance.resolved_by = actor_username
    grievance.resolution_summary = resolution_summary
    _transition(
        db, grievance, to_status="RESOLVED", event="GRIEVANCE_RESOLVED",
        audit_event="GRIEVANCE_RESOLVED",
        reason=(
            f"Grievance {grievance.reference_no} resolved "
            f"{'within' if within_period else 'AFTER'} the published "
            f"{grievance.response_days}-day response period"
        ),
        note="Resolution recorded and sent to the complainant",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id, actor_role=actor_role,
        metadata={
            "within_published_period": within_period,
            "resolved_at": now.isoformat(),
            "days_taken": (now - as_utc(grievance.received_at)).days,
        },
    )
    db.commit()
    db.refresh(grievance)

    notification_ids = _notify_principal(
        db, grievance, customer,
        status_text="resolved",
        details=(
            f"Reference {grievance.reference_no}. Our response: {resolution_summary} "
            "If you are not satisfied, you may escalate to our Data Protection Officer or "
            "complain to the Data Protection Board."
        ),
        actor_username=actor_username,
    )
    return grievance, notification_ids


def close_grievance(
    db: Session,
    grievance: Grievance,
    *,
    note: str = "",
    actor_username: str,
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> Grievance:
    grievance.closed_at = utcnow()
    _transition(
        db, grievance, to_status="CLOSED", event="GRIEVANCE_CLOSED",
        audit_event="GRIEVANCE_CLOSED",
        reason=f"Grievance {grievance.reference_no} closed",
        note=note or "Grievance closed", actor_username=actor_username,
        actor_type=actor_type, actor_id=actor_id, actor_role=actor_role,
    )
    db.commit()
    db.refresh(grievance)
    return grievance


def record_feedback(
    db: Session,
    grievance: Grievance,
    *,
    rating: int,
    comment: str = "",
    actor_username: str = "principal",
) -> Grievance:
    """Capture the complainant's satisfaction with the resolution.

    Feedback does not change the grievance's status - it is an observation
    about a resolution that has already happened, and letting it reopen or
    close a complaint would make the SLA numbers depend on whether someone
    happened to fill in a form.
    """
    grievance.feedback_rating = rating
    grievance.feedback_comment = comment
    grievance.feedback_at = utcnow()
    grievance.updated_at = utcnow()
    db.flush()
    _append_event(
        db, grievance, event="GRIEVANCE_FEEDBACK", from_status=grievance.status,
        to_status=grievance.status, note=f"Complainant rated the resolution {rating}/5",
        actor_username=actor_username,
    )
    log_audit(
        db, "GRIEVANCE_FEEDBACK_RECORDED", actor_username=actor_username,
        actor_type="PRINCIPAL", source_app=grievance.source_app,
        tenant_id=grievance.tenant_id, customer_id=grievance.customer_id,
        reason=f"Complainant recorded feedback on grievance {grievance.reference_no}",
        # The rating is a number, not narrative; the free-text comment is
        # encrypted on the row and deliberately not copied into the ledger.
        metadata={"reference_no": grievance.reference_no, "rating": rating},
        commit=False,
    )
    db.commit()
    db.refresh(grievance)
    return grievance


def escalate_grievance(
    db: Session,
    grievance: Grievance,
    *,
    reason_code: str,
    note: str = "",
    actor_username: str = "system",
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> tuple[Grievance, Optional[int]]:
    """Escalate to the tenant's DPO and notify them.

    Idempotent by design: `escalated_at` is the latch. The escalation job
    runs on a schedule, and a grievance nobody resolves would otherwise mail
    the DPO on every interval forever - the same "once each" rule
    `services/processors.py::escalate_overdue_alerts` applies to processor
    SLAs, for the same reason.

    Returns the grievance and the id of the DPO Notification row (None when
    the tenant has no DPO email on file - which is itself worth surfacing,
    so it is logged and recorded in the timeline rather than silently
    swallowed).
    """
    if grievance.escalated_at is not None:
        return grievance, None

    dpo_name, dpo_email, _ = tenant_contact(db, grievance.tenant_id)
    now = utcnow()
    grievance.escalated_at = now
    grievance.escalated_to = dpo_email or dpo_name
    grievance.escalation_reason = reason_code

    _transition(
        db, grievance, to_status="ESCALATED", event="GRIEVANCE_ESCALATED",
        audit_event="GRIEVANCE_ESCALATED",
        reason=(
            f"Grievance {grievance.reference_no} escalated to the Data Protection Officer "
            f"({reason_code}); published response period of {grievance.response_days} days "
            f"expired {as_utc(grievance.due_at).date().isoformat()}"
        ),
        note=note or "Escalated to the Data Protection Officer",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id, actor_role=actor_role,
        metadata={
            "escalation_reason": reason_code,
            # The DPO address is a named individual's contact detail: recorded
            # (encrypted) on the row, referenced only as a boolean here.
            "dpo_contact_on_file": bool(dpo_email),
        },
    )
    db.commit()
    db.refresh(grievance)

    notification_id: Optional[int] = None
    if dpo_email:
        try:
            notification = notification_service.queue_operational_notification(
                db,
                recipient=dpo_email,
                event_type=_escalation_event_type(),
                tenant_id=grievance.tenant_id,
                source_app=grievance.source_app,
                channel="EMAIL",
                context={
                    "reference_no": grievance.reference_no,
                    "category": grievance.category,
                    "received_at": as_utc(grievance.received_at).date().isoformat(),
                    "due_at": as_utc(grievance.due_at).date().isoformat(),
                    "response_days": grievance.response_days,
                    # Filled only for the GRIEVANCE_STATUS fallback template,
                    # which addresses its reader by name.
                    "customer_name": dpo_name or "Data Protection Officer",
                    "status": "overdue and escalated to the Data Protection Officer",
                    "details": (
                        f"Grievance {grievance.reference_no} ({grievance.category}) passed its "
                        f"{grievance.response_days}-day response deadline on "
                        f"{as_utc(grievance.due_at).date().isoformat()} without a resolution."
                    ),
                },
                actor_username=actor_username,
            )
            if notification is not None:
                notification_id = notification.id
                grievance.escalation_notification_id = notification.id
                db.commit()
                db.refresh(grievance)
        except Exception:  # noqa: BLE001 - the escalation itself must stand
            logger.exception(
                "Escalated grievance %s but could not queue the DPO notification",
                grievance.reference_no,
            )
    else:
        logger.warning(
            "Grievance %s escalated but tenant %s has no DPO email on file - nobody was notified",
            grievance.reference_no, grievance.tenant_id,
        )
        _append_event(
            db, grievance, event="GRIEVANCE_ESCALATION_UNDELIVERABLE",
            from_status=grievance.status, to_status=grievance.status,
            note="No DPO contact is configured for this tenant, so no escalation email could be sent.",
            actor_username=actor_username, visible_to_principal=False,
        )
        db.commit()

    return grievance, notification_id


# --------------------------------------------------------------------------- #
#  The scheduled sweep
# --------------------------------------------------------------------------- #

def overdue_grievances(db: Session, *, now: Optional[datetime] = None, limit: int = 500) -> list[Grievance]:
    """Open grievances past their published response period that have not yet
    been escalated."""
    reference = now or utcnow()
    return (
        db.query(Grievance)
        .filter(
            Grievance.status.notin_(GRIEVANCE_TERMINAL_STATUSES),
            Grievance.escalated_at.is_(None),
            Grievance.due_at <= reference,
        )
        .order_by(Grievance.due_at.asc())
        .limit(limit)
        .all()
    )


def escalate_overdue_grievances(db: Session, *, now: Optional[datetime] = None, limit: int = 500) -> dict:
    """Escalate every overdue, unescalated grievance to its tenant's DPO.

    This is the automatic half of the DoD ("overdue items escalate
    automatically"), and it is a scheduled sweep rather than a check on the
    read path deliberately: a grievance nobody opens in the admin queue is
    exactly the one most likely to go past its deadline, so tying escalation
    to someone looking at it would escalate precisely the complaints that
    were already being handled and miss the ones that were not.

    Never raises for one bad row: a single grievance whose escalation fails
    (a broken DPO address, a transient database error) is logged, rolled back
    and skipped, so the rest of the sweep still runs. The `scheduler_runs`
    row that `JobRunner` writes carries the counts below.
    """
    reference = now or utcnow()
    pending = overdue_grievances(db, now=reference, limit=limit)
    escalated = 0
    notified = 0
    failed = 0
    for grievance in pending:
        try:
            _, notification_id = escalate_grievance(
                db, grievance, reason_code="RESPONSE_PERIOD_ELAPSED",
                note=(
                    f"Automatically escalated: the published {grievance.response_days}-day "
                    f"response period elapsed on "
                    f"{as_utc(grievance.due_at).date().isoformat()} without a resolution."
                ),
                actor_username="scheduler", actor_type="SYSTEM",
            )
            escalated += 1
            if notification_id is not None:
                notified += 1
        except Exception:  # noqa: BLE001 - one bad row must not abort the sweep
            db.rollback()
            failed += 1
            logger.exception("Failed to escalate overdue grievance id=%s", grievance.id)
    return {
        "grievances_overdue": len(pending),
        "grievances_escalated": escalated,
        "dpo_notifications_queued": notified,
        "grievance_escalations_failed": failed,
    }


# --------------------------------------------------------------------------- #
#  Queue metrics
# --------------------------------------------------------------------------- #

def queue_metrics(db: Session, *, source_app: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """The admin queue's header numbers plus the DoD's on-time closure rate.

    `resolved_within_period` compares each grievance's own `resolved_at`
    against its own snapshotted `due_at`, so the rate is measured against
    what each complainant was actually promised.
    """
    reference = now or utcnow()
    query = db.query(Grievance)
    if source_app:
        query = query.filter(Grievance.source_app == source_app)
    rows: list[Grievance] = query.all()

    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    open_count = overdue = escalated = 0
    resolved_total = resolved_within = 0
    total_days = 0.0

    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_category[row.category] = by_category.get(row.category, 0) + 1
        if row.status == "ESCALATED":
            escalated += 1
        if row.status in GRIEVANCE_TERMINAL_STATUSES:
            if row.resolved_at is not None:
                resolved_total += 1
                if as_utc(row.resolved_at) <= as_utc(row.due_at):
                    resolved_within += 1
                total_days += (as_utc(row.resolved_at) - as_utc(row.received_at)).total_seconds() / 86400
        else:
            open_count += 1
            if as_utc(row.due_at) <= reference:
                overdue += 1

    return {
        "total": len(rows),
        "open": open_count,
        "overdue": overdue,
        "escalated": escalated,
        "by_status": by_status,
        "by_category": by_category,
        "resolved_total": resolved_total,
        "resolved_within_period": resolved_within,
        "on_time_closure_rate": (
            round(resolved_within * 100.0 / resolved_total, 2) if resolved_total else None
        ),
        "average_days_to_resolution": (
            round(total_days / resolved_total, 2) if resolved_total else None
        ),
    }
