"""R2-05 (E-01, E-02, E-03, E-05, E-07, E-09): the data-principal rights
desk - intake, R.14(2) identity verification, the s.11 fulfilment package,
the s.12(1) correction workflow, the s.12(3) hand-off to the erasure engine,
s.14 nominations, and the K-20/K-21/K-22 metrics.

Every state change in a request's life goes through this module. Routes
validate input and authorisation; they never assign `RightsRequest.status`
directly, exactly as `services/consent.py` owns every consent transition and
`services/grievance.py` owns every grievance transition. Each transition here
does four things in one unit of work:

  1. validate the move against `RIGHTS_REQUEST_TRANSITIONS`;
  2. stamp the timestamps that make the SLA provable;
  3. append a `RightsRequestEvent` (the timeline the queue and the
     principal's own view render); and
  4. write an `audit_logs` row via `log_audit`.

(4) is the record a regulator sees, and `audit_logs` is hash-chained and
append-only: a row is written once and never touched again, so every call
here passes final values, never a placeholder it means to update later.

--------------------------------------------------------------------------
WHAT THIS MODULE DELIBERATELY DOES NOT DO
--------------------------------------------------------------------------
**It does not erase anything.** An approved ERASURE request calls
`app/services/erasure.py::approve_rights_request_erasure` and records the
`erasure_jobs` row that comes back. That engine owns the R.8(2) forty-eight
hour notice, the retention floors that outrank the principal's own request,
the named-authoriser requirement and the evidence hash - and its two database
CHECK constraints (`ck_erasure_jobs_executed_has_evidence`,
`ck_erasure_jobs_executed_is_authorised`) are the backstop for all of it. A
second deletion path here would be a second copy of those guarantees to keep
in step, and the first divergence would be an irreversible destruction of
somebody's data.

**It does not verify anyone's identity itself.** R.14(2) verification is
delegated to the two mechanisms that already exist: the email OTP in
`app/services/otp.py` (which owns the code generation, the constant-time
comparison, the attempt budget and the expiry) and the fiduciary assertion
recorded on a `ConsentContext` by `app/services/context.py`. This module only
*reads* `ConsentContext.verified_at` and copies the method onto the request.
There is no third path and no way for a handler to attest to an identity
themselves - see `app/models/rights.py`'s IDENTITY_VERIFICATION_METHODS.

**It does not dispatch notifications.** Everything goes through
`app/services/notifications.py::queue_notification` under the existing
REQUEST_STATUS event type, so rights-desk mail inherits the same templates,
retries, backoff and K-44/K-45 delivery metrics as everything else.

--------------------------------------------------------------------------
THE PERIODS
--------------------------------------------------------------------------
Two clocks, both snapshotted onto the request at receipt so a later change to
a tenant's published commitments cannot move the deadline of a request
already in flight:

* the **response period**, from `organizations.settings['rights_response_days']`
  if the tenant configured one, else `organizations.grievance_response_days`
  (already CHECK-constrained to <= 90). The fallback is stated rather than
  silent: R.14(3) fixes the published maximum for the grievance response, and
  a fiduciary publishing "we answer within N days" should not be running its
  rights desk on a different, unpublished clock. A tenant wanting a separate
  (shorter) rights clock sets the settings key.
* the **acknowledgement commitment**, from
  `organizations.settings['rights_acknowledgement_hours']`, default 72 - K-21's
  stated policy target. This one has no statutory number behind it and is a
  published service commitment, which is exactly why it is configurable and
  snapshotted rather than a constant in a comparison.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.encryption import hmac_digest
from app.models.rights import (
    CORRECTABLE_CUSTOMER_FIELDS,
    DEFAULT_RIGHTS_ACKNOWLEDGEMENT_HOURS,
    DEFAULT_RIGHTS_RESPONSE_DAYS,
    MAX_RIGHTS_ACKNOWLEDGEMENT_HOURS,
    MAX_RIGHTS_RESPONSE_DAYS,
    NOMINATION_TRANSITIONS,
    RIGHTS_REQUEST_TERMINAL_STATUSES,
    RIGHTS_REQUEST_TRANSITIONS,
    Nomination,
    RightsRequest,
    RightsRequestEvent,
)
from app.services import notifications as notification_service
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id

logger = logging.getLogger("app.rights")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalise a datetime to UTC.

    Two distinct hazards, and this handles both. A datetime that has been
    flushed but not reloaded still carries whatever the caller set, which may
    be naive - so a missing tzinfo is read as UTC. And Postgres hands a
    `timestamptz` back in the *session's* timezone, so the same instant this
    process wrote as ``...T05:00:51+00:00`` comes back as
    ``...T13:00:51+08:00``.

    That second case is why this converts rather than merely attaching. The
    two strings are the same moment and compare equal, but they do not
    *serialise* equal - and `compute_closure_hash` hashes ISO strings. Merely
    attaching UTC meant a closure hash computed before the write did not match
    one recomputed after the read, so an auditor checking the evidence would
    have found a mismatch and concluded the record had been tampered with.
    docs/ARCHITECTURE.md records the same requirement for the audit chain ("created_at is
    normalised to UTC before hashing"); this is that rule, applied here.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RightsRequestError(ValueError):
    """A refused rights-desk operation the caller can be told about."""


class RightsTransitionError(RightsRequestError):
    """An illegal move in the request lifecycle (see RIGHTS_REQUEST_TRANSITIONS)."""


class IdentityNotVerified(RightsRequestError):
    """R.14(2): the requester's identity has not been established, so the
    request cannot be fulfilled. Raised rather than returned as a flag so no
    call site can accidentally proceed past it."""


# --------------------------------------------------------------------------- #
#  Reference numbers
# --------------------------------------------------------------------------- #

# Crockford-style base32 minus the characters people mistranscribe when
# reading a reference off a screen: I, L, O, U and the digits 0 and 1.
# 30 symbols, 12 characters -> ~58 bits of entropy.
_REFERENCE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_REFERENCE_LENGTH = 12


def _random_reference(prefix: str, now: datetime) -> str:
    body = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(_REFERENCE_LENGTH))
    return f"{prefix}-{now.year}-{body[:4]}-{body[4:8]}-{body[8:]}"


def _unique_reference(
    db: Session, model, column, prefix: str, now: Optional[datetime], attempts: int = 8
) -> str:
    when = now or utcnow()
    for _ in range(attempts):
        candidate = _random_reference(prefix, when)
        if db.query(model.id).filter(column == candidate).first() is None:
            return candidate
    raise RuntimeError(f"Could not generate a unique {prefix} reference")


def generate_reference_no(db: Session, *, now: Optional[datetime] = None) -> str:
    """A unique, non-guessable reference for a new rights request.

    Three properties, all requirements rather than preferences:

    * **Non-sequential.** This identifier is handed to someone outside the
      organisation and quoted back in email and on the phone. Deriving it from
      the primary key (or any counter) would publish how many people have
      asked this fiduciary for their data - a number that is itself
      commercially and reputationally sensitive - and would let anyone holding
      one reference walk to their neighbours' by decrementing it. On a rights
      register that walk discloses *that a named individual asked to be
      erased*, which is personal data about them in its own right.
    * **CSPRNG.** `secrets`, never `random`: `random`'s Mersenne Twister state
      is fully reconstructible from a few hundred outputs, after which every
      subsequent reference is predictable - and an attacker only needs
      references they are entitled to see (file a few requests of their own).
    * **Unique.** Enforced by the column's unique index; the retry loop only
      avoids surfacing the astronomically rare collision as a 500.
    """
    return _unique_reference(db, RightsRequest, RightsRequest.reference_no, "RRQ", now)


def generate_nomination_ref(db: Session, *, now: Optional[datetime] = None) -> str:
    return _unique_reference(db, Nomination, Nomination.nomination_ref, "NOM", now)


# --------------------------------------------------------------------------- #
#  The periods
# --------------------------------------------------------------------------- #

def _tenant(db: Session, tenant_id: Optional[int]):
    from app.models.entities import Organization

    return db.get(Organization, tenant_id) if tenant_id is not None else None


def response_days_for_tenant(db: Session, tenant_id: Optional[int]) -> int:
    """The tenant's own published response period for a rights request.

    `organizations.settings['rights_response_days']` when the tenant has set
    one, otherwise `organizations.grievance_response_days` - see the module
    docstring for why that fallback is deliberate rather than a silent reuse.
    The clamp is defensive only (a tenant auto-provisioned by
    `resolve_tenant_id` takes the column default, and a settings blob is free
    JSON with no CHECK behind it), never a second source of truth.
    """
    organization = _tenant(db, tenant_id)
    configured: Any = None
    settings_blob = getattr(organization, "settings", None) or {}
    if isinstance(settings_blob, dict):
        configured = settings_blob.get("rights_response_days")
    if not isinstance(configured, int) or isinstance(configured, bool) or configured <= 0:
        configured = getattr(organization, "grievance_response_days", None)
    if not isinstance(configured, int) or isinstance(configured, bool) or configured <= 0:
        logger.warning(
            "Tenant %s has no usable rights response period (%r); falling back to %d days",
            tenant_id, configured, DEFAULT_RIGHTS_RESPONSE_DAYS,
        )
        return DEFAULT_RIGHTS_RESPONSE_DAYS
    if configured > MAX_RIGHTS_RESPONSE_DAYS:
        logger.warning(
            "Tenant %s publishes a rights response period of %d days, above the %d-day "
            "ceiling; clamping", tenant_id, configured, MAX_RIGHTS_RESPONSE_DAYS,
        )
        return MAX_RIGHTS_RESPONSE_DAYS
    return configured


def acknowledgement_hours_for_tenant(db: Session, tenant_id: Optional[int]) -> int:
    """The tenant's published acknowledgement commitment, in hours (K-21)."""
    organization = _tenant(db, tenant_id)
    settings_blob = getattr(organization, "settings", None) or {}
    configured = settings_blob.get("rights_acknowledgement_hours") if isinstance(settings_blob, dict) else None
    if not isinstance(configured, int) or isinstance(configured, bool) or configured <= 0:
        return DEFAULT_RIGHTS_ACKNOWLEDGEMENT_HOURS
    return min(configured, MAX_RIGHTS_ACKNOWLEDGEMENT_HOURS)


