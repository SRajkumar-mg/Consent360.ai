"""R1-06 (G-01..G-04, G-06, G-08): the retention and erasure engine's API.

Permissions, and why these ones:

* Reading policies, holds, jobs and the G-08 metrics needs **`erasure.view`** -
  compliance reporting, granted to every role that can already read the audit
  trail.
* Every act that can lead to an irreversible destruction - writing a retention
  policy, raising an erasure, authorising one, executing one - needs
  **`erasure.manage`**, granted only to `admin` and `dpo`.
* Placing and releasing a **legal hold** also needs `erasure.manage`. Both
  directions are consequential: placing one stops a statutory erasure, and
  releasing one un-stops it, so the release is exactly as sensitive as the
  erasure it unblocks.

`erasure.manage` is deliberately NOT `policy.manage`. R1-10 gated record-class
retention on `policy.manage` because "how long do we keep this class of
record" is a policy judgement. This is a different act: destroying one named
human being's personal data, irreversibly, on a schedule. Anyone who can edit
a retention schedule should not thereby be able to erase a person. See
`app/core/rbac.py` for the per-role grants.

**A re-seed is required.** `rbac.py`'s `ROLE_PERMISSIONS` is copied into the
`roles` table by `seed.py` and re-synced at startup by `rbac.sync_roles`, so
`erasure.view` / `erasure.manage` grant nothing to an existing deployment's
roles until one of those has run.

Route order matters: `/erasure/jobs/metrics` and the two `/erasure/scan/*`
paths are declared before `/erasure/jobs/{job_ref}`, or FastAPI would match
them as a job reference.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_ERASURE_MANAGE, PERM_ERASURE_VIEW
from app.core.utils import get_request_id
from app.models.entities import Customer, Organization, User
from app.models.erasure import ErasureJob, LegalHold, RetentionPolicy
from app.schemas.erasure import (
    ErasureAuthoriseIn,
    ErasureCancelIn,
    ErasureExecuteIn,
    ErasureJobOut,
    ErasureMetricsOut,
    ErasureRequestIn,
    ErasureScanOut,
    LegalHoldIn,
    LegalHoldOut,
    LegalHoldReleaseIn,
    RetentionPolicyIn,
    RetentionPolicyOut,
)
from app.services import erasure as erasure_service
from app.services.retention import RetentionFloorViolation
from app.services.tenancy import ANY_TENANT, resolve_customer

router = APIRouter(prefix="/erasure", tags=["erasure"])


def _policy_out(db: Session, row: RetentionPolicy) -> RetentionPolicyOut:
    out = RetentionPolicyOut.model_validate(row)
    out.floor_days = erasure_service.floor_for_record_class(db, row.record_class)
    from app.models.erasure import HARD_DELETABLE_RECORD_CLASSES

    out.hard_delete_permitted = row.record_class in HARD_DELETABLE_RECORD_CLASSES
    return out


def _resolve_principal(db: Session, external_id: str, user: User) -> Customer:
    """Resolve a principal by external id, honouring the caller's org scope.

    A wrong-tenant match is a 404, never a 403 - the same idiom every other
    customer lookup in this codebase uses, and it matters more here than
    anywhere: a 403 would confirm to an org-scoped admin that a given external
    id exists in some other tenant, which is exactly the cross-tenant
    disclosure `tests/test_cross_tenant_context.py` exists to prevent.
    """
    scope = get_org_scope(user)
    customer = resolve_customer(
        db, source_app=scope if scope else ANY_TENANT, external_id=external_id
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


def _scoped_jobs(db: Session, user: User):
    q = db.query(ErasureJob)
    scope = get_org_scope(user)
    if scope:
        q = q.filter(ErasureJob.source_app == scope)
    return q


# --------------------------------------------------------------------------- #
# Retention policies
# --------------------------------------------------------------------------- #
@router.get("/policies", response_model=list[RetentionPolicyOut])
def list_policies(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_ERASURE_VIEW)),
):
    """Every configured retention ceiling, each shown next to the statutory
    floor in force for its class so the two can be compared directly."""
    erasure_service.ensure_default_policies(db)
    rows = db.query(RetentionPolicy).order_by(RetentionPolicy.record_class, RetentionPolicy.scope).all()
    return [_policy_out(db, r) for r in rows]


@router.put("/policies", response_model=RetentionPolicyOut)
def upsert_policy(
    payload: RetentionPolicyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Create or replace the policy for one (record_class, scope).

    A ceiling below the statutory floor, or a hard delete of a class that may
    never be hard-deleted, is refused with 422. That refusal is the point of
    the endpoint: it is where "the floor wins" stops being a comment.
    """
    try:
        row = erasure_service.upsert_retention_policy(
            db,
            record_class=payload.record_class,
            scope=payload.scope,
            retention_days=payload.retention_days,
            inactivity_days=payload.inactivity_days,
            pre_erasure_notice_hours=payload.pre_erasure_notice_hours,
            action=payload.action,
            legal_basis_for_retention=payload.legal_basis_for_retention,
            is_active=payload.is_active,
            notes=payload.notes,
            actor_username=user.username,
            request_id=get_request_id(),
        )
    except RetentionFloorViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _policy_out(db, row)


