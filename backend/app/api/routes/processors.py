"""R3-07 (C-02, M-02, M-05, K-08): the processor register API.

Route order matters here: `/processors/alerts...` and `/processors/reports/...`
are declared BEFORE `/processors/{processor_id}`, or FastAPI would match
"alerts" as a processor id.

The acknowledgement endpoint (`POST /processors/alerts/{ref}/ack`) is the one
route in this module with no staff credential. It is authenticated instead by
an HMAC signature over the request body, computed with the per-processor
webhook secret this platform issued - the same secret and the same scheme used
to sign the outbound instruction. That is deliberate: the acknowledger is an
external processor, not a user of this console, and issuing it a staff account
to close the loop would be a far larger grant than "may confirm the
instructions we sent you". A staff member recording an out-of-band
confirmation uses `/ack-manual` instead, which does require a credential and
is recorded with `ack_method=MANUAL` so the two can be told apart in K-08.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE, PERM_POLICY_MANAGE, PERM_POLICY_VIEW
from app.core.utils import mask_identifier
from app.models.entities import Organization, Processor, ProcessorAlert, User
from app.schemas.schemas import (
    MessageOut,
    ProcessorAlertAckIn,
    ProcessorAlertManualAckIn,
    ProcessorAlertOut,
    ProcessorContractCoverageOut as _ProcessorContractCoverageOut,
    ProcessorCreateOut,
    ProcessorDispatchOut,
    ProcessorIn,
    ProcessorOut,
    ProcessorSecretOut,
    ProcessorUpdate,
    PropagationSlaOut as _PropagationSlaOut,
)
from app.services.audit import log_audit
from app.services.processors import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    acknowledge_alert,
    contract_coverage_report,
    dispatch_pending_alerts,
    escalate_overdue_alerts,
    generate_webhook_secret,
    propagation_sla_metrics,
    verify_signature,
)
from app.services.tenancy import resolve_tenant_id

router = APIRouter(prefix="/processors", tags=["processors"])


class ProcessorContractCoverageOut(_ProcessorContractCoverageOut):
    """K-32: widens `coverage_pct` to Optional. Zero registered processors
    means coverage is undefined, not "100% covered". Overridden locally
    rather than in app/schemas/schemas.py, which this lane does not own -
    see contract_coverage_report() in app/services/processors.py for the
    corresponding source-side fix."""

    coverage_pct: Optional[float] = None


class PropagationSlaOut(_PropagationSlaOut):
    """K-08: widens `k08_propagation_sla_pct` to Optional. No withdrawal
    whose SLA outcome is decided means the percentage is undefined, not
    "100% propagated". Overridden locally rather than in
    app/schemas/schemas.py, which this lane does not own - see
    propagation_sla_metrics() in app/services/processors.py."""

    k08_propagation_sla_pct: Optional[float] = None


def _scope_tenant_id(db: Session, user: User) -> Optional[int]:
    """The tenant an org-scoped staff role may see, or None for an unscoped
    platform role (which sees every tenant's processors)."""
    scope = get_org_scope(user)
    return resolve_tenant_id(db, scope) if scope else None


def _visible(db: Session, user: User):
    q = db.query(Processor)
    tenant_id = _scope_tenant_id(db, user)
    if tenant_id is not None:
        # Platform-wide processors (tenant_id NULL) are visible to every
        # tenant: they are shared infrastructure this platform itself uses.
        q = q.filter(Processor.tenant_id.in_([tenant_id, None]))
    return q


def _contract_in_force(p: Processor) -> bool:
    today = datetime.now(timezone.utc).date()
    if p.contract_valid_from and p.contract_valid_from > today:
        return False
    if p.contract_valid_until and p.contract_valid_until < today:
        return False
    return bool(p.contract_ref)


def _out(p: Processor) -> dict:
    """Response body for a processor. `webhook_secret` is absent by
    construction - contact details are masked for display, the same treatment
    routes/customers.py gives principal contact details."""
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "name": p.name,
        "type": p.type,
        "country": p.country,
        "contact_name": p.contact_name,
        "contact_email_masked": mask_identifier(p.contact_email),
        "contact_phone_masked": mask_identifier(p.contact_phone),
        "escalation_email_masked": mask_identifier(p.escalation_email),
        "contract_ref": p.contract_ref,
        "contract_signed_on": p.contract_signed_on,
        "contract_valid_from": p.contract_valid_from,
        "contract_valid_until": p.contract_valid_until,
        "contract_in_force": _contract_in_force(p),
        "security_clause_ref": p.security_clause_ref,
        "security_measures": p.security_measures,
        "erasure_clause_ref": p.erasure_clause_ref,
        "erasure_sla_days": p.erasure_sla_days,
        "webhook_url": p.webhook_url,
        "webhook_configured": bool(p.webhook_url and p.webhook_secret),
        "webhook_secret_fingerprint": p.webhook_secret_fingerprint,
        "webhook_secret_set_at": p.webhook_secret_set_at,
        "ack_sla_hours": p.ack_sla_hours,
        "notes": p.notes,
        "is_active": p.is_active,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _alert_out(alert: ProcessorAlert, processor_name: str = "") -> ProcessorAlertOut:
    within_sla = None
    if alert.acknowledged_at is not None:
        acked = alert.acknowledged_at
        due = alert.due_at
        if acked.tzinfo is None:
            acked = acked.replace(tzinfo=timezone.utc)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        within_sla = acked <= due
    return ProcessorAlertOut(
        id=alert.id, alert_ref=alert.alert_ref, trigger_ref=alert.trigger_ref,
        tenant_id=alert.tenant_id, processor_id=alert.processor_id,
        processor_name=processor_name or (alert.processor.name if alert.processor else ""),
        customer_id=alert.customer_id, consent_id=alert.consent_id, purpose_id=alert.purpose_id,
        alert_type=alert.alert_type, status=alert.status, payload_hash=alert.payload_hash,
        attempts=alert.attempts, max_attempts=alert.max_attempts, http_status=alert.http_status,
        last_error=alert.last_error, ack_sla_hours=alert.ack_sla_hours, due_at=alert.due_at,
        acknowledged_at=alert.acknowledged_at, acknowledged_by=alert.acknowledged_by,
        ack_reference=alert.ack_reference, ack_method=alert.ack_method,
        escalated_at=alert.escalated_at, within_sla=within_sla,
        created_at=alert.created_at, sent_at=alert.sent_at, reason=alert.reason,
    )


# ---------------------------------------------------------------------------
# Alerts (declared before /{processor_id} - see the module docstring)
# ---------------------------------------------------------------------------
@router.get("/alerts", response_model=list[ProcessorAlertOut])
def list_alerts(
    status: Optional[str] = None,
    alert_type: Optional[str] = None,
    processor_id: Optional[int] = None,
    trigger_ref: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    q = db.query(ProcessorAlert)
    tenant_id = _scope_tenant_id(db, user)
    if tenant_id is not None:
        q = q.filter(ProcessorAlert.tenant_id == tenant_id)
    if status:
        q = q.filter(ProcessorAlert.status == status)
    if alert_type:
        q = q.filter(ProcessorAlert.alert_type == alert_type)
    if processor_id:
        q = q.filter(ProcessorAlert.processor_id == processor_id)
    if trigger_ref:
        q = q.filter(ProcessorAlert.trigger_ref == trigger_ref)
    alerts = q.order_by(ProcessorAlert.id.desc()).limit(limit).all()
    return [_alert_out(a) for a in alerts]


@router.get("/alerts/{alert_ref}", response_model=ProcessorAlertOut)
def get_alert(
    alert_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    alert = db.query(ProcessorAlert).filter(ProcessorAlert.alert_ref == alert_ref).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    tenant_id = _scope_tenant_id(db, user)
    if tenant_id is not None and alert.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_out(alert)


@router.post("/alerts/{alert_ref}/ack", response_model=ProcessorAlertOut)
async def acknowledge(alert_ref: str, request: Request, db: Session = Depends(get_db)):
    """The processor's own callback confirming it acted on an instruction.

    Authenticated by an HMAC signature over the raw request body with the
    processor's webhook secret - see the module docstring. The raw bytes are
    read before parsing, because a signature is over exactly what was sent,
    not over a re-serialisation of it.
    """
    alert = db.query(ProcessorAlert).filter(ProcessorAlert.alert_ref == alert_ref).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    processor = db.get(Processor, alert.processor_id)
    if not processor or not processor.webhook_secret:
        # No secret means no way to authenticate this caller. 404 rather than
        # 401: an unauthenticated caller must not be able to tell a real
        # alert_ref from a fabricated one.
        raise HTTPException(status_code=404, detail="Alert not found")

    raw = await request.body()
    if not verify_signature(
        processor.webhook_secret,
        request.headers.get(TIMESTAMP_HEADER),
        raw,
        request.headers.get(SIGNATURE_HEADER),
    ):
        log_audit(
            db, "PROCESSOR_ALERT_ACKNOWLEDGED", actor_username="processor",
            actor_type="SYSTEM", source_app=alert.source_app, tenant_id=alert.tenant_id,
            reason=f"Rejected an unsigned/invalid acknowledgement of {alert_ref}",
            metadata={"alert_ref": alert_ref, "accepted": False,
                      "processor_id": alert.processor_id},
        )
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    try:
        payload = ProcessorAlertAckIn.model_validate_json(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Acknowledgement body is not valid")

    alert = acknowledge_alert(
        db, alert, acknowledged_by=payload.acknowledged_by, method="WEBHOOK",
        reference=payload.reference, note=payload.note, actor_username="processor",
    )
    return _alert_out(alert, processor_name=processor.name)


@router.post("/alerts/{alert_ref}/ack-manual", response_model=ProcessorAlertOut)
def acknowledge_manually(
    alert_ref: str,
    payload: ProcessorAlertManualAckIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    """Record a confirmation a processor gave out of band (email, phone).
    Kept distinct from a signed callback via `ack_method` so an auditor can
    re-read K-08 counting only cryptographically attested acknowledgements."""
    alert = db.query(ProcessorAlert).filter(ProcessorAlert.alert_ref == alert_ref).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    tenant_id = _scope_tenant_id(db, user)
    if tenant_id is not None and alert.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert = acknowledge_alert(
        db, alert, acknowledged_by=payload.acknowledged_by, method="MANUAL",
        reference=payload.reference, note=payload.note, actor_username=user.username,
    )
    return _alert_out(alert)


@router.post("/alerts/dispatch", response_model=ProcessorDispatchOut)
def dispatch_now(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Run one delivery + escalation pass on demand.

    The same work app/jobs/processor_alert_job.py does on a schedule; exposed
    so an operator can flush the queue after fixing a processor's endpoint
    without waiting for the next interval, and so the pipeline is operable in
    a deployment running with SCHEDULER_ENABLED=false.
    """
    dispatched = dispatch_pending_alerts(db)
    escalated = escalate_overdue_alerts(db)
    return ProcessorDispatchOut(**dispatched, **escalated)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@router.get("/reports/contract-coverage", response_model=ProcessorContractCoverageOut)
def contract_coverage(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    return contract_coverage_report(db, tenant_id=_scope_tenant_id(db, user))


@router.get("/reports/propagation-sla", response_model=PropagationSlaOut)
def propagation_sla(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    """K-08: withdrawals acknowledged by every processor within the SLA."""
    return propagation_sla_metrics(
        db, date_from=date_from, date_to=date_to, tenant_id=_scope_tenant_id(db, user)
    )


# ---------------------------------------------------------------------------
# Register CRUD
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ProcessorOut])
def list_processors(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    q = _visible(db, user)
    if not include_inactive:
        q = q.filter(Processor.is_active.is_(True))
    return [_out(p) for p in q.order_by(Processor.id.asc()).all()]


@router.post("", response_model=ProcessorCreateOut, status_code=201)
def create_processor(
    payload: ProcessorIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Register a processor and issue its webhook secret.

    The plaintext secret is in this response and nowhere else, ever again.
    """
    tenant_id = _scope_tenant_id(db, user)
    if payload.tenant_code:
        organization = (
            db.query(Organization).filter(Organization.code == payload.tenant_code).first()
        )
        if not organization:
            raise HTTPException(status_code=404, detail="Unknown tenant_code")
        if tenant_id is not None and organization.id != tenant_id:
            raise HTTPException(
                status_code=403, detail="Cannot register a processor for another tenant"
            )
        tenant_id = organization.id

    plaintext, fingerprint = generate_webhook_secret()
    now = datetime.now(timezone.utc)
    processor = Processor(
        tenant_id=tenant_id,
        name=payload.name, type=payload.type, country=payload.country,
        contact_name=payload.contact_name, contact_email=payload.contact_email,
        contact_phone=payload.contact_phone, escalation_email=payload.escalation_email,
        contract_ref=payload.contract_ref, contract_signed_on=payload.contract_signed_on,
        contract_valid_from=payload.contract_valid_from,
        contract_valid_until=payload.contract_valid_until,
        security_clause_ref=payload.security_clause_ref,
        security_measures=payload.security_measures,
        erasure_clause_ref=payload.erasure_clause_ref,
        erasure_sla_days=payload.erasure_sla_days,
        webhook_url=payload.webhook_url, webhook_secret=plaintext,
        webhook_secret_fingerprint=fingerprint, webhook_secret_set_at=now,
        ack_sla_hours=payload.ack_sla_hours, notes=payload.notes, is_active=payload.is_active,
    )
    db.add(processor)
    db.flush()
    log_audit(
        db, "PROCESSOR_REGISTERED", actor_username=user.username,
        actor_role=user.role.name if user.role else "", actor_type="USER", actor_id=str(user.id),
        tenant_id=tenant_id,
        reason=f"Processor '{processor.name}' registered",
        metadata={
            "processor_id": processor.id, "type": processor.type, "country": processor.country,
            "contract_ref": processor.contract_ref, "ack_sla_hours": processor.ack_sla_hours,
            # Fingerprint only - never the secret.
            "webhook_secret_fingerprint": fingerprint,
        },
        commit=False,
    )
    db.commit()
    db.refresh(processor)
    return {**_out(processor), "webhook_secret": plaintext}


@router.get("/{processor_id}", response_model=ProcessorOut)
def get_processor(
    processor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    processor = _visible(db, user).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    return _out(processor)


@router.put("/{processor_id}", response_model=ProcessorOut)
def update_processor(
    processor_id: int,
    payload: ProcessorUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    processor = _visible(db, user).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    changed = payload.model_dump(exclude_unset=True)
    for key, value in changed.items():
        setattr(processor, key, value)
    valid_from = processor.contract_valid_from
    valid_until = processor.contract_valid_until
    if valid_from and valid_until and valid_from > valid_until:
        raise HTTPException(
            status_code=422, detail="contract_valid_from must not be after contract_valid_until"
        )
    db.flush()
    log_audit(
        db, "PROCESSOR_UPDATED", actor_username=user.username,
        actor_role=user.role.name if user.role else "", actor_type="USER", actor_id=str(user.id),
        tenant_id=processor.tenant_id,
        reason=f"Processor '{processor.name}' updated",
        # Field NAMES only: contact_email/escalation_email are encrypted
        # columns and their values must not be echoed into the ledger.
        metadata={"processor_id": processor.id, "fields_changed": sorted(changed.keys())},
        commit=False,
    )
    db.commit()
    db.refresh(processor)
    return _out(processor)


@router.post("/{processor_id}/rotate-webhook-secret", response_model=ProcessorSecretOut)
def rotate_webhook_secret(
    processor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Issue a new webhook secret. Alerts already queued are re-signed with
    the new secret at their next delivery attempt (see
    services/processors.py::deliver_alert), so a rotation does not strand the
    queue."""
    processor = _visible(db, user).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    plaintext, fingerprint = generate_webhook_secret()
    now = datetime.now(timezone.utc)
    previous_fingerprint = processor.webhook_secret_fingerprint
    processor.webhook_secret = plaintext
    processor.webhook_secret_fingerprint = fingerprint
    processor.webhook_secret_set_at = now
    db.flush()
    log_audit(
        db, "PROCESSOR_WEBHOOK_SECRET_ROTATED", actor_username=user.username,
        actor_role=user.role.name if user.role else "", actor_type="USER", actor_id=str(user.id),
        tenant_id=processor.tenant_id,
        reason=f"Webhook secret rotated for processor '{processor.name}'",
        metadata={"processor_id": processor.id,
                  "previous_fingerprint": previous_fingerprint,
                  "webhook_secret_fingerprint": fingerprint},
        commit=False,
    )
    db.commit()
    return ProcessorSecretOut(
        processor_id=processor.id, webhook_secret=plaintext,
        webhook_secret_fingerprint=fingerprint, webhook_secret_set_at=now,
    )


@router.delete("/{processor_id}", response_model=MessageOut)
def deactivate_processor(
    processor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Deactivate rather than delete: the register is itself a retained record
    (data_sharing_events and processor_alerts both reference it, and both have
    a retention floor - see services/retention.py)."""
    processor = _visible(db, user).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")
    processor.is_active = False
    db.flush()
    log_audit(
        db, "PROCESSOR_UPDATED", actor_username=user.username,
        actor_role=user.role.name if user.role else "", actor_type="USER", actor_id=str(user.id),
        tenant_id=processor.tenant_id,
        reason=f"Processor '{processor.name}' deactivated",
        metadata={"processor_id": processor.id, "fields_changed": ["is_active"]},
        commit=False,
    )
    db.commit()
    return MessageOut(message=f"Processor {processor.name} deactivated")