def due_date_for(received_at: datetime, response_days: int) -> datetime:
    return as_utc(received_at) + timedelta(days=response_days)


def tenant_contact(db: Session, tenant_id: Optional[int]) -> dict[str, str]:
    """R.9 / E-08: every rights response carries the contact. Returned as a
    dict so a caller can spread it into an acknowledgement or a package
    without four positional unpackings."""
    organization = _tenant(db, tenant_id)
    if organization is None:
        return {"dpo_name": "", "dpo_email": "", "rights_url": "",
                "grievance_url": "", "board_complaint_url": ""}
    return {
        "dpo_name": organization.dpo_name or "",
        "dpo_email": organization.dpo_email or "",
        "rights_url": organization.rights_url or "",
        "grievance_url": organization.grievance_url or "",
        "board_complaint_url": organization.board_complaint_url or "",
    }


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #

# REQUEST_STATUS already exists in entities.py's NOTIFICATION_EVENT_TYPES and
# is exactly this: a status update on a data-principal request. Reused rather
# than adding a rights-specific type, so the rights desk shares the templates,
# the retry/backoff and the K-44/K-45 delivery metrics with everything else.
PRINCIPAL_EVENT_TYPE = "REQUEST_STATUS"

notification_service.FALLBACK_TEMPLATES.setdefault(
    PRINCIPAL_EVENT_TYPE,
    {
        "EMAIL": (
            "Your request {reference_no} - {status}",
            "We have your {request_type} request under reference {reference_no}.\n\n"
            "{details}\n\n"
            "We publish a response period of {response_days} days, so you will hear from us "
            "by {due_at}. If you are not satisfied you may raise a grievance with our Data "
            "Protection Officer and, after that, complain to the Data Protection Board of India.",
        ),
        "SMS": ("", "Consent360: request {reference_no} - {status}. Response due by {due_at}."),
        "IN_APP": ("Request {reference_no}: {status}", "{details}"),
    },
)


def _notify_principal(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    status_text: str,
    details: str,
    actor_username: str,
) -> list[int]:
    """Queue a REQUEST_STATUS notification to the principal.

    Never lets a transport problem undo a state change: the transition is the
    legally significant act and the caller commits it; a notification that
    could not be queued is logged (and visible as a missing row in
    `GET /notifications`) rather than rolled back on top of it.
    """
    if customer is None:
        return []
    try:
        queued = notification_service.queue_notification(
            db,
            customer=customer,
            event_type=PRINCIPAL_EVENT_TYPE,
            source_app=request.source_app,
            context={
                "status": status_text,
                "details": details,
                "reference_no": request.reference_no,
                "request_type": request.request_type,
                "due_at": as_utc(request.due_at).date().isoformat(),
                "response_days": request.response_days,
            },
            actor_username=actor_username,
        )
        return [n.id for n in queued]
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception(
            "Could not queue the rights-request notification for %s", request.reference_no
        )
        return []


# --------------------------------------------------------------------------- #
#  Transitions
# --------------------------------------------------------------------------- #

def _append_event(
    db: Session,
    request: RightsRequest,
    *,
    event: str,
    from_status: str = "",
    to_status: str = "",
    note: str = "",
    actor_username: str = "system",
    visible_to_principal: bool = True,
) -> RightsRequestEvent:
    row = RightsRequestEvent(
        request_id=request.id,
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
    request: RightsRequest,
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
) -> RightsRequest:
    """The one place `RightsRequest.status` is assigned. Validates the move,
    appends the timeline entry and writes the audit row - all inside the
    caller's transaction, which the caller commits."""
    from_status = request.status
    if to_status != from_status and to_status not in RIGHTS_REQUEST_TRANSITIONS.get(from_status, []):
        raise RightsTransitionError(
            f"Cannot move rights request {request.reference_no} from {from_status} to {to_status}"
        )
    request.status = to_status
    request.updated_at = utcnow()
    db.flush()
    _append_event(
        db, request, event=event, from_status=from_status, to_status=to_status,
        note=note, actor_username=actor_username, visible_to_principal=visible_to_principal,
    )
    log_audit(
        db,
        audit_event,
        actor_username=actor_username,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        source_app=request.source_app,
        tenant_id=request.tenant_id,
        customer_id=request.customer_id,
        old_status=from_status,
        new_status=to_status,
        reason=reason,
        request_id=request.request_id,
        # Never the narrative: a request detail is encrypted at rest, and the
        # audit ledger is exported wholesale to auditors and regulators. Only
        # the reference, the type and the SLA facts.
        metadata={
            "reference_no": request.reference_no,
            "request_type": request.request_type,
            "due_at": as_utc(request.due_at).isoformat(),
            "response_days": request.response_days,
            "identity_verified": bool(request.identity_verified),
            **(metadata or {}),
        },
        commit=False,
    )
    return request


# --------------------------------------------------------------------------- #
#  Intake
# --------------------------------------------------------------------------- #

