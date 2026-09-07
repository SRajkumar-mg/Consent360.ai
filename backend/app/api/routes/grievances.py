"""R2-06 (G-01..G-04): the grievance redressal API.

Two audiences, one register:

* the **data principal's own form** (`/grievances/me*`), authenticated by the
  same `X-Context-Token` every other self-service surface uses - the
  complainant's only credential, and the reason no PII appears in any of
  these paths; and
* the **handler's queue** (`/grievances*`), authenticated by a staff JWT and
  gated on `grievance.view` / `grievance.manage`, org-scoped through
  `get_org_scope` exactly like the customers/consents/audit lists.

A grievance is addressed by its `reference_no` in every path, never by its
primary key - see `app/schemas/grievance.py`'s module docstring for why.

The response period this module quotes back to a complainant is the tenant's
own `grievance_response_days`; the same value is already published
unauthenticated at `GET /public/{tenant_code}/rights`, which is the "response
period published on the public rights page" half of R2-06. This module reads
it through `services/grievance.py::response_days_for_tenant` and never
defines a period of its own.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_customer_from_context, get_org_scope, require_permission, verify_context_token
from app.core.database import get_db
from app.core.rbac import PERM_GRIEVANCE_MANAGE, PERM_GRIEVANCE_VIEW
from app.models.entities import ConsentContext, Purpose, User
from app.models.grievance import GRIEVANCE_TERMINAL_STATUSES, Grievance
from app.schemas.grievance import (
    GrievanceAcknowledgementOut,
    GrievanceCloseIn,
    GrievanceEscalateIn,
    GrievanceEventOut,
    GrievanceFeedbackIn,
    GrievanceOut,
    GrievanceQueueStatsOut,
    GrievanceResolveIn,
    GrievanceSelfIn,
    GrievanceStaffIn,
    GrievanceUpdateIn,
)
from app.services import grievance as grievance_service
from app.services.tenancy import ANY_TENANT, resolve_customer

router = APIRouter(prefix="/grievances", tags=["grievances"])


# --------------------------------------------------------------------------- #
#  Serialisation
# --------------------------------------------------------------------------- #

def _out(db: Session, g: Grievance, *, for_principal: bool = False) -> GrievanceOut:
    """Serialise a grievance.

    `for_principal=True` filters the timeline to entries a handler marked
    visible: internal triage notes ("chased the marketing team, no reply")
    belong in the queue and in the audit ledger, not in the complainant's
    view of their own case.
    """
    out = GrievanceOut.model_validate(g)
    # Reads the external id off the Grievance's own already-loaded `customer`
    # relationship purely to echo it back - the Grievance itself was resolved
    # and tenant-checked before this is reached. See the written justification
    # in tests/test_customer_resolution_guard.py's ALLOWED_FILES.
    principal = g.customer
    out.customer_external_id = principal.external_id if principal is not None else ""
    now = datetime.now(timezone.utc)
    out.overdue = g.is_overdue(now)
    out.days_remaining = g.remaining_days(now)
    events = g.events or []
    if for_principal:
        events = [e for e in events if e.visible_to_principal]
    out.events = [GrievanceEventOut.model_validate(e) for e in events]
    return out


def _acknowledgement(db: Session, g: Grievance, notification_ids: list[int]) -> GrievanceAcknowledgementOut:
    dpo_name, dpo_email, board_url = grievance_service.tenant_contact(db, g.tenant_id)
    due = grievance_service.as_utc(g.due_at)
    return GrievanceAcknowledgementOut(
        grievance=_out(db, g, for_principal=True),
        reference_no=g.reference_no,
        acknowledged=g.acknowledged_at is not None,
        acknowledgement_message=(
            f"Your grievance has been registered under reference {g.reference_no}. "
            f"We publish a response period of {g.response_days} days, so you will receive a "
            f"response by {due.date().isoformat()}. If we do not respond within that period the "
            "complaint is escalated automatically to our Data Protection Officer, and you may "
            "also complain to the Data Protection Board of India."
        ),
        response_days=g.response_days,
        due_at=due,
        # The DPO's NAME is the published grievance-officer identity (it is
        # already served by GET /public/{tenant}/privacy-contact); the address
        # is deliberately not echoed into a submission response.
        grievance_officer=dpo_name,
        board_complaint_url=board_url,
        notification_ids=notification_ids,
    )


# --------------------------------------------------------------------------- #
#  Lookup helpers
# --------------------------------------------------------------------------- #

def _by_reference(db: Session, reference_no: str) -> Grievance:
    g = db.query(Grievance).filter(Grievance.reference_no == reference_no).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return g


def _staff_grievance(db: Session, reference_no: str, user: User) -> Grievance:
    """Fetch a grievance for a staff caller, refusing one outside their org
    scope with the same 404 an unknown reference gets - an org-scoped handler
    must not be able to confirm that another tenant's reference exists."""
    g = _by_reference(db, reference_no)
    scope = get_org_scope(user)
    if scope and g.source_app != scope:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return g


