"""R2-05 (E-01, E-02, E-03, E-05, E-07, E-09): the data-principal rights API.

Two audiences and one register, exactly like `routes/grievances.py`:

* the **principal's own surface** (`/rights/me/*`), authenticated by the same
  `X-Context-Token` every other self-service surface uses - and, unlike the
  grievance form, requiring that context to be **verified**. Lodging a
  complaint is harmless if it turns out to be from the wrong person; being
  handed a summary of someone's personal data, the identities of everyone it
  was disclosed to, or the ability to change their record is not. R.14(2)
  requires the fiduciary to verify the requester, and this is where that is
  enforced for self-service.
* the **handler's queue** (`/rights/requests*`), authenticated by a staff JWT
  and gated on `rights.view` / `rights.manage`, org-scoped through
  `get_org_scope` exactly like the customers/consents/audit/grievance lists.

A request is addressed by its `reference_no` in every path and a nomination
by its `nomination_ref`, never by a primary key - see
`app/schemas/rights.py`'s module docstring for why.

The one unauthenticated route here is `POST /rights/nominations/{ref}/confirm`,
because the person confirming is the **nominee**: a third party who has no
account on this platform and must not acquire one merely by being named. It is
protected the way any bearer-of-a-secret endpoint is - a CSPRNG code delivered
out of band to the nominee's own address, a constant-time comparison, a
per-nomination attempt cap, an expiry, an undifferentiated failure response,
and a per-IP rate limit - and confirming a nomination confers no ability to
read or change anything. Activation, which does confer something, is a
staff-only act requiring recorded evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import (
    get_customer_from_context,
    get_org_scope,
    require_permission,
    verify_context_token,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import PERM_RIGHTS_MANAGE, PERM_RIGHTS_VIEW
from app.core.utils import RateLimiter, mask_identifier
from app.models.entities import ConsentContext, User
from app.models.rights import (
    RIGHTS_REQUEST_TERMINAL_STATUSES,
    Nomination,
    RightsRequest,
)
from app.schemas.rights import (
    AccessPackageOut,
    CorrectionApplyIn,
    ErasureApprovalIn,
    NominationAcknowledgementOut,
    NominationActivateIn,
    NominationConfirmIn,
    NominationOut,
    NominationRevokeIn,
    NominationSelfIn,
    RightsAcknowledgementOut,
    RightsFulfilIn,
    RightsQueueStatsOut,
    RightsRejectIn,
    RightsRequestEventOut,
    RightsRequestOut,
    RightsRequestSelfIn,
    RightsRequestStaffIn,
    RightsRequestUpdateIn,
    RightsVerificationConfirmIn,
)
from app.services import rights_requests as rights_service
from app.services.tenancy import ANY_TENANT, resolve_customer

settings = get_settings()

router = APIRouter(prefix="/rights", tags=["rights"])

# The nominee confirmation endpoint is the one unauthenticated surface here.
# Five attempts per IP per fifteen minutes, on top of the per-nomination
# attempt cap the service enforces: the per-nomination cap stops an attacker
# grinding one reference, and this stops them spreading guesses across many.
nominee_confirm_limiter = RateLimiter(limit=5, window_seconds=900)


# --------------------------------------------------------------------------- #
#  Serialisation
# --------------------------------------------------------------------------- #

def _out(db: Session, r: RightsRequest, *, for_principal: bool = False) -> RightsRequestOut:
    """Serialise a rights request.

    `for_principal=True` filters the timeline to entries a handler marked
    visible: internal triage notes belong in the queue and in the audit
    ledger, not in the principal's view of her own case.
    """
    out = RightsRequestOut.model_validate(r)
    # Reads the external id off the request's own already-loaded `customer`
    # relationship purely to echo it back - the request itself was resolved
    # and tenant-checked before this is reached. Same justification as
    # routes/grievances.py::_out (see tests/test_customer_resolution_guard.py).
    principal = r.customer
    out.customer_external_id = principal.external_id if principal is not None else ""
    now = datetime.now(timezone.utc)
    out.overdue = r.is_overdue(now)
    out.days_remaining = r.remaining_days(now)

    if r.erasure_job_id is not None:
        job = rights_service.erasure_job_for(db, r)
        if job is not None:
            out.erasure_job_ref = job.job_ref
            out.erasure_job_status = job.status
    if r.nomination_id is not None:
        nomination = db.get(Nomination, r.nomination_id)
        if nomination is not None:
            out.nomination_ref = nomination.nomination_ref

    events = r.events or []
    if for_principal:
        events = [e for e in events if e.visible_to_principal]
    out.events = [RightsRequestEventOut.model_validate(e) for e in events]
    return out


def _nomination_out(n: Nomination) -> NominationOut:
    out = NominationOut.model_validate(n)
    # Masked even for the principal who supplied them - see NominationOut's
    # docstring. The nominee is a third party.
    out.nominee_email_masked = mask_identifier(n.nominee_email) or ""
    out.nominee_phone_masked = mask_identifier(n.nominee_phone) or ""
    customer = n.customer
    out.customer_external_id = customer.external_id if customer is not None else ""
    return out


def _acknowledgement(
    db: Session, r: RightsRequest, notification_ids: list[int]
) -> RightsAcknowledgementOut:
    contact = rights_service.tenant_contact(db, r.tenant_id)
    due = rights_service.as_utc(r.due_at)
    ack_due = rights_service.as_utc(r.acknowledgement_due_at)
    next_step = ""
    if not r.identity_verified:
        next_step = (
            "Before we can act on this request we have to establish that it came from you "
            "(DPDP Rules 2025 R.14(2)). We will send a verification code to the contact "
            "details already on file for your account - not to any address supplied with "
            "this request - and the request will be worked once you confirm it."
        )
    return RightsAcknowledgementOut(
        request=_out(db, r, for_principal=True),
        reference_no=r.reference_no,
        acknowledged=r.acknowledged_at is not None,
        acknowledgement_message=(
            f"Your {r.request_type.lower()} request has been registered under reference "
            f"{r.reference_no}. We publish a response period of {r.response_days} days, so you "
            f"will receive a response by {due.date().isoformat()}. If you are not satisfied with "
            "our response you may raise a grievance with our Data Protection Officer and, after "
            "exhausting it, complain to the Data Protection Board of India."
        ),
        acknowledgement_hours=r.acknowledgement_hours,
        acknowledgement_due_at=ack_due,
        response_days=r.response_days,
        due_at=due,
        identity_verified=bool(r.identity_verified),
        next_step=next_step,
        # The DPO's NAME is the published contact identity (already served by
        # GET /public/{tenant}/privacy-contact); the address is deliberately
        # not echoed into a submission response.
        data_protection_officer=contact["dpo_name"],
        grievance_url=contact["grievance_url"],
        board_complaint_url=contact["board_complaint_url"],
        notification_ids=notification_ids,
    )


# --------------------------------------------------------------------------- #
#  Lookup helpers
# --------------------------------------------------------------------------- #

def _by_reference(db: Session, reference_no: str) -> RightsRequest:
    r = db.query(RightsRequest).filter(RightsRequest.reference_no == reference_no).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rights request not found")
    return r


def _staff_request(db: Session, reference_no: str, user: User) -> RightsRequest:
    """Fetch a request for a staff caller, refusing one outside their org
    scope with the same 404 an unknown reference gets - an org-scoped handler
    must not be able to confirm that another tenant's reference exists."""
    r = _by_reference(db, reference_no)
    scope = get_org_scope(user)
    if scope and r.source_app != scope:
        raise HTTPException(status_code=404, detail="Rights request not found")
    return r