# --------------------------------------------------------------------------- #
# Legal holds
# --------------------------------------------------------------------------- #
@router.get("/holds", response_model=list[LegalHoldOut])
def list_holds(
    active_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_ERASURE_VIEW)),
):
    q = db.query(LegalHold)
    if active_only:
        q = q.filter(LegalHold.is_active.is_(True))
    return q.order_by(LegalHold.id.desc()).all()


@router.post("/holds", response_model=LegalHoldOut, status_code=201)
def place_hold(
    payload: LegalHoldIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Place a legal hold. A hold outranks both the retention ceiling and the
    principal's own s.12(3) request - s.8(7) permits retention that is
    "necessary for compliance with any law for the time being in force" - so
    it blocks erasure rather than delaying it, and a blocked job says which
    hold blocked it."""
    customer_id = None
    if payload.customer_external_id:
        customer_id = _resolve_principal(db, payload.customer_external_id, user).id
    tenant_id = None
    if payload.tenant_code:
        organization = db.query(Organization).filter(Organization.code == payload.tenant_code).first()
        if not organization:
            raise HTTPException(status_code=404, detail="Unknown tenant_code")
        tenant_id = organization.id
    try:
        return erasure_service.place_legal_hold(
            db, legal_basis=payload.legal_basis, reason=payload.reason,
            customer_id=customer_id, record_class=payload.record_class, tenant_id=tenant_id,
            expires_at=payload.expires_at, placed_by=user.username,
            request_id=get_request_id(),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/holds/{hold_ref}/release", response_model=LegalHoldOut)
def release_hold(
    hold_ref: str,
    payload: LegalHoldReleaseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    hold = db.query(LegalHold).filter(LegalHold.hold_ref == hold_ref).first()
    if not hold:
        raise HTTPException(status_code=404, detail="Legal hold not found")
    return erasure_service.release_legal_hold(
        db, hold, released_by=user.username, release_reason=payload.release_reason,
        request_id=get_request_id(),
    )


# --------------------------------------------------------------------------- #
# Scans and metrics - declared before /jobs/{job_ref}
# --------------------------------------------------------------------------- #
@router.get("/metrics", response_model=ErasureMetricsOut)
def metrics(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_ERASURE_VIEW)),
):
    """G-08 / K-28..K-31: erasure backlog, turnaround, pre-erasure notice
    compliance and inactivity-clock breaches, computed from the job rows."""
    return erasure_service.erasure_metrics(db)


@router.post("/scan/retention", response_model=ErasureScanOut)
def scan_retention(
    propose: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Principals whose configured retention period has run out.

    Proposes `erasure_jobs` rows; erases nothing. Pass `propose=false` for a
    pure count. Every job it raises is PROPOSED and unauthorised, so nothing
    can act on it until a named human authorises it.
    """
    return erasure_service.erasure_retention_scan(
        db, actor_username=user.username, propose=propose
    )


@router.post("/scan/inactivity", response_model=ErasureScanOut)
def scan_inactivity(
    propose: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """DPDP Rules 2025 R.8(1) read with the Third Schedule: principals who
    have neither approached this fiduciary nor exercised their rights for the
    configured period (three years for the Third Schedule classes).

    Proposes; erases nothing.
    """
    return erasure_service.inactivity_scan(db, actor_username=user.username, propose=propose)


# --------------------------------------------------------------------------- #
# Erasure jobs
# --------------------------------------------------------------------------- #
@router.get("/jobs", response_model=list[ErasureJobOut])
def list_jobs(
    status: Optional[str] = Query(default=None),
    customer_external_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_VIEW)),
):
    q = _scoped_jobs(db, user)
    if status:
        q = q.filter(ErasureJob.status == status)
    if customer_external_id:
        q = q.filter(ErasureJob.customer_id == _resolve_principal(db, customer_external_id, user).id)
    return q.order_by(ErasureJob.id.desc()).limit(limit).all()