def _principal_context(x_context_token: str, db: Session):
    """The `/grievances/me*` credential check, identical to
    portal.py::_resolve_customer_and_context: the JWT's own `source_app`
    claim scopes the customer lookup, and the PERSISTED context row is then
    re-checked against that customer's tenant independently."""
    payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, payload)
    context = db.query(ConsentContext).filter(ConsentContext.token == x_context_token).first()
    if not context or context.source_app != customer.source_app:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    return customer, context


def _my_grievance(db: Session, reference_no: str, customer) -> Grievance:
    g = _by_reference(db, reference_no)
    if g.customer_id != customer.id or g.source_app != customer.source_app:
        # Same 404 as an unknown reference: a complainant must not be able to
        # probe whether someone else's reference exists.
        raise HTTPException(status_code=404, detail="Grievance not found")
    return g


def _purpose_id(db: Session, purpose_code: str | None) -> int | None:
    if not purpose_code:
        return None
    purpose = db.query(Purpose).filter(Purpose.code == purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    return purpose.id


# --------------------------------------------------------------------------- #
#  Data principal - the portal form
# --------------------------------------------------------------------------- #

@router.post("/me", response_model=GrievanceAcknowledgementOut, status_code=201)
def submit_my_grievance(
    payload: GrievanceSelfIn,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """Submit a grievance (the portal form).

    Returns the reference number and the acknowledgement in one response -
    the complainant never has to poll for either, and the acknowledgement
    states the published response period and the resulting deadline, which is
    what makes the promise auditable from the complainant's side too.
    """
    customer, context = _principal_context(x_context_token, db)
    grievance, notification_ids = grievance_service.create_grievance(
        db,
        customer=customer,
        category=payload.category,
        subject=payload.subject,
        description=payload.description,
        channel="PORTAL",
        source_app=context.source_app,
        purpose_id=_purpose_id(db, payload.purpose_code),
        consent_id=payload.consent_id,
        actor_username=f"principal:{customer.external_id}",
        actor_type="PRINCIPAL",
        actor_id=customer.external_id,
    )
    return _acknowledgement(db, grievance, notification_ids)


@router.get("/me", response_model=list[GrievanceOut])
def my_grievances(
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _principal_context(x_context_token, db)
    rows = (
        db.query(Grievance)
        .filter(Grievance.customer_id == customer.id, Grievance.source_app == context.source_app)
        .order_by(Grievance.received_at.desc())
        .limit(200)
        .all()
    )
    return [_out(db, g, for_principal=True) for g in rows]


@router.get("/me/{reference_no}", response_model=GrievanceOut)
def my_grievance(
    reference_no: str,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, _ = _principal_context(x_context_token, db)
    return _out(db, _my_grievance(db, reference_no, customer), for_principal=True)


@router.post("/me/{reference_no}/feedback", response_model=GrievanceOut)
def submit_my_feedback(
    reference_no: str,
    payload: GrievanceFeedbackIn,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """Rate the resolution. Only meaningful once there is one to rate, so a
    grievance that has not been resolved is refused rather than silently
    accepting feedback about nothing."""
    customer, _ = _principal_context(x_context_token, db)
    g = _my_grievance(db, reference_no, customer)
    if g.status not in GRIEVANCE_TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Feedback can only be given once the grievance has been resolved",
        )
    g = grievance_service.record_feedback(
        db, g, rating=payload.rating, comment=payload.comment,
        actor_username=f"principal:{customer.external_id}",
    )
    return _out(db, g, for_principal=True)


# --------------------------------------------------------------------------- #
#  Staff - the admin queue
# --------------------------------------------------------------------------- #

@router.get("", response_model=list[GrievanceOut])
def list_grievances(
    status: str | None = Query(default=None, description="Exact status filter"),
    category: str | None = None,
    assigned_to: str | None = None,
    customer_external_id: str | None = None,
    open_only: bool = Query(default=False, description="Exclude RESOLVED and CLOSED"),
    overdue_only: bool = Query(default=False, description="Open and past due_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_VIEW)),
):
    """The queue.

    Every filter here is a structured column. There is deliberately no
    free-text search over `subject`/`description`: those are AES-GCM
    encrypted at rest (see app/models/grievance.py), so `LIKE` against them
    would silently match nothing rather than fail, which is worse than not
    offering it.
    """
    scope = get_org_scope(user)
    q = db.query(Grievance)
    if scope:
        q = q.filter(Grievance.source_app == scope)
    if customer_external_id:
        customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_external_id)
        if not customer:
            return []
        q = q.filter(Grievance.customer_id == customer.id)
    if status:
        q = q.filter(Grievance.status == status)
    if category:
        q = q.filter(Grievance.category == category)
    if assigned_to:
        q = q.filter(Grievance.assigned_to == assigned_to)
    if open_only or overdue_only:
        q = q.filter(Grievance.status.notin_(GRIEVANCE_TERMINAL_STATUSES))
    if overdue_only:
        q = q.filter(Grievance.due_at <= datetime.now(timezone.utc))
    # Soonest deadline first: the queue's job is to surface what is about to
    # breach, not what arrived most recently.
    rows = q.order_by(Grievance.due_at.asc()).offset(offset).limit(limit).all()
    return [_out(db, g) for g in rows]


@router.get("/stats", response_model=GrievanceQueueStatsOut)
def grievance_stats(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_VIEW)),
):
    """Queue counts plus the DoD's "closed within the published period"
    measure. Declared before `/{reference_no}` so the literal path wins."""
    return GrievanceQueueStatsOut(**grievance_service.queue_metrics(db, source_app=get_org_scope(user)))


@router.post("", response_model=GrievanceAcknowledgementOut, status_code=201)
def create_grievance_for_customer(
    payload: GrievanceStaffIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_MANAGE)),
):
    """Log a grievance that arrived off-platform (email, phone, post).

    It enters the same register with the same reference format and the same
    clock. `received_at` may be back-dated to when the complaint actually
    reached the organisation - the deadline is measured from receipt, not
    from data entry.
    """
    scope = get_org_scope(user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=payload.customer_external_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    grievance, notification_ids = grievance_service.create_grievance(
        db,
        customer=customer,
        category=payload.category,
        subject=payload.subject,
        description=payload.description,
        channel=payload.channel,
        source_app=customer.source_app,
        purpose_id=_purpose_id(db, payload.purpose_code),
        consent_id=payload.consent_id,
        received_at=payload.received_at,
        actor_username=user.username,
        actor_type="USER",
        actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
    )
    return _acknowledgement(db, grievance, notification_ids)


@router.get("/{reference_no}", response_model=GrievanceOut)
def get_grievance(
    reference_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_VIEW)),
):
    return _out(db, _staff_grievance(db, reference_no, user))