def _staff_nomination(db: Session, nomination_ref: str, user: User) -> Nomination:
    n = db.query(Nomination).filter(Nomination.nomination_ref == nomination_ref).first()
    if not n:
        raise HTTPException(status_code=404, detail="Nomination not found")
    scope = get_org_scope(user)
    if scope and n.source_app != scope:
        raise HTTPException(status_code=404, detail="Nomination not found")
    return n


def _principal_context(x_context_token: str, db: Session):
    """The `/rights/me*` credential check, identical to
    portal.py::_resolve_customer_and_context: the JWT's own `source_app` claim
    scopes the customer lookup, and the PERSISTED context row is then
    re-checked against that customer's tenant independently."""
    payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, payload)
    context = db.query(ConsentContext).filter(ConsentContext.token == x_context_token).first()
    if not context or context.source_app != customer.source_app:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    return customer, context


def _verified_principal_context(x_context_token: str, db: Session):
    """`_principal_context`, plus R.14(2).

    Every self-service rights surface goes through this rather than
    `_principal_context`. The distinction from the grievance form is
    deliberate and is the single most important line in this module: a
    grievance is a complaint anyone may make about their own treatment, while
    an access request returns one identified person's personal data and a
    correction request rewrites their record. `portal.py::_require_verified`
    draws the same line for consent changes; this is the rights-desk copy of
    it, and it honours the same `PORTAL_REQUIRE_VERIFICATION` switch so a
    deployment cannot end up with two different answers to "is verification
    on?".
    """
    customer, context = _principal_context(x_context_token, db)
    if settings.PORTAL_REQUIRE_VERIFICATION and context.verified_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Identity verification is required before exercising a data-principal right "
                "(DPDP Rules 2025 R.14(2))"
            ),
        )
    return customer, context