@router.post("/jobs", response_model=ErasureJobOut, status_code=201)
def raise_job(
    payload: ErasureRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Raise an erasure for one principal - an approved s.12(3) rights
    request, or a manual erasure with a recorded reason.

    The approving officer is the authoriser, so the job is created already
    authorised: the Act asks for the fiduciary's decision, not for two of
    them. The R.8(2) notice and its period still apply, and nothing is
    destroyed until `POST /erasure/jobs/{job_ref}/execute` (or the executor
    job) runs after that window.
    """
    customer = _resolve_principal(db, payload.customer_external_id, user)
    if payload.trigger == "RIGHTS_REQUEST":
        if not payload.request_ref:
            raise HTTPException(
                status_code=422,
                detail=(
                    "request_ref is required for a RIGHTS_REQUEST erasure: it names the s.12(3) "
                    "request being approved (a grievance reference_no) so the register entry and "
                    "the erasure can be reconciled from either side."
                ),
            )
        job = erasure_service.approve_rights_request_erasure(
            db, customer, request_ref=payload.request_ref, approved_by=user.username,
            basis=payload.authorisation_basis, source_app=customer.source_app or "",
            request_id=get_request_id(),
        )
    else:
        job = erasure_service.propose_erasure_job(
            db, customer, trigger="MANUAL",
            trigger_ref=f"manual:{user.username}:{get_request_id()}",
            reason=payload.reason or "Manual erasure raised by a staff user",
            source_app=customer.source_app or "", actor_username=user.username,
            request_id=get_request_id(), authorised_by=user.username,
            authorisation_basis=payload.authorisation_basis,
        )
    return job


@router.get("/jobs/{job_ref}", response_model=ErasureJobOut)
def get_job(
    job_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_VIEW)),
):
    job = _scoped_jobs(db, user).filter(ErasureJob.job_ref == job_ref).first()
    if not job:
        raise HTTPException(status_code=404, detail="Erasure job not found")
    return job


@router.post("/jobs/{job_ref}/authorise", response_model=ErasureJobOut)
def authorise_job(
    job_ref: str,
    payload: ErasureAuthoriseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Take responsibility for an irreversible act.

    Required for every job a clock proposed. A scan noticing that a period has
    elapsed is an inference about somebody else's data, not a decision anyone
    made, and the executor will not touch a job that nobody has signed.
    """
    job = _scoped_jobs(db, user).filter(ErasureJob.job_ref == job_ref).first()
    if not job:
        raise HTTPException(status_code=404, detail="Erasure job not found")
    try:
        return erasure_service.authorise_erasure_job(
            db, job, actor_username=user.username, basis=payload.basis,
            request_id=get_request_id(),
        )
    except erasure_service.ErasureNotAuthorised as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/jobs/{job_ref}/notice", response_model=ErasureJobOut)
def send_notice(
    job_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Send the R.8(2) pre-erasure notice now rather than waiting for the
    scheduled sweep. Idempotent: a job that already has a notice on record is
    returned untouched, so this cannot be used to restart a principal's 48
    hours."""
    job = _scoped_jobs(db, user).filter(ErasureJob.job_ref == job_ref).first()
    if not job:
        raise HTTPException(status_code=404, detail="Erasure job not found")
    try:
        return erasure_service.send_pre_erasure_notice(
            db, job, actor_username=user.username, request_id=get_request_id()
        )
    except erasure_service.ErasureNotAuthorised as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except erasure_service.ErasureError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/jobs/{job_ref}/execute", response_model=ErasureJobOut)
def execute_job(
    job_ref: str,
    payload: ErasureExecuteIn = ErasureExecuteIn(),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    """Perform the erasure. Irreversible.

    409 when a precondition does not hold - unauthorised, no notice on record,
    notice period not elapsed, or a legal hold in force. There is no override:
    the notice window and the hold are the only two things standing between a
    mistake and an irreversible one.
    """
    job = _scoped_jobs(db, user).filter(ErasureJob.job_ref == job_ref).first()
    if not job:
        raise HTTPException(status_code=404, detail="Erasure job not found")
    try:
        return erasure_service.execute_erasure_job(
            db, job, actor_username=user.username, request_id=get_request_id()
        )
    except (erasure_service.ErasureNotAuthorised, erasure_service.ErasureBlocked) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except erasure_service.ErasureError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/jobs/{job_ref}/cancel", response_model=ErasureJobOut)
def cancel_job(
    job_ref: str,
    payload: ErasureCancelIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_ERASURE_MANAGE)),
):
    job = _scoped_jobs(db, user).filter(ErasureJob.job_ref == job_ref).first()
    if not job:
        raise HTTPException(status_code=404, detail="Erasure job not found")
    try:
        return erasure_service.cancel_erasure_job(
            db, job, actor_username=user.username, reason=payload.reason,
            request_id=get_request_id(),
        )
    except erasure_service.ErasureError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