@router.patch("/{reference_no}", response_model=GrievanceOut)
def update_grievance(
    reference_no: str,
    payload: GrievanceUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_MANAGE)),
):
    """Triage: assign a handler, move it to IN_PROGRESS, and/or add a note."""
    g = _staff_grievance(db, reference_no, user)
    if g.status in GRIEVANCE_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Grievance is already {g.status}")
    try:
        g = grievance_service.update_grievance(
            db, g, assigned_to=payload.assigned_to, to_status=payload.status,
            note=payload.note, visible_to_principal=payload.visible_to_principal,
            actor_username=user.username, actor_type="USER", actor_id=str(user.id),
            actor_role=user.role.name if user.role else "",
        )
    except grievance_service.GrievanceTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(db, g)


@router.post("/{reference_no}/escalate", response_model=GrievanceOut)
def escalate(
    reference_no: str,
    payload: GrievanceEscalateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_MANAGE)),
):
    """Escalate to the DPO ahead of the deadline.

    The automatic escalation of an overdue grievance is a scheduled job
    (`app/jobs/grievance_escalation_job.py`), not this endpoint; this is the
    manual path for a handler who knows a complaint needs the DPO now.
    """
    g = _staff_grievance(db, reference_no, user)
    if g.status in GRIEVANCE_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Grievance is already {g.status}")
    if g.escalated_at is not None:
        raise HTTPException(status_code=409, detail="Grievance has already been escalated")
    g, _ = grievance_service.escalate_grievance(
        db, g, reason_code=payload.reason or "MANUAL", note=payload.note,
        actor_username=user.username, actor_type="USER", actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
    )
    return _out(db, g)


@router.post("/{reference_no}/resolve", response_model=GrievanceOut)
def resolve(
    reference_no: str,
    payload: GrievanceResolveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_MANAGE)),
):
    """Record the resolution summary and notify the complainant."""
    g = _staff_grievance(db, reference_no, user)
    customer = resolve_customer(db, source_app=g.source_app, customer_pk=g.customer_id)
    try:
        g, _ = grievance_service.resolve_grievance(
            db, g, customer, resolution_summary=payload.resolution_summary,
            actor_username=user.username, actor_type="USER", actor_id=str(user.id),
            actor_role=user.role.name if user.role else "",
        )
    except grievance_service.GrievanceTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(db, g)


@router.post("/{reference_no}/close", response_model=GrievanceOut)
def close(
    reference_no: str,
    payload: GrievanceCloseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_GRIEVANCE_MANAGE)),
):
    g = _staff_grievance(db, reference_no, user)
    try:
        g = grievance_service.close_grievance(
            db, g, note=payload.note, actor_username=user.username,
            actor_type="USER", actor_id=str(user.id),
            actor_role=user.role.name if user.role else "",
        )
    except grievance_service.GrievanceTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(db, g)