def create_request(
    db: Session,
    *,
    customer,
    request_type: str,
    request_detail: str = "",
    requested_changes: Optional[dict] = None,
    channel: str = "PORTAL",
    source_app: str,
    received_at: Optional[datetime] = None,
    verified_context=None,
    actor_username: str = "principal",
    actor_type: str = "PRINCIPAL",
    actor_id: Optional[str] = None,
    actor_role: str = "",
    request_id: Optional[str] = None,
) -> tuple[RightsRequest, list[int]]:
    """Register a rights request and acknowledge it in the same unit of work.

    The DoD is that every request type gets an acknowledgement and a due date,
    so acknowledgement is not a separate manual step a handler might forget:
    the row is created RECEIVED, the principal is notified, and it moves to
    ACKNOWLEDGED before this returns. Both states are recorded, so the trail
    still shows the moment of receipt distinctly from the moment of
    acknowledgement - which is what K-21 measures between.

    `verified_context` is a `ConsentContext` whose `verified_at` is set, when
    the principal submitted this herself through the portal. Passing an
    unverified context is a programming error and raises: the caller must have
    already refused it. A staff-logged request passes none and starts
    VERIFYING - it cannot be fulfilled until an OTP the handler never sees has
    been confirmed.

    Returns the request and the ids of the Notification rows queued.
    """
    tenant_id = resolve_tenant_id(db, source_app)
    when = as_utc(received_at) if received_at else utcnow()
    response_days = response_days_for_tenant(db, tenant_id)
    ack_hours = acknowledgement_hours_for_tenant(db, tenant_id)

    request = RightsRequest(
        tenant_id=tenant_id,
        reference_no=generate_reference_no(db, now=when),
        customer_id=customer.id,
        request_type=request_type,
        channel=channel,
        status="RECEIVED",
        request_detail=request_detail,
        requested_changes=dict(requested_changes or {}),
        received_at=when,
        acknowledgement_hours=ack_hours,
        acknowledgement_due_at=when + timedelta(hours=ack_hours),
        response_days=response_days,
        due_at=due_date_for(when, response_days),
        source_app=source_app,
        created_by=actor_username,
        request_id=request_id,
    )

    if verified_context is not None:
        if getattr(verified_context, "verified_at", None) is None:
            # Fail closed and loudly. A caller reaching here with an
            # unverified context has skipped the R.14(2) check, and quietly
            # recording the request as unverified would hide the bug rather
            # than surface it.
            raise IdentityNotVerified(
                "A rights request cannot be created against an unverified consent context"
            )
        _apply_verification(request, verified_context)

    db.add(request)
    db.flush()

    log_audit(
        db, "RIGHTS_REQUEST_RECEIVED",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        actor_role=actor_role, source_app=source_app, tenant_id=tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        new_status="RECEIVED", request_id=request_id,
        reason=f"{request_type} request received under reference {request.reference_no}",
        metadata={
            "reference_no": request.reference_no,
            "request_type": request_type,
            "channel": channel,
            "response_days": response_days,
            "due_at": as_utc(request.due_at).isoformat(),
            "acknowledgement_hours": ack_hours,
            "identity_verified": bool(request.identity_verified),
            "verification_method": request.verification_method,
        },
        commit=False,
    )
    _append_event(
        db, request, event="RECEIVED", to_status="RECEIVED",
        note=f"{request_type} request received via {channel}", actor_username=actor_username,
    )

    # R1-06/G-02: making a rights request IS the data principal exercising her
    # rights, which is one of the two acts DPDP Rules 2025 R.8(1) read with the
    # Third Schedule uses as the zero point of the three-year inactivity clock.
    # Never fatal - failing to refresh a clock must not deny a principal her
    # right to make the request.
    try:
        from app.services.erasure import touch_last_interaction

        touch_last_interaction(db, customer, channel="rights_request")
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to refresh last_interaction_at for customer_id=%s", customer.id
        )

    request.acknowledged_at = utcnow()
    contact = tenant_contact(db, tenant_id)
    notification_ids = _notify_principal(
        db, request, customer,
        status_text="Received and acknowledged",
        details=(
            f"We have received your {request_type.lower()} request and registered it under "
            f"reference {request.reference_no}."
        ),
        actor_username=actor_username,
    )
    _transition(
        db, request, to_status="ACKNOWLEDGED",
        event="ACKNOWLEDGED", audit_event="RIGHTS_REQUEST_ACKNOWLEDGED",
        reason=f"Acknowledgement issued for {request.reference_no}",
        note="Acknowledgement sent to the data principal",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        actor_role=actor_role,
        metadata={
            "notification_ids": notification_ids,
            "acknowledgement_hours": ack_hours,
            "data_protection_officer": contact["dpo_name"],
        },
    )

    # A request whose requester is not yet established goes straight into the
    # verification queue rather than sitting in ACKNOWLEDGED looking workable.
    if not request.identity_verified:
        _transition(
            db, request, to_status="VERIFYING",
            event="VERIFICATION_REQUIRED", audit_event="RIGHTS_REQUEST_VERIFICATION_REQUIRED",
            reason="R.14(2): the requester's identity has not been established",
            note=(
                "Identity verification is required before this request can be worked. A "
                "verification code will be sent to the contact details on file."
            ),
            actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
            actor_role=actor_role,
        )
    else:
        _transition(
            db, request, to_status="IN_PROGRESS",
            event="IN_PROGRESS", audit_event="RIGHTS_REQUEST_UPDATED",
            reason="Identity established at intake; request accepted for handling",
            note="Identity verified at intake via the principal's own verified session",
            actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
            actor_role=actor_role,
        )

    db.commit()
    db.refresh(request)
    return request, notification_ids


# --------------------------------------------------------------------------- #
#  R.14(2) identity verification
# --------------------------------------------------------------------------- #

def _apply_verification(request: RightsRequest, context) -> None:
    """Copy an established verification from a ConsentContext onto a request.

    The method is taken from the context, never chosen here: `EMAIL_OTP` is
    written by `otp.py::confirm_otp` and `FIDUCIARY_ASSERTED` by
    `context.py`, and conflating the two would let an OTP-only check
    downstream be satisfied by an assertion that never involved the
    principal (the same distinction `services/consent.py::_create_evidence`
    already relies on).
    """
    request.identity_verified = True
    request.verified_at = as_utc(context.verified_at)
    request.verification_method = context.verification_method or "EMAIL_OTP"
    request.verification_context_id = context.id