def _my_request(db: Session, reference_no: str, customer) -> RightsRequest:
    r = _by_reference(db, reference_no)
    if r.customer_id != customer.id or r.source_app != customer.source_app:
        # Same 404 as an unknown reference: a principal must not be able to
        # probe whether someone else's reference exists.
        raise HTTPException(status_code=404, detail="Rights request not found")
    return r


def _customer_for(db: Session, r: RightsRequest):
    customer = resolve_customer(db, source_app=r.source_app, customer_pk=r.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Data principal not found")
    return customer


def _actor(user: User) -> dict:
    return {
        "actor_username": user.username,
        "actor_type": "USER",
        "actor_id": str(user.id),
        "actor_role": user.role.name if user.role else "",
    }


def _refuse(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


# --------------------------------------------------------------------------- #
#  Data principal - the portal surface
# --------------------------------------------------------------------------- #

@router.post("/me/requests", response_model=RightsAcknowledgementOut, status_code=201)
def submit_my_request(
    payload: RightsRequestSelfIn,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """Make a rights request (ss.11-14) from the self-service portal.

    Requires a **verified** context; the requester is taken from that token
    and never from the body. Returns the reference number and the
    acknowledgement in one response - the principal never has to poll for
    either, and the acknowledgement states the published response period and
    the resulting deadline, which is what makes the promise auditable from her
    side too.
    """
    customer, context = _verified_principal_context(x_context_token, db)
    if payload.request_type == "NOMINATION":
        raise HTTPException(
            status_code=422,
            detail="Use POST /rights/me/nominations to make a s.14 nomination",
        )
    if payload.request_type == "CORRECTION" and not payload.requested_changes:
        raise HTTPException(
            status_code=422,
            detail="A correction request must say which fields are wrong and what they should be",
        )
    try:
        r, notification_ids = rights_service.create_request(
            db,
            customer=customer,
            request_type=payload.request_type,
            request_detail=payload.request_detail,
            requested_changes=payload.requested_changes,
            channel="PORTAL",
            source_app=context.source_app,
            verified_context=context,
            actor_username=f"principal:{customer.external_id}",
            actor_type="PRINCIPAL",
            actor_id=customer.external_id,
        )
    except rights_service.RightsRequestError as exc:
        raise _refuse(exc) from exc
    return _acknowledgement(db, r, notification_ids)


@router.get("/me/requests", response_model=list[RightsRequestOut])
def my_requests(
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _verified_principal_context(x_context_token, db)
    rows = (
        db.query(RightsRequest)
        .filter(
            RightsRequest.customer_id == customer.id,
            RightsRequest.source_app == context.source_app,
        )
        .order_by(RightsRequest.received_at.desc())
        .limit(200)
        .all()
    )
    return [_out(db, r, for_principal=True) for r in rows]


@router.get("/me/requests/{reference_no}", response_model=RightsRequestOut)
def my_request(
    reference_no: str,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, _ = _verified_principal_context(x_context_token, db)
    return _out(db, _my_request(db, reference_no, customer), for_principal=True)


@router.get("/me/requests/{reference_no}/package", response_model=AccessPackageOut)
def my_access_package(
    reference_no: str,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """s.11: the principal collects her own fulfilment package.

    Three independent checks stand between a caller and this data: the context
    token must be valid, it must be verified, and the request must belong to
    the customer that token resolves to. Each is enforced separately rather
    than one implying the others.
    """
    customer, _ = _verified_principal_context(x_context_token, db)
    r = _my_request(db, reference_no, customer)
    if r.request_type != "ACCESS":
        raise HTTPException(status_code=409, detail="This is not an access request")
    rights_service.require_verified(r)
    package = rights_service.build_access_package(db, r, customer)
    from app.services.audit import log_audit

    log_audit(
        db, "RIGHTS_REQUEST_PACKAGE_ISSUED",
        actor_username=f"principal:{customer.external_id}", actor_type="PRINCIPAL",
        actor_id=customer.external_id, source_app=r.source_app, tenant_id=r.tenant_id,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"s.11 access package generated for {r.reference_no}",
        metadata={
            "reference_no": r.reference_no,
            "package_hash": package["package_hash"],
            "recipients": len(package["recipients"]),
        },
    )
    return AccessPackageOut(**package)


# --------------------------------------------------------------------------- #
#  Data principal - s.14 nominations
# --------------------------------------------------------------------------- #

@router.post("/me/nominations", response_model=NominationAcknowledgementOut, status_code=201)
def create_my_nomination(
    payload: NominationSelfIn,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """s.14: nominate an individual to exercise your rights on your death or
    incapacity.

    Creates the nomination PENDING and sends the nominee a confirmation code
    at their own address. It confers nothing until they confirm - naming
    someone is an assertion about a person who has not been asked.

    A NOMINATION rights request is registered alongside it, so a nomination
    appears in the same queue, on the same clock, with the same
    acknowledgement and closure evidence as the other three rights.
    """
    customer, context = _verified_principal_context(x_context_token, db)
    nomination, code = rights_service.create_nomination(
        db,
        customer=customer,
        nominee_name=payload.nominee_name,
        nominee_email=payload.nominee_email,
        nominee_phone=payload.nominee_phone,
        nominee_relationship=payload.nominee_relationship,
        source_app=context.source_app,
        context=context,
        actor_username=f"principal:{customer.external_id}",
        actor_id=customer.external_id,
    )
    db.commit()
    db.refresh(nomination)
    # The plaintext code leaves this process only through the notification
    # service, addressed to the nominee. It is never returned to the
    # principal and never logged.
    rights_service.send_nominee_confirmation(
        db, nomination, code, actor_username=f"principal:{customer.external_id}"
    )
    db.commit()

    r, _notification_ids = rights_service.create_request(
        db,
        customer=customer,
        request_type="NOMINATION",
        request_detail=(
            f"Nomination of {payload.nominee_name} "
            f"({payload.nominee_relationship or 'relationship not stated'}) under DPDP Act s.14."
        ),
        channel="PORTAL",
        source_app=context.source_app,
        verified_context=context,
        actor_username=f"principal:{customer.external_id}",
        actor_type="PRINCIPAL",
        actor_id=customer.external_id,
    )
    r.nomination_id = nomination.id
    db.commit()
    db.refresh(r)
    return NominationAcknowledgementOut(
        nomination=_nomination_out(nomination),
        request=_out(db, r, for_principal=True),
        message=(
            f"Nomination {nomination.nomination_ref} has been recorded and a confirmation code "
            f"sent to your nominee. It confers nothing until they confirm it, and it takes "
            f"effect only if this organisation activates it against recorded evidence of your "
            f"death or incapacity. You may revoke it at any time."
        ),
    )


@router.get("/me/nominations", response_model=list[NominationOut])
def my_nominations(
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _verified_principal_context(x_context_token, db)
    rows = (
        db.query(Nomination)
        .filter(
            Nomination.customer_id == customer.id,
            Nomination.source_app == context.source_app,
        )
        .order_by(Nomination.nominated_at.desc())
        .limit(100)
        .all()
    )
    return [_nomination_out(n) for n in rows]


@router.post("/me/nominations/{nomination_ref}/revoke", response_model=NominationOut)
def revoke_my_nomination(
    nomination_ref: str,
    payload: NominationRevokeIn,
    x_context_token: str = Header(..., alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, _ = _verified_principal_context(x_context_token, db)
    n = db.query(Nomination).filter(Nomination.nomination_ref == nomination_ref).first()
    if not n or n.customer_id != customer.id or n.source_app != customer.source_app:
        raise HTTPException(status_code=404, detail="Nomination not found")
    try:
        n = rights_service.revoke_nomination(
            db, n, reason=payload.reason,
            actor_username=f"principal:{customer.external_id}",
            actor_type="PRINCIPAL", actor_id=customer.external_id,
        )
    except rights_service.RightsRequestError as exc:
        raise _refuse(exc) from exc
    return _nomination_out(n)


@router.post("/nominations/{nomination_ref}/confirm")
def confirm_nomination(
    nomination_ref: str,
    payload: NominationConfirmIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """The **nominee** confirms, with the code sent to their own address.

    Deliberately unauthenticated: the nominee is a third party with no account
    here, and creating one for them merely so they can decline would be
    collecting personal data nobody asked for.

    Every failure - a wrong code, an expired window, an exhausted attempt
    budget, an unknown reference, a nomination that is not PENDING - returns
    the same `{"confirmed": false}`. The endpoint therefore cannot be used to
    discover which nomination references exist, which is what stops it
    becoming an oracle over who has nominated whom.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not nominee_confirm_limiter.allow(f"nominee:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many attempts; try again later")
    n = db.query(Nomination).filter(Nomination.nomination_ref == nomination_ref).first()
    if n is None:
        return {"confirmed": False}
    confirmed = rights_service.confirm_nomination(db, n, payload.code)
    return {"confirmed": confirmed}


# --------------------------------------------------------------------------- #
#  Staff - the admin queue
# --------------------------------------------------------------------------- #

@router.get("/requests", response_model=list[RightsRequestOut])
def list_requests(
    status: str | None = Query(default=None, description="Exact status filter"),
    request_type: str | None = Query(default=None, description="ACCESS|CORRECTION|ERASURE|NOMINATION"),
    assigned_to: str | None = None,
    customer_external_id: str | None = None,
    open_only: bool = Query(default=False, description="Exclude FULFILLED, REJECTED and CLOSED"),
    overdue_only: bool = Query(default=False, description="Open and past due_at"),
    unverified_only: bool = Query(default=False, description="Identity not yet established"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_VIEW)),
):
    """The queue, with its SLA timers.

    Every filter is a structured column. There is deliberately no free-text
    search over `request_detail` or `resolution_summary`: those are AES-GCM
    encrypted at rest (see app/models/rights.py), so `LIKE` against them would
    silently match nothing rather than fail, which is worse than not offering
    it.

    Ordered by soonest deadline first: the queue's job is to surface what is
    about to breach, not what arrived most recently. Each row carries
    `overdue` and `days_remaining` for the SLA timer, plus
    `acknowledgement_due_at` for the K-21 clock.
    """
    scope = get_org_scope(user)
    q = db.query(RightsRequest)
    if scope:
        q = q.filter(RightsRequest.source_app == scope)
    if customer_external_id:
        customer = resolve_customer(
            db, source_app=scope or ANY_TENANT, external_id=customer_external_id
        )
        if not customer:
            return []
        q = q.filter(RightsRequest.customer_id == customer.id)
    if status:
        q = q.filter(RightsRequest.status == status)
    if request_type:
        q = q.filter(RightsRequest.request_type == request_type)
    if assigned_to:
        q = q.filter(RightsRequest.assigned_to == assigned_to)
    if unverified_only:
        q = q.filter(RightsRequest.identity_verified.is_(False))
    if open_only or overdue_only:
        q = q.filter(RightsRequest.status.notin_(RIGHTS_REQUEST_TERMINAL_STATUSES))
    if overdue_only:
        q = q.filter(RightsRequest.due_at <= datetime.now(timezone.utc))
    rows = q.order_by(RightsRequest.due_at.asc()).offset(offset).limit(limit).all()
    return [_out(db, r) for r in rows]


@router.get("/requests/stats", response_model=RightsQueueStatsOut)
def request_stats(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_VIEW)),
):
    """Queue counts plus K-20 (`by_type`), K-21
    (`median_acknowledgement_hours`) and K-22 (`on_time_closure_rate`).

    Declared before `/requests/{reference_no}` so the literal path wins.
    `app/services/kpi_catalogue.py` republishes exactly these numbers, so this
    screen and the compliance dashboard cannot disagree.
    """
    return RightsQueueStatsOut(
        **rights_service.queue_metrics(db, source_app=get_org_scope(user))
    )


@router.post("/requests", response_model=RightsAcknowledgementOut, status_code=201)
def create_request_for_principal(
    payload: RightsRequestStaffIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """Log a request that arrived off-platform (email, phone, post).

    It enters the same register with the same reference format and the same
    clock, and `received_at` may be back-dated to when the request actually
    reached the organisation - the deadline is measured from receipt, not from
    data entry.

    It starts **UNVERIFIED** and lands in VERIFYING. A handler cannot mark it
    verified: the only route out is
    `POST /rights/requests/{ref}/verification/start`, which sends a code to
    the address already on file, followed by the principal reading that code
    back. This is the difference between a rights desk and a social-engineering
    surface.
    """
    scope = get_org_scope(user)
    customer = resolve_customer(
        db, source_app=scope or ANY_TENANT, external_id=payload.customer_external_id
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        r, notification_ids = rights_service.create_request(
            db,
            customer=customer,
            request_type=payload.request_type,
            request_detail=payload.request_detail,
            requested_changes=payload.requested_changes,
            channel=payload.channel,
            source_app=customer.source_app,
            received_at=payload.received_at,
            **_actor(user),
        )
    except rights_service.RightsRequestError as exc:
        raise _refuse(exc) from exc
    return _acknowledgement(db, r, notification_ids)


@router.get("/requests/{reference_no}", response_model=RightsRequestOut)
def get_request(
    reference_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_VIEW)),
):
    return _out(db, _staff_request(db, reference_no, user))


@router.patch("/requests/{reference_no}", response_model=RightsRequestOut)
def update_request(
    reference_no: str,
    payload: RightsRequestUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """Triage: assign a handler, move it along, add a note.

    `status` accepts only VERIFYING and IN_PROGRESS. The outcome states are
    reached through their own endpoints, each of which has preconditions this
    one deliberately cannot bypass - and IN_PROGRESS is itself refused for an
    unverified request, so triage cannot smuggle a request past R.14(2).
    """
    r = _staff_request(db, reference_no, user)
    if r.status in RIGHTS_REQUEST_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Request is already {r.status}")
    if payload.status == "IN_PROGRESS" and not r.identity_verified:
        raise HTTPException(
            status_code=409,
            detail=(
                "The requester's identity has not been established (DPDP Rules 2025 R.14(2)). "
                "Send a verification code with POST /rights/requests/"
                f"{reference_no}/verification/start."
            ),
        )
    try:
        r = rights_service.update_request(
            db, r, assigned_to=payload.assigned_to, to_status=payload.status,
            note=payload.note, visible_to_principal=payload.visible_to_principal,
            **_actor(user),
        )
    except rights_service.RightsTransitionError as exc:
        raise _refuse(exc) from exc
    return _out(db, r)


# --------------------------------------------------------------------------- #
#  Staff - R.14(2) identity verification
# --------------------------------------------------------------------------- #

@router.post("/requests/{reference_no}/verification/start", response_model=RightsRequestOut)
def start_verification(
    reference_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """Send the R.14(2) verification code for an off-platform request.

    The code goes to the contact details **already on file** for the
    principal, via `app/services/otp.py`. It is never returned in this
    response, never logged, and the handler who triggers it does not see it -
    which is precisely what makes the subsequent confirmation evidence of the
    principal's identity rather than of the handler's confidence.
    """
    r = _staff_request(db, reference_no, user)
    if r.identity_verified:
        raise HTTPException(status_code=409, detail="Identity has already been established")
    if r.status in RIGHTS_REQUEST_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Request is already {r.status}")
    customer = _customer_for(db, r)
    r, _context = rights_service.start_identity_verification(
        db, r, customer,
        actor_username=user.username, actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
    )
    return _out(db, r)


@router.post("/requests/{reference_no}/verification/confirm", response_model=RightsRequestOut)
def confirm_verification(
    reference_no: str,
    payload: RightsVerificationConfirmIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """Confirm the code the principal received.

    A wrong or expired code is a 403 with no detail about which it was -
    `app/services/otp.py` makes every failure mode indistinguishable and this
    endpoint preserves that.
    """
    r = _staff_request(db, reference_no, user)
    ok = rights_service.confirm_identity_verification(
        db, r, payload.code,
        actor_username=user.username, actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
    )
    if not ok:
        raise HTTPException(status_code=403, detail="Verification failed")
    return _out(db, r)


# --------------------------------------------------------------------------- #
#  Staff - fulfilment by type
# --------------------------------------------------------------------------- #

@router.get("/requests/{reference_no}/package", response_model=AccessPackageOut)
def access_package(
    reference_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_VIEW)),
):
    """s.11(1)(a)-(b): build the fulfilment package for an access request.

    Refuses while the requester's identity is unestablished, even for a
    permissioned handler - this endpoint's output is the thing that gets sent
    to whoever asked, and generating it before verification is how the wrong
    person ends up with it.
    """
    r = _staff_request(db, reference_no, user)
    if r.request_type != "ACCESS":
        raise HTTPException(status_code=409, detail="This is not an access request")
    try:
        rights_service.require_verified(r)
    except rights_service.IdentityNotVerified as exc:
        raise _refuse(exc) from exc
    customer = _customer_for(db, r)
    package = rights_service.build_access_package(db, r, customer)
    from app.services.audit import log_audit

    log_audit(
        db, "RIGHTS_REQUEST_PACKAGE_ISSUED",
        source_app=r.source_app, tenant_id=r.tenant_id, customer_id=customer.id,
        customer_external_id=customer.external_id,
        reason=f"s.11 access package generated for {r.reference_no}",
        metadata={
            "reference_no": r.reference_no,
            "package_hash": package["package_hash"],
            "recipients": len(package["recipients"]),
        },
        **_actor(user),
    )
    return AccessPackageOut(**package)


@router.post("/requests/{reference_no}/correction", response_model=RightsRequestOut)
def apply_correction(
    reference_no: str,
    payload: CorrectionApplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """s.12(1)-(2): the staff customer-update endpoint.

    This is the write to a `customers` row that E-02 records as missing, and
    it is bound to a correction request on purpose - see
    `app/services/rights_requests.py::apply_correction`. It maintains the
    `email_search` HMAC companion, records the before-image as evidence, and
    writes both a CUSTOMER_UPDATED and a RIGHTS_REQUEST_CORRECTION_APPLIED
    audit row.
    """
    r = _staff_request(db, reference_no, user)
    customer = _customer_for(db, r)
    changes = payload.changes if payload.changes is not None else (r.requested_changes or {})
    try:
        r = rights_service.apply_correction(
            db, r, customer, changes=changes, note=payload.note,
            actor_username=user.username, actor_id=str(user.id),
            actor_role=user.role.name if user.role else "",
        )
    except rights_service.IdentityNotVerified as exc:
        raise _refuse(exc) from exc
    except rights_service.RightsRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _out(db, r)


@router.post("/requests/{reference_no}/erasure", response_model=RightsRequestOut)
def approve_erasure(
    reference_no: str,
    payload: ErasureApprovalIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """s.12(3): approve an erasure request by raising an R1-06 erasure job.

    Nothing is erased here and nothing is erased by this approval. The job
    that comes back still has to have its R.8(2) forty-eight-hour notice sent
    and elapse, still re-checks the retention floors and any legal hold at
    execution time, and still needs `erasure.manage` to execute - so the
    rights desk cannot destroy anyone's data on its own. The request stays
    IN_PROGRESS and becomes FULFILLED only once that job reaches EXECUTED.
    """
    r = _staff_request(db, reference_no, user)
    customer = _customer_for(db, r)
    try:
        rights_service.approve_erasure(
            db, r, customer, basis=payload.basis,
            actor_username=user.username, actor_id=str(user.id),
            actor_role=user.role.name if user.role else "",
        )
    except rights_service.IdentityNotVerified as exc:
        raise _refuse(exc) from exc
    except rights_service.RightsRequestError as exc:
        raise _refuse(exc) from exc
    return _out(db, r)


@router.post("/requests/{reference_no}/fulfil", response_model=RightsRequestOut)
def fulfil_request(
    reference_no: str,
    payload: RightsFulfilIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """Record that the right was given effect, notify the principal, and close
    with evidence.

    For an ERASURE request this refuses unless the erasure job has actually
    reached EXECUTED - the register never claims a person was erased on the
    strength of a job still waiting out its notice period.
    """
    r = _staff_request(db, reference_no, user)
    customer = _customer_for(db, r)
    package_hash = None
    if r.request_type == "ACCESS":
        # Recompute and pin the package hash at fulfilment, so the closure
        # evidence names exactly what was delivered.
        try:
            rights_service.require_verified(r)
        except rights_service.IdentityNotVerified as exc:
            raise _refuse(exc) from exc
        package_hash = rights_service.build_access_package(db, r, customer)["package_hash"]
    try:
        r, _ids = rights_service.fulfil_request(
            db, r, customer, resolution_summary=payload.resolution_summary,
            package_hash=package_hash, **_actor(user),
        )
    except rights_service.IdentityNotVerified as exc:
        raise _refuse(exc) from exc
    except (rights_service.RightsRequestError, rights_service.RightsTransitionError) as exc:
        raise _refuse(exc) from exc
    return _out(db, r)


@router.post("/requests/{reference_no}/reject", response_model=RightsRequestOut)
def reject_request(
    reference_no: str,
    payload: RightsRejectIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """Refuse a request lawfully, recording the ground and telling the
    principal - including that a grievance and ultimately the Board remain
    open to them."""
    r = _staff_request(db, reference_no, user)
    customer = _customer_for(db, r)
    try:
        r, _ids = rights_service.reject_request(
            db, r, customer, reason=payload.reason, basis=payload.basis, **_actor(user),
        )
    except rights_service.RightsTransitionError as exc:
        raise _refuse(exc) from exc
    return _out(db, r)


# --------------------------------------------------------------------------- #
#  Staff - nominations
# --------------------------------------------------------------------------- #

@router.get("/nominations", response_model=list[NominationOut])
def list_nominations(
    status: str | None = None,
    customer_external_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(Nomination)
    if scope:
        q = q.filter(Nomination.source_app == scope)
    if customer_external_id:
        customer = resolve_customer(
            db, source_app=scope or ANY_TENANT, external_id=customer_external_id
        )
        if not customer:
            return []
        q = q.filter(Nomination.customer_id == customer.id)
    if status:
        q = q.filter(Nomination.status == status)
    rows = q.order_by(Nomination.nominated_at.desc()).offset(offset).limit(limit).all()
    return [_nomination_out(n) for n in rows]


@router.get("/nominations/{nomination_ref}", response_model=NominationOut)
def get_nomination(
    nomination_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_VIEW)),
):
    return _nomination_out(_staff_nomination(db, nomination_ref, user))


@router.post("/nominations/{nomination_ref}/activate", response_model=NominationOut)
def activate_nomination(
    nomination_ref: str,
    payload: NominationActivateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    """s.14: activate a confirmed nomination on the principal's death or
    incapacity.

    Staff-only, never self-service, and reachable only from VERIFIED. The
    triggering event is exactly the moment the principal cannot contradict a
    false claim, so the platform requires that the nominee was independently
    confirmed beforehand, that a named human takes responsibility, that a
    statutory ground is stated, and that an evidence reference an auditor can
    check is recorded. `ck_nominations_active_is_authorised` enforces the last
    three at the database level.
    """
    n = _staff_nomination(db, nomination_ref, user)
    try:
        n = rights_service.activate_nomination(
            db, n, ground=payload.ground, evidence_ref=payload.evidence_ref,
            note=payload.note, actor_username=user.username, actor_id=str(user.id),
            actor_role=user.role.name if user.role else "",
        )
    except rights_service.RightsTransitionError as exc:
        raise _refuse(exc) from exc
    except rights_service.RightsRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _nomination_out(n)


@router.post("/nominations/{nomination_ref}/revoke", response_model=NominationOut)
def revoke_nomination(
    nomination_ref: str,
    payload: NominationRevokeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_RIGHTS_MANAGE)),
):
    n = _staff_nomination(db, nomination_ref, user)
    try:
        n = rights_service.revoke_nomination(
            db, n, reason=payload.reason, actor_username=user.username,
            actor_type="USER", actor_id=str(user.id),
        )
    except rights_service.RightsTransitionError as exc:
        raise _refuse(exc) from exc
    return _nomination_out(n)