def start_identity_verification(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    actor_username: str = "system",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> "tuple[RightsRequest, object]":
    """Send the R.14(2) verification code for a request that arrived off-platform.

    Mints a consent context bound to this already-resolved customer and hands
    it to `app/services/otp.py::start_otp`, which sends a six-digit CSPRNG
    code to the address **on file** - not to any address the caller supplied -
    and stores only its HMAC. The handler who logged the request never sees
    the code; the person who receives it is by construction whoever controls
    the mailbox this platform already associated with the principal.

    That is the whole point: a request that arrived by phone is verified by
    the principal reading back a code only she received, not by a handler
    attesting that they recognised her.
    """
    from app.core.security import create_context_token
    from app.core.config import get_settings
    from app.models.entities import ConsentContext
    from app.services.otp import start_otp

    if request.identity_verified:
        return request, None

    now = utcnow()
    token = create_context_token(customer.id, request.source_app, request.request_id)
    context = ConsentContext(
        customer_id=customer.id,
        token=token,
        source_app=request.source_app,
        request_id=request.request_id,
        created_by=f"rights:{request.reference_no}",
        expires_at=now + timedelta(minutes=get_settings().CONTEXT_TOKEN_EXPIRE_MINUTES),
    )
    db.add(context)
    db.flush()

    # start_otp commits (it has to: a delivery failure must not leave a
    # phantom challenge behind), so the context row above is committed with it.
    start_otp(db, context, customer)

    request.verification_context_id = context.id
    request.updated_at = now
    _append_event(
        db, request, event="VERIFICATION_CODE_SENT",
        note=(
            "A verification code was sent to the contact details on file. It was not shown to "
            "the handler."
        ),
        actor_username=actor_username,
    )
    log_audit(
        db, "RIGHTS_REQUEST_VERIFICATION_SENT",
        actor_username=actor_username, actor_type="USER", actor_id=actor_id,
        actor_role=actor_role, source_app=request.source_app, tenant_id=request.tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"R.14(2) verification code issued for rights request {request.reference_no}",
        metadata={"reference_no": request.reference_no, "context_id": context.id},
        commit=False,
    )
    db.commit()
    db.refresh(request)
    return request, context


def confirm_identity_verification(
    db: Session,
    request: RightsRequest,
    code: str,
    *,
    actor_username: str = "system",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> bool:
    """Confirm the code and, on success, mark the request verified.

    The comparison, the attempt budget and the expiry all belong to
    `app/services/otp.py::confirm_otp`, which returns a bare boolean and makes
    a wrong code, an expired challenge, an exhausted budget and "no challenge
    at all" indistinguishable. This function adds nothing to that and
    deliberately reports the same undifferentiated failure upward.
    """
    from app.models.entities import ConsentContext
    from app.services.otp import confirm_otp

    if request.identity_verified:
        return True
    context = (
        db.get(ConsentContext, request.verification_context_id)
        if request.verification_context_id is not None
        else None
    )
    if context is None:
        return False
    # An independent tenant check on the persisted context row, exactly as
    # portal.py::_resolve_customer_and_context does: a context bound to a
    # different tenant's source_app must never verify this request.
    if context.source_app != request.source_app or context.customer_id != request.customer_id:
        return False

    if not confirm_otp(db, context, code):
        _append_event(
            db, request, event="VERIFICATION_FAILED",
            note="An identity verification attempt failed.",
            actor_username=actor_username, visible_to_principal=False,
        )
        db.commit()
        return False

    db.refresh(context)
    _apply_verification(request, context)
    request.verification_note = "Email OTP to the address on file"
    _append_event(
        db, request, event="IDENTITY_VERIFIED",
        note="The requester's identity was established by email OTP (DPDP Rules 2025 R.14(2)).",
        actor_username=actor_username,
    )
    log_audit(
        db, "RIGHTS_REQUEST_IDENTITY_VERIFIED",
        actor_username=actor_username, actor_type="USER", actor_id=actor_id,
        actor_role=actor_role, source_app=request.source_app, tenant_id=request.tenant_id,
        customer_id=request.customer_id,
        reason=f"Identity established for rights request {request.reference_no}",
        metadata={
            "reference_no": request.reference_no,
            "verification_method": request.verification_method,
        },
        commit=False,
    )
    if request.status == "VERIFYING":
        _transition(
            db, request, to_status="IN_PROGRESS",
            event="IN_PROGRESS", audit_event="RIGHTS_REQUEST_UPDATED",
            reason="Identity established; request accepted for handling",
            actor_username=actor_username, actor_type="USER", actor_id=actor_id,
            actor_role=actor_role,
        )
    db.commit()
    db.refresh(request)
    return True


def require_verified(request: RightsRequest) -> None:
    """The one guard every fulfilment path calls before doing anything the
    principal could be harmed by. The database CHECK behind it
    (`ck_rights_requests_fulfilled_is_verified`) is the backstop, not the
    primary control - this raises first so the caller gets a 409 explaining
    why rather than an IntegrityError."""
    if not request.identity_verified:
        raise IdentityNotVerified(
            f"Rights request {request.reference_no} cannot be fulfilled: the requester's "
            "identity has not been established (DPDP Rules 2025 R.14(2))"
        )


# --------------------------------------------------------------------------- #
#  Triage
# --------------------------------------------------------------------------- #

def update_request(
    db: Session,
    request: RightsRequest,
    *,
    assigned_to: Optional[str] = None,
    to_status: Optional[str] = None,
    note: str = "",
    visible_to_principal: bool = False,
    actor_username: str = "system",
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> RightsRequest:
    changes: dict[str, Any] = {}
    if assigned_to is not None and assigned_to != request.assigned_to:
        changes["assigned_to"] = assigned_to
        request.assigned_to = assigned_to
    if to_status and to_status != request.status:
        _transition(
            db, request, to_status=to_status,
            event=to_status, audit_event="RIGHTS_REQUEST_UPDATED",
            reason=f"Rights request {request.reference_no} moved to {to_status}",
            note=note, actor_username=actor_username, actor_type=actor_type,
            actor_id=actor_id, actor_role=actor_role,
            visible_to_principal=visible_to_principal,
            metadata=changes or None,
        )
    else:
        request.updated_at = utcnow()
        db.flush()
        if note or changes:
            _append_event(
                db, request, event="NOTE" if not changes else "ASSIGNED",
                note=note, actor_username=actor_username,
                visible_to_principal=visible_to_principal,
            )
            log_audit(
                db, "RIGHTS_REQUEST_UPDATED",
                actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
                actor_role=actor_role, source_app=request.source_app,
                tenant_id=request.tenant_id, customer_id=request.customer_id,
                reason=f"Rights request {request.reference_no} updated",
                metadata={"reference_no": request.reference_no, **changes},
                commit=False,
            )
    db.commit()
    db.refresh(request)
    return request


# --------------------------------------------------------------------------- #
#  s.11 - the access fulfilment package
# --------------------------------------------------------------------------- #

def _canonical_hash(payload: dict) -> str:
    """SHA-256 over canonical JSON: sorted keys, no whitespace. The same
    discipline `app/core/audit_chain.py` and
    `app/services/erasure.py::compute_evidence_hash` apply, so the hash is
    reproducible from the data by anyone reading it."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_access_package(db: Session, request: RightsRequest, customer) -> dict:
    """s.11(1)(a)-(b): the summary of personal data and processing activities,
    and the identities of everyone the data was shared with.

    The recipients limb comes from `data_sharing_events` - the register of
    disclosures that actually happened - and never from the processor
    register or a contract list. A processor under contract that was never
    actually sent anything is not a recipient of this person's data, and
    naming it as one would be a false statement about where their data went.
    Conversely, an event recorded as REQUESTED or DENIED did not result in a
    disclosure, so those are reported separately under `non_disclosures`
    rather than folded into the recipient list.

    The consent, history, evidence and receipt limbs reuse
    `app/services/principal_records.py::build_principal_record`, which is the
    module that already owns "everything this platform holds about one
    principal for one source_app" for `GET /portal/history` and
    `GET /portal/export`. Two builders would drift, and the one that drifted
    would be answering a statutory question wrongly.
    """
    from app.models.entities import DataCategory, DataSharingEvent, Processor, Purpose
    from app.services.principal_records import build_principal_record

    source_app = request.source_app or customer.source_app
    record = build_principal_record(db, customer, source_app)

    consents_out: list[dict] = []
    activities: dict[str, dict] = {}
    for consent in record.consents:
        purpose = consent.purpose
        category = consent.data_category
        activity = consent.processing_activity
        consents_out.append({
            "purpose_code": getattr(purpose, "code", "") or "",
            "purpose_name": getattr(purpose, "name", "") or "",
            "data_category": getattr(category, "name", "") or "",
            "processing_activity": getattr(activity, "name", "") or "",
            "status": consent.status,
            "consent_version": consent.consent_version,
            "granted_at": consent.granted_at,
            "withdrawn_at": consent.withdrawn_at,
            "expires_at": consent.expires_at,
        })
        if activity is None:
            continue
        key = f"{activity.code}:{getattr(purpose, 'code', '')}"
        entry = activities.setdefault(key, {
            "code": activity.code,
            "name": activity.name,
            "description": activity.description or "",
            "purpose_code": getattr(purpose, "code", "") or "",
            "purpose_name": getattr(purpose, "name", "") or "",
            "data_categories": [],
            "consent_status": consent.status,
        })
        category_name = getattr(category, "name", "") or ""
        if category_name and category_name not in entry["data_categories"]:
            entry["data_categories"].append(category_name)

    # ---- s.11(1)(b): recipients, from the disclosure register --------------
    events = (
        db.query(DataSharingEvent)
        .filter(DataSharingEvent.customer_id == customer.id)
        .order_by(DataSharingEvent.occurred_at.asc())
        .all()
    )
    # Scoped to the tenant this request belongs to, like every other list in
    # the platform. A DataSharingEvent carries its own source_app; where it is
    # blank (older rows) the tenant_id is the fallback discriminator.
    events = [
        e for e in events
        if (e.source_app or "") in ("", source_app)
        and (e.tenant_id is None or request.tenant_id is None or e.tenant_id == request.tenant_id)
    ]

    category_names = {
        row.id: row.name for row in db.query(DataCategory).all()
    }
    purpose_names = {row.id: (row.code, row.name) for row in db.query(Purpose).all()}
    processors = {row.id: row for row in db.query(Processor).all()} if events else {}

    recipients: dict[int, dict] = {}
    non_disclosures: list[dict] = []
    for event in events:
        processor = processors.get(event.processor_id)
        purpose_code, purpose_name = purpose_names.get(event.purpose_id, ("", ""))
        shared_categories = [
            category_names.get(cid, str(cid)) for cid in (event.data_category_ids or [])
        ]
        if event.event_type != "SENT":
            non_disclosures.append({
                "processor_name": getattr(processor, "name", "") or "",
                "event_type": event.event_type,
                "purpose": purpose_name or purpose_code,
                "data_categories": shared_categories,
                "reason": event.reason or "",
                "occurred_at": as_utc(event.occurred_at).isoformat(),
            })
            continue
        entry = recipients.setdefault(event.processor_id, {
            "processor_name": getattr(processor, "name", "") or f"processor:{event.processor_id}",
            "processor_type": getattr(processor, "type", "") or "",
            "country": getattr(processor, "country", "") or "",
            "purposes": [],
            "data_categories": [],
            "disclosures": 0,
            "first_shared_at": None,
            "last_shared_at": None,
            "legal_bases": [],
        })
        entry["disclosures"] += 1
        label = purpose_name or purpose_code
        if label and label not in entry["purposes"]:
            entry["purposes"].append(label)
        for name in shared_categories:
            if name not in entry["data_categories"]:
                entry["data_categories"].append(name)
        if event.legal_basis and event.legal_basis not in entry["legal_bases"]:
            entry["legal_bases"].append(event.legal_basis)
        occurred = as_utc(event.occurred_at)
        if entry["first_shared_at"] is None:
            entry["first_shared_at"] = occurred
        entry["last_shared_at"] = occurred

    contact = tenant_contact(db, request.tenant_id)
    package = {
        "reference_no": request.reference_no,
        "generated_at": utcnow(),
        "source_app": source_app,
        "personal_data": {
            "external_id": customer.external_id,
            "name": customer.name,
            "email": customer.email or "",
            "phone": customer.phone or "",
            "status": customer.status,
            "created_at": customer.created_at,
            "last_verified_at": customer.last_verified_at,
        },
        "consents": consents_out,
        "processing_activities": sorted(activities.values(), key=lambda a: (a["code"], a["purpose_code"])),
        "recipients": sorted(recipients.values(), key=lambda r: r["processor_name"]),
        "non_disclosures": non_disclosures,
        "consent_history_entries": len(record.history),
        "receipts_issued": len(record.receipts),
        "data_protection_officer": contact["dpo_name"],
        "dpo_contact": contact["dpo_email"],
        "rights_url": contact["rights_url"],
        "grievance_url": contact["grievance_url"],
        "board_complaint_url": contact["board_complaint_url"],
    }
    # The hash is CONTENT-addressed: computed over the package with both the
    # hash field and `generated_at` absent.
    #
    # Excluding the timestamp is the point. This hash exists to answer "what
    # exactly did you send her?" months later, and it is recorded twice - on
    # the request as closure evidence, and in the immutable audit row for
    # RIGHTS_REQUEST_PACKAGE_ISSUED. If it covered the moment of generation,
    # the principal's own download and the handler's fulfilment would produce
    # two different hashes for byte-identical content, and neither would
    # identify the package: the evidence would prove only that something was
    # generated at some time. Content-addressing also gives the property an
    # auditor actually wants - two hashes differ if and only if what was
    # disclosed differed.
    package["package_hash"] = _canonical_hash(
        {k: v for k, v in package.items() if k != "generated_at"}
    )
    return package


# --------------------------------------------------------------------------- #
#  s.12(1) - correction
# --------------------------------------------------------------------------- #



def apply_correction(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    changes: dict[str, str],
    note: str = "",
    actor_username: str,
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> RightsRequest:
    """s.12(1)-(2): correct, complete or update the principal's record.

    This is the staff customer-update endpoint E-02 says the platform lacks -
    deliberately bound to a request rather than offered as a bare
    `PATCH /customers/{id}`. A correction that is not attached to a request
    has no s.12 record behind it: nobody can later show who asked for it, when
    the clock started, or that the principal was told it was done. Binding it
    here means every correction carries all three by construction.

    Two things it maintains that a naive update would silently break:

    * `email_search`, the HMAC companion for the AES-GCM encrypted `email`
      column. docs/ARCHITECTURE.md is explicit that nothing fills it automatically, and a
      correction that updated `email` alone would leave the principal
      unfindable by email - which, since every upsert path reads a miss as
      "new customer", would fabricate a duplicate identity for them.
    * The before-image, recorded in `applied_changes` (encrypted) so the
      correction is reversible-in-evidence: an auditor can see what the value
      was, not just what it became.

    `external_id`, `source_app`, `status` and `anonymised_ref` are not
    correctable - see CORRECTABLE_CUSTOMER_FIELDS in app/models/rights.py.
    """
    require_verified(request)
    if request.request_type != "CORRECTION":
        raise RightsRequestError(
            f"Rights request {request.reference_no} is a {request.request_type} request, "
            "not a correction"
        )
    cleaned: dict[str, str] = {}
    for field, value in (changes or {}).items():
        if field not in CORRECTABLE_CUSTOMER_FIELDS:
            raise RightsRequestError(
                f"'{field}' is not a correctable field. A s.12(1) correction may change "
                f"{', '.join(CORRECTABLE_CUSTOMER_FIELDS)}."
            )
        if value is None or not str(value).strip():
            raise RightsRequestError(f"'{field}' cannot be corrected to an empty value")
        cleaned[field] = str(value).strip()
    if not cleaned:
        raise RightsRequestError("A correction must name at least one field to change")

    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for field, value in cleaned.items():
        current = getattr(customer, field, "") or ""
        if current == value:
            continue
        before[field] = current
        after[field] = value
        setattr(customer, field, value)
        if field == "email":
            # See the docstring: the HMAC companion is not maintained for us.
            customer.email_search = hmac_digest(value)

    if not after:
        raise RightsRequestError("Every requested value already matches the record on file")

    customer.updated_at = utcnow()
    request.applied_changes = {
        "before": before,
        "after": after,
        "applied_at": utcnow().isoformat(),
        "applied_by": actor_username,
    }
    db.flush()

    _append_event(
        db, request, event="CORRECTION_APPLIED",
        note=note or f"Corrected: {', '.join(sorted(after))}",
        actor_username=actor_username,
    )
    # Two audit rows, deliberately: CUSTOMER_UPDATED is the event the customer
    # timeline and every existing audit filter already know about, and
    # RIGHTS_REQUEST_CORRECTION_APPLIED is the one that ties it to the s.12
    # request. Neither carries the values themselves - only which fields moved.
    log_audit(
        db, "CUSTOMER_UPDATED",
        actor_username=actor_username, actor_type="USER", actor_id=actor_id,
        actor_role=actor_role, source_app=request.source_app, tenant_id=request.tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"DPDP Act s.12(1) correction applied under rights request {request.reference_no}",
        metadata={"reference_no": request.reference_no, "fields": sorted(after)},
        commit=False,
    )
    log_audit(
        db, "RIGHTS_REQUEST_CORRECTION_APPLIED",
        actor_username=actor_username, actor_type="USER", actor_id=actor_id,
        actor_role=actor_role, source_app=request.source_app, tenant_id=request.tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"Correction applied for {request.reference_no}",
        metadata={"reference_no": request.reference_no, "fields": sorted(after)},
        commit=False,
    )
    db.commit()
    db.refresh(request)
    return request


# --------------------------------------------------------------------------- #
#  s.12(3) - erasure, handed to the R1-06 engine
# --------------------------------------------------------------------------- #

def approve_erasure(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    basis: str = "",
    actor_username: str,
    actor_id: Optional[str] = None,
    actor_role: str = "",
):
    """Approve an ERASURE request by creating an `erasure_jobs` row.

    This function contains no deletion logic and must never grow any. It calls
    `app/services/erasure.py::approve_rights_request_erasure`, which raises the
    job already carrying its authoriser (the approval IS the authorisation for
    a s.12(3) request), and every downstream guarantee follows from that
    engine:

    * the R.8(2) forty-eight-hour pre-erasure notice must actually have been
      queued before anything is destroyed - `execute_erasure_job` refuses
      otherwise, and the notice window is what lets the principal say "this
      was not me" if the request was not really hers;
    * the R1-10 retention floors are re-checked at execution time and beat the
      principal's own request where a law requires the data to be kept, with
      what was kept and why recorded on the job;
    * a legal hold blocks it rather than delaying it;
    * `ck_erasure_jobs_executed_has_evidence` and
      `ck_erasure_jobs_executed_is_authorised` make an unevidenced or
      unauthorised execution unwritable.

    The rights request therefore stays IN_PROGRESS after this: the right has
    not yet been given effect, and it is fulfilled only once the job it points
    at reaches EXECUTED (see `fulfil_request`).
    """
    from app.services.erasure import approve_rights_request_erasure

    require_verified(request)
    if request.request_type != "ERASURE":
        raise RightsRequestError(
            f"Rights request {request.reference_no} is a {request.request_type} request, "
            "not an erasure request"
        )
    if request.erasure_job_id is not None:
        from app.models.erasure import ErasureJob

        return db.get(ErasureJob, request.erasure_job_id)

    job = approve_rights_request_erasure(
        db, customer,
        request_ref=request.reference_no,
        approved_by=actor_username,
        basis=basis,
        source_app=request.source_app,
        request_id=request.request_id,
    )
    request.erasure_job_id = job.id
    request.updated_at = utcnow()
    db.flush()
    _append_event(
        db, request, event="ERASURE_APPROVED",
        note=(
            f"Erasure job {job.job_ref} raised. A pre-erasure notice will be sent and the "
            f"statutory {job.notice_hours}-hour period must elapse before anything is erased."
        ),
        actor_username=actor_username,
    )
    log_audit(
        db, "RIGHTS_REQUEST_ERASURE_APPROVED",
        actor_username=actor_username, actor_type="USER", actor_id=actor_id,
        actor_role=actor_role, source_app=request.source_app, tenant_id=request.tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"s.12(3) erasure approved for {request.reference_no}",
        metadata={
            "reference_no": request.reference_no,
            "job_ref": job.job_ref,
            "job_status": job.status,
            "notice_hours": job.notice_hours,
        },
        commit=False,
    )
    db.commit()
    db.refresh(request)
    return job


def erasure_job_for(db: Session, request: RightsRequest):
    if request.erasure_job_id is None:
        return None
    from app.models.erasure import ErasureJob

    return db.get(ErasureJob, request.erasure_job_id)


# --------------------------------------------------------------------------- #
#  s.14 - nominations
# --------------------------------------------------------------------------- #

_NOMINEE_CODE_TTL_MINUTES = 60 * 24 * 14  # a nominee is not sitting at a screen


def _nominee_code() -> str:
    """A six-digit confirmation code from the OS CSPRNG.

    `secrets.randbelow`, never `random`, for exactly the reason
    `app/services/otp.py::_generate_code` documents: this code is the only
    thing standing between an arbitrary caller and the ability to confirm a
    nomination they were never named in, and `random`'s Mersenne Twister state
    is recoverable from a few hundred observed outputs.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def create_nomination(
    db: Session,
    *,
    customer,
    nominee_name: str,
    nominee_email: str,
    nominee_phone: str = "",
    nominee_relationship: str = "",
    source_app: str,
    context=None,
    actor_username: str = "principal",
    actor_id: Optional[str] = None,
) -> tuple[Nomination, str]:
    """Record a s.14 nomination and send the nominee their confirmation code.

    Returns the nomination and the plaintext code, which the caller passes
    straight to the notification service and **never** returns to the
    principal or writes to a log. Only the HMAC is stored.

    The nomination is created PENDING and confers nothing. Naming an email
    address is an assertion about a person who has not been asked; it becomes
    a nomination only when that person confirms from that address.
    """
    tenant_id = resolve_tenant_id(db, source_app)
    now = utcnow()
    code = _nominee_code()
    nomination = Nomination(
        tenant_id=tenant_id,
        nomination_ref=generate_nomination_ref(db, now=now),
        customer_id=customer.id,
        nominee_name=nominee_name,
        nominee_email=nominee_email,
        nominee_email_search=hmac_digest(nominee_email.strip().lower()),
        nominee_phone=nominee_phone,
        nominee_relationship=nominee_relationship,
        status="PENDING",
        principal_context_id=getattr(context, "id", None),
        nominated_at=now,
        verification_code_hash=hmac_digest(code),
        verification_expires_at=now + timedelta(minutes=_NOMINEE_CODE_TTL_MINUTES),
        verification_sent_at=now,
        source_app=source_app,
        created_by=actor_username,
    )
    db.add(nomination)
    db.flush()
    log_audit(
        db, "NOMINATION_RECORDED",
        actor_username=actor_username, actor_type="PRINCIPAL", actor_id=actor_id,
        source_app=source_app, tenant_id=tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"DPDP Act s.14 nomination {nomination.nomination_ref} recorded",
        # Never the nominee's contact details: the audit ledger is exported
        # wholesale, and the nominee is a third party who has not even been
        # asked yet. The reference is enough to find the row.
        metadata={"nomination_ref": nomination.nomination_ref, "status": "PENDING"},
        commit=False,
    )
    return nomination, code


def send_nominee_confirmation(
    db: Session, nomination: Nomination, code: str, *, actor_username: str = "system"
) -> Optional[int]:
    """Deliver the nominee's confirmation code through the existing
    notification service.

    `queue_operational_notification` is used rather than `queue_notification`
    because the recipient is NOT a data principal of this platform - the
    nominee has no `customers` row and must not acquire one merely by being
    named. That is the same reason R3-07's DPO escalation uses it. Same table,
    same dispatcher, same retry/backoff and delivery metrics; only the
    recipient is explicit.
    """
    if not nomination.nominee_email:
        return None
    try:
        queued = notification_service.queue_operational_notification(
            db,
            recipient=nomination.nominee_email,
            event_type="REQUEST_STATUS",
            tenant_id=nomination.tenant_id,
            source_app=nomination.source_app,
            channel="EMAIL",
            context={
                "reference_no": nomination.nomination_ref,
                "request_type": "NOMINATION",
                "status": "Awaiting your confirmation",
                "details": (
                    f"You have been nominated under DPDP Act s.14 to exercise another person's "
                    f"data-protection rights in the event of their death or incapacity. "
                    f"Your confirmation code is {code}. Quote reference "
                    f"{nomination.nomination_ref}. If you were not expecting this, ignore this "
                    f"message - a nomination confers nothing until it is confirmed."
                ),
                "due_at": "",
                "response_days": "",
            },
            actor_username=actor_username,
        )
        return queued.id if queued is not None else None
    except Exception:  # noqa: BLE001 - a transport failure must not undo the record
        logger.exception(
            "Could not queue the nominee confirmation for %s", nomination.nomination_ref
        )
        return None


def confirm_nomination(
    db: Session, nomination: Nomination, code: str, *, actor_username: str = "nominee"
) -> bool:
    """The nominee confirms, with the code sent to their own address.

    Mirrors `otp.py::confirm_otp`'s discipline exactly, and for the same
    reasons: a constant-time comparison against the stored HMAC, one attempt
    spent per guess whether or not the challenge is still live, and a single
    undifferentiated `False` for every failure mode - a wrong code, an expired
    window, an exhausted budget and a nomination that is not PENDING all look
    identical to the caller, so the endpoint cannot be used to probe which
    references exist or which are still open.
    """
    supplied = hmac_digest(code.strip()) or ""
    stored = nomination.verification_code_hash or hmac_digest("000000")
    now = utcnow()

    within_window = (
        nomination.status == "PENDING"
        and nomination.verification_expires_at is not None
        and as_utc(nomination.verification_expires_at) >= now
        and nomination.verification_attempts < nomination.max_verification_attempts
    )
    # Always pay the same write, so "expired" cannot be timed apart from a
    # plain wrong code.
    nomination.verification_attempts += 1
    matches = hmac.compare_digest(supplied, stored)
    success = bool(within_window and matches)

    if success:
        nomination.status = "VERIFIED"
        nomination.verified_at = now
        # The code is spent. Keeping it would leave a live credential on a row
        # that no longer needs one.
        nomination.verification_code_hash = None
        nomination.updated_at = now
        log_audit(
            db, "NOMINATION_VERIFIED",
            actor_username=actor_username, actor_type="PRINCIPAL",
            source_app=nomination.source_app, tenant_id=nomination.tenant_id,
            customer_id=nomination.customer_id,
            reason=f"Nominee confirmed nomination {nomination.nomination_ref}",
            metadata={"nomination_ref": nomination.nomination_ref, "status": "VERIFIED"},
            commit=False,
        )
    db.commit()
    return success


def _nomination_transition(nomination: Nomination, to_status: str) -> None:
    if to_status not in NOMINATION_TRANSITIONS.get(nomination.status, []):
        raise RightsTransitionError(
            f"Cannot move nomination {nomination.nomination_ref} from {nomination.status} "
            f"to {to_status}"
        )
    nomination.status = to_status
    nomination.updated_at = utcnow()


def activate_nomination(
    db: Session,
    nomination: Nomination,
    *,
    ground: str,
    evidence_ref: str,
    note: str = "",
    actor_username: str,
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> Nomination:
    """s.14: switch a confirmed nomination on, following the principal's death
    or incapacity.

    A staff act under `rights.manage`, never self-service, and reachable only
    from VERIFIED. The reasoning is in `app/models/rights.py`'s class
    docstring: the triggering event is precisely the moment the principal
    cannot contradict a false claim, so the platform requires (a) that the
    nominee was independently confirmed long before, (b) a named human taking
    responsibility, (c) a statutory ground, and (d) an evidence reference an
    auditor can check. `ck_nominations_active_is_authorised` enforces (b),
    (c) and (d) at the database level.
    """
    if not evidence_ref or not evidence_ref.strip():
        raise RightsRequestError(
            "Activating a nomination requires a reference to the evidence of death or "
            "incapacity relied on"
        )
    _nomination_transition(nomination, "ACTIVE")
    nomination.activated_at = utcnow()
    nomination.activated_by = actor_username
    nomination.activation_ground = ground
    nomination.activation_evidence_ref = evidence_ref.strip()
    nomination.activation_note = note
    db.flush()
    log_audit(
        db, "NOMINATION_ACTIVATED",
        actor_username=actor_username, actor_type="USER", actor_id=actor_id,
        actor_role=actor_role, source_app=nomination.source_app,
        tenant_id=nomination.tenant_id, customer_id=nomination.customer_id,
        reason=(
            f"Nomination {nomination.nomination_ref} activated on {ground.lower()} "
            f"(evidence {evidence_ref.strip()})"
        ),
        metadata={
            "nomination_ref": nomination.nomination_ref,
            "ground": ground,
            "evidence_ref": evidence_ref.strip(),
        },
        commit=False,
    )
    db.commit()
    db.refresh(nomination)
    return nomination


def revoke_nomination(
    db: Session,
    nomination: Nomination,
    *,
    reason: str = "",
    actor_username: str,
    actor_type: str = "PRINCIPAL",
    actor_id: Optional[str] = None,
) -> Nomination:
    _nomination_transition(nomination, "REVOKED")
    nomination.revoked_at = utcnow()
    nomination.revoked_by = actor_username
    nomination.revocation_reason = reason
    nomination.verification_code_hash = None
    db.flush()
    log_audit(
        db, "NOMINATION_REVOKED",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        source_app=nomination.source_app, tenant_id=nomination.tenant_id,
        customer_id=nomination.customer_id,
        reason=f"Nomination {nomination.nomination_ref} revoked",
        metadata={"nomination_ref": nomination.nomination_ref},
        commit=False,
    )
    db.commit()
    db.refresh(nomination)
    return nomination


# --------------------------------------------------------------------------- #
#  Closure
# --------------------------------------------------------------------------- #

def compute_closure_hash(request: RightsRequest) -> str:
    """SHA-256 over the canonical JSON of what the request actually produced.

    The same value is written into the RIGHTS_REQUEST_CLOSED `audit_logs` row,
    and that ledger is hash-chained and immutable - so an after-the-fact edit
    to `rights_requests` is detectable by recomputing this and comparing.
    Deliberately covers the outcome and the SLA facts, and deliberately does
    NOT cover the narrative: `resolution_summary` is encrypted with a key that
    may be rotated, and a hash whose inputs can legitimately change ciphertext
    is not a checkable hash.

    Every input is a value that is **stable on the stored row**, which is a
    sharper requirement than it first appears and one this function got wrong
    once. `outcome` is derived from `fulfilled_at`/`rejection_reason` rather
    than read from `status`, because the hash has to be computed before the
    transition to CLOSED (the audit row that carries it is written by that
    transition, and `audit_logs` is append-only, so there is no second chance
    to fill it in). Hashing `status` meant hashing FULFILLED and storing the
    result on a row that then said CLOSED - and an auditor recomputing it from
    the row would have found a mismatch and concluded the record had been
    tampered with. `verification_context_id` is left out for a related
    reason: the erasure engine legitimately nulls it, so it is not stable.
    """
    payload = {
        "reference_no": request.reference_no,
        "request_type": request.request_type,
        "customer_id": request.customer_id,
        "source_app": request.source_app,
        "received_at": as_utc(request.received_at).isoformat(),
        "acknowledged_at": as_utc(request.acknowledged_at).isoformat() if request.acknowledged_at else None,
        "acknowledgement_hours": request.acknowledgement_hours,
        "response_days": request.response_days,
        "due_at": as_utc(request.due_at).isoformat(),
        "identity_verified": bool(request.identity_verified),
        "verification_method": request.verification_method,
        "verified_at": as_utc(request.verified_at).isoformat() if request.verified_at else None,
        # The outcome, not the lifecycle state - see the docstring.
        "outcome": (
            "FULFILLED" if request.fulfilled_at is not None
            else "REJECTED" if request.rejection_reason
            else request.status
        ),
        "fulfilled_at": as_utc(request.fulfilled_at).isoformat() if request.fulfilled_at else None,
        "fulfilled_by": request.fulfilled_by or "",
        "rejection_reason": request.rejection_reason,
        "package_hash": request.package_hash,
        "erasure_job_id": request.erasure_job_id,
        "nomination_id": request.nomination_id,
        "applied_changes_fields": sorted((request.applied_changes or {}).get("after", {}).keys()),
    }
    return _canonical_hash(payload)


def _close(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    actor_username: str,
    actor_type: str,
    actor_id: Optional[str],
    actor_role: str,
    status_text: str,
    details: str,
) -> list[int]:
    """FULFILLED/REJECTED -> CLOSED, writing the closure evidence.

    Closure is not a separate manual step: a request that reached an outcome
    is closed in the same unit of work, so `ck_rights_requests_closed_has_
    evidence` is satisfied by construction and no request can sit resolved but
    unevidenced. The hash is computed BEFORE the transition writes the audit
    row, so the row carries it.
    """
    request.closed_at = utcnow()
    request.closed_by = actor_username
    request.closure_hash = compute_closure_hash(request)
    db.flush()
    notification_ids = _notify_principal(
        db, request, customer, status_text=status_text, details=details,
        actor_username=actor_username,
    )
    _transition(
        db, request, to_status="CLOSED",
        event="CLOSED", audit_event="RIGHTS_REQUEST_CLOSED",
        reason=f"Rights request {request.reference_no} closed ({status_text})",
        note=details, actor_username=actor_username, actor_type=actor_type,
        actor_id=actor_id, actor_role=actor_role,
        metadata={
            "closure_hash": request.closure_hash,
            "closed_within_period": as_utc(request.closed_at) <= as_utc(request.due_at),
            "notification_ids": notification_ids,
        },
    )
    return notification_ids


def fulfil_request(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    resolution_summary: str,
    package_hash: Optional[str] = None,
    actor_username: str,
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> tuple[RightsRequest, list[int]]:
    """Record that the right was given effect, then close with evidence.

    Refuses unless identity was established (R.14(2)) and, for an ERASURE
    request, unless the `erasure_jobs` row it delegated to has actually
    reached EXECUTED. That second check is the one that stops this register
    claiming a person was erased on the strength of a job that is still
    waiting out its statutory notice period, is blocked by a legal hold, or
    failed.
    """
    require_verified(request)
    if request.request_type == "ERASURE":
        job = erasure_job_for(db, request)
        if job is None:
            raise RightsRequestError(
                f"Rights request {request.reference_no} has no erasure job; approve it first"
            )
        if job.status != "EXECUTED":
            raise RightsRequestError(
                f"Erasure job {job.job_ref} is {job.status}, not EXECUTED. A s.12(3) request "
                "is fulfilled only once the erasure engine has actually carried the erasure "
                "out and recorded its evidence."
            )
    if request.request_type == "CORRECTION" and not (request.applied_changes or {}).get("after"):
        raise RightsRequestError(
            f"Rights request {request.reference_no} is a correction with nothing applied yet"
        )
    if request.request_type == "NOMINATION" and request.nomination_id is not None:
        nomination = db.get(Nomination, request.nomination_id)
        if nomination is not None and not nomination.is_verified():
            raise RightsRequestError(
                f"Nomination {nomination.nomination_ref} has not been confirmed by the nominee, "
                "so the s.14 request is not fulfilled"
            )

    request.fulfilled_at = utcnow()
    request.fulfilled_by = actor_username
    request.resolution_summary = resolution_summary
    if package_hash:
        request.package_hash = package_hash
    _transition(
        db, request, to_status="FULFILLED",
        event="FULFILLED", audit_event="RIGHTS_REQUEST_FULFILLED",
        reason=f"Rights request {request.reference_no} fulfilled",
        note="The request was fulfilled.",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        actor_role=actor_role,
        metadata={"package_hash": request.package_hash, "erasure_job_id": request.erasure_job_id},
    )
    notification_ids = _close(
        db, request, customer,
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        actor_role=actor_role, status_text="Fulfilled",
        details=resolution_summary,
    )
    db.commit()
    db.refresh(request)
    return request, notification_ids


def reject_request(
    db: Session,
    request: RightsRequest,
    customer,
    *,
    reason: str,
    basis: str,
    actor_username: str,
    actor_type: str = "USER",
    actor_id: Optional[str] = None,
    actor_role: str = "",
) -> tuple[RightsRequest, list[int]]:
    """Refuse a request lawfully, with the ground recorded and the principal
    told - including that they may take it further.

    s.12(3) permits refusal of erasure only where retention is necessary for
    the specified purpose or for compliance with a law; R.14(2) permits
    refusal where identity cannot be established. Either way the principal has
    to be told why, and told that a grievance and ultimately the Board are
    open to them - which is what the notification says.
    """
    request.rejection_reason = reason
    request.rejection_basis = basis
    _transition(
        db, request, to_status="REJECTED",
        event="REJECTED", audit_event="RIGHTS_REQUEST_REJECTED",
        reason=f"Rights request {request.reference_no} refused ({reason})",
        note="The request was refused; the ground is recorded on the request.",
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        actor_role=actor_role,
        metadata={"rejection_reason": reason},
    )
    contact = tenant_contact(db, request.tenant_id)
    notification_ids = _close(
        db, request, customer,
        actor_username=actor_username, actor_type=actor_type, actor_id=actor_id,
        actor_role=actor_role, status_text="Not accepted",
        details=(
            f"{basis}\n\nIf you disagree you may raise a grievance"
            + (f" at {contact['grievance_url']}" if contact["grievance_url"] else "")
            + " and, after exhausting it, complain to the Data Protection Board of India"
            + (f" at {contact['board_complaint_url']}" if contact["board_complaint_url"] else "")
            + "."
        ),
    )
    db.commit()
    db.refresh(request)
    return request, notification_ids


# --------------------------------------------------------------------------- #
#  Metrics - K-20, K-21, K-22
# --------------------------------------------------------------------------- #

def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def queue_metrics(
    db: Session, *, source_app: Optional[str] = None, now: Optional[datetime] = None
) -> dict:
    """The admin queue's header numbers and the three KPIs this module owns.

    * **K-20** - `by_type`: access / correction / erasure / nomination counts,
      straight off the register rather than inferred from audit events.
    * **K-21** - `median_acknowledgement_hours`, measured per request between
      its own `received_at` and its own `acknowledged_at`. The median rather
      than the mean because one back-entered postal request logged a fortnight
      late would otherwise swamp a thousand instant portal acknowledgements.
    * **K-22** - `on_time_closure_rate`: each request's own `closed_at`
      compared against its own snapshotted `due_at`, so the rate is measured
      against what each principal was actually promised, not against today's
      configuration.

    `app/services/kpi_catalogue.py` republishes these numbers and does not
    recompute them - one implementation per KPI is the only structural defence
    against two screens disagreeing about one compliance figure.
    """
    reference = now or utcnow()
    query = db.query(RightsRequest)
    if source_app:
        query = query.filter(RightsRequest.source_app == source_app)
    rows: list[RightsRequest] = query.all()

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_rejection: dict[str, int] = {}
    open_count = overdue = awaiting_verification = 0
    fulfilled_total = rejected_total = 0
    ack_hours: list[float] = []
    ack_within = 0
    closed_total = closed_within = 0
    total_days = 0.0

    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_type[row.request_type] = by_type.get(row.request_type, 0) + 1
        if row.rejection_reason:
            by_rejection[row.rejection_reason] = by_rejection.get(row.rejection_reason, 0) + 1
        if row.status == "FULFILLED" or (row.status == "CLOSED" and row.fulfilled_at is not None):
            fulfilled_total += 1
        if row.status == "REJECTED" or (row.status == "CLOSED" and row.rejection_reason):
            rejected_total += 1

        taken = row.acknowledgement_hours_taken()
        if taken is not None:
            ack_hours.append(taken)
            if taken <= row.acknowledgement_hours:
                ack_within += 1

        if row.status == "CLOSED" and row.closed_at is not None:
            closed_total += 1
            if as_utc(row.closed_at) <= as_utc(row.due_at):
                closed_within += 1
            total_days += (as_utc(row.closed_at) - as_utc(row.received_at)).total_seconds() / 86400
        elif row.status not in RIGHTS_REQUEST_TERMINAL_STATUSES:
            open_count += 1
            if as_utc(row.due_at) <= reference:
                overdue += 1
            if row.status == "VERIFYING" or not row.identity_verified:
                awaiting_verification += 1

    nominations_query = db.query(Nomination)
    if source_app:
        nominations_query = nominations_query.filter(Nomination.source_app == source_app)
    nominations = nominations_query.all()

    return {
        "total": len(rows),
        "open": open_count,
        "overdue": overdue,
        "awaiting_verification": awaiting_verification,
        "by_status": by_status,
        "by_type": by_type,
        "by_rejection_reason": by_rejection,
        "acknowledged_total": len(ack_hours),
        "acknowledged_within_commitment": ack_within,
        "median_acknowledgement_hours": _median(ack_hours),
        "closed_total": closed_total,
        "closed_within_period": closed_within,
        "on_time_closure_rate": (
            round(closed_within * 100.0 / closed_total, 2) if closed_total else None
        ),
        "average_days_to_closure": (
            round(total_days / closed_total, 2) if closed_total else None
        ),
        "fulfilled_total": fulfilled_total,
        "rejected_total": rejected_total,
        "nominations_total": len(nominations),
        "nominations_active": sum(1 for n in nominations if n.status == "ACTIVE"),
    }
