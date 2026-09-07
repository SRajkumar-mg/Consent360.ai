"""R3-08 (I-01..I-04, H-12): the breach register API.

Permissions, and why these ones:

* Reading the register, a breach, its clocks, its filings and K-39..K-41 needs
  **`breach.view`** - compliance reporting, granted to every role that can
  already read the audit trail (admin, DPO, auditor, operator, viewer,
  consent_manager and the three org-scoped admins, the last four read-only
  within their own tenant).
* Reading a filing's *content* needs **`audit.export`**: a principal notice
  quotes that principal's own data and a Board report aggregates every
  affected principal, so it is held to the same bar as the ledger export
  rather than to the register's own read permission.
* Every state-changing action - registering a breach, recording awareness,
  scoping affected principals, issuing notices, filing with the Board or
  CERT-In, logging an extension request, closing - needs **`breach.manage`**,
  granted only to `admin` and `dpo`.

`breach.manage` is deliberately NOT `policy.manage`. Filing - or failing to
file - a statutory notification with the Data Protection Board under R.7(2),
or the CERT-In report under the 28 Apr 2022 directions, is a regulator-facing
act of a wholly different character from editing a policy document; anyone who
can do the second should not thereby be able to do the first. See
`app/core/rbac.py` for the full rationale and the per-role grants.

**A re-seed is required.** `rbac.py`'s `ROLE_PERMISSIONS` is copied into the
`roles` table by `seed.py` (and re-synced at startup by `rbac.sync_roles`), so
`breach.view` / `breach.manage` grant nothing to an existing deployment's roles
until one of those two has run against its database.

Route order matters: `/breaches/metrics` and `/breaches/register` are declared
before `/breaches/{breach_ref}`, or FastAPI would match them as a breach_ref.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_EXPORT, PERM_BREACH_MANAGE, PERM_BREACH_VIEW
from app.core.utils import get_request_id
from app.models.breach import Breach, BreachExtensionRequest, BreachNotification
from app.models.entities import User
from app.schemas.breach import (
    BreachAffectedIn,
    BreachAffectedOut,
    BreachAwareIn,
    BreachClocksOut,
    BreachCreateIn,
    BreachDetailOut,
    BreachExtensionDecisionIn,
    BreachExtensionOut,
    BreachExtensionRequestIn,
    BreachFilingRecordIn,
    BreachHashVerifyOut,
    BreachMetricsOut,
    BreachNoticeIssueIn,
    BreachNoticeIssueOut,
    BreachNotificationContentOut,
    BreachNotificationOut,
    BreachOut,
    BreachReportPreviewOut,
    BreachStatusIn,
    BreachUpdateIn,
)
from app.services import breach as breach_service
from app.services.breach import BreachError

router = APIRouter(prefix="/breaches", tags=["breaches"])


def _scoped_query(db: Session, user: User):
    """Org-scoped staff roles (jobhub_admin / codex_admin / skilllearn_admin)
    see only their own tenant's breaches; an unscoped role sees all. Same
    filter every other list endpoint applies - see docs/ARCHITECTURE.md's tenancy note."""
    query = db.query(Breach)
    scope = get_org_scope(user)
    if scope:
        query = query.filter(Breach.source_app == scope)
    return query


def _get_breach(db: Session, user: User, breach_ref: str) -> Breach:
    row = _scoped_query(db, user).filter(Breach.breach_ref == breach_ref).first()
    if row is None:
        # A breach outside the caller's scope is indistinguishable from one
        # that does not exist - the same posture resolve_customer takes.
        raise HTTPException(status_code=404, detail=f"Breach '{breach_ref}' not found")
    return row


def _guard_write_scope(user: User, source_app: str) -> None:
    scope = get_org_scope(user)
    if scope and scope != source_app:
        raise HTTPException(
            status_code=403,
            detail=f"Your role is scoped to {scope} and cannot register a breach for {source_app}",
        )


def _detail(db: Session, row: Breach) -> dict:
    payload = BreachOut.model_validate(row).model_dump()
    payload["clocks"] = breach_service.breach_clocks(db, row)
    payload["timeline"] = breach_service.breach_timeline(db, row)
    payload["outstanding_obligations"] = breach_service.outstanding_obligations(db, row)
    return payload


# ---------------------------------------------------------------------------
# Collection-level routes (declared before /{breach_ref})
# ---------------------------------------------------------------------------

@router.get("/metrics", response_model=BreachMetricsOut)
def metrics(
    source_app: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    """K-39 (time-to-detect / time-to-notify principals), K-40 (Board
    notification timeliness), K-41 (affected principals and notice delivery
    rate), plus the CERT-In 6-hour figure H-12 asks for."""
    scope = get_org_scope(user) or source_app
    return breach_service.breach_metrics(db, source_app=scope)


@router.get("/register")
def register(
    source_app: Optional[str] = Query(None),
    period_start: Optional[datetime] = Query(None),
    period_end: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    """The register extract a s.28 Board request - or
    `app/services/evidence_pack.py` - asks for: every breach in the period
    with its clocks, its filings and their content hashes."""
    scope = get_org_scope(user) or source_app
    return breach_service.breach_register(
        db, source_app=scope, period_start=period_start, period_end=period_end
    )


@router.get("", response_model=list[BreachOut])
def list_breaches(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    query = _scoped_query(db, user)
    if status:
        query = query.filter(Breach.status == status)
    return query.order_by(Breach.detected_at.desc()).all()


@router.post("", response_model=BreachDetailOut, status_code=201)
def create_breach(
    payload: BreachCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    _guard_write_scope(user, payload.source_app)
    try:
        row = breach_service.register_breach(
            db, actor_username=user.username, request_id=get_request_id(),
            **payload.model_dump(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _detail(db, row)


# ---------------------------------------------------------------------------
# One breach
# ---------------------------------------------------------------------------

@router.get("/{breach_ref}", response_model=BreachDetailOut)
def get_breach(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    return _detail(db, _get_breach(db, user, breach_ref))


@router.patch("/{breach_ref}", response_model=BreachDetailOut)
def update_breach(
    breach_ref: str,
    payload: BreachUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    breach_service.update_breach(
        db, row, payload.model_dump(exclude_unset=True),
        actor_username=user.username, request_id=get_request_id(),
    )
    return _detail(db, row)


@router.post("/{breach_ref}/aware", response_model=BreachDetailOut)
def mark_aware(
    breach_ref: str,
    payload: BreachAwareIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    """Record the moment of becoming aware. This starts the CERT-In 6-hour,
    the R.7(1)/(2)(a) "without delay" and the R.7(2)(b) 72-hour clocks."""
    row = _get_breach(db, user, breach_ref)
    try:
        breach_service.mark_aware(
            db, row, aware_at=payload.aware_at,
            actor_username=user.username, request_id=get_request_id(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _detail(db, row)


@router.post("/{breach_ref}/status", response_model=BreachDetailOut)
def change_status(
    breach_ref: str,
    payload: BreachStatusIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    try:
        breach_service.transition_breach(
            db, row, payload.status, note=payload.note,
            actor_username=user.username, request_id=get_request_id(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _detail(db, row)


@router.get("/{breach_ref}/clocks", response_model=BreachClocksOut)
def clocks(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    row = _get_breach(db, user, breach_ref)
    return {
        "breach_ref": row.breach_ref,
        "aware_at": row.aware_at,
        "detected_at": row.detected_at,
        "clocks": breach_service.breach_clocks(db, row),
        "timeline": breach_service.breach_timeline(db, row),
    }


# ---------------------------------------------------------------------------
# Affected-principal scope (R.7(1))
# ---------------------------------------------------------------------------

@router.post("/{breach_ref}/affected", response_model=BreachAffectedOut)
def add_affected(
    breach_ref: str,
    payload: BreachAffectedIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    return breach_service.add_affected_principals(
        db, row, external_ids=payload.external_ids, data_involved=payload.data_involved,
        actor_username=user.username, request_id=get_request_id(),
    )


@router.post("/{breach_ref}/affected/finalise", response_model=BreachDetailOut)
def finalise_affected(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    breach_service.finalise_scope(
        db, row, actor_username=user.username, request_id=get_request_id()
    )
    return _detail(db, row)


# ---------------------------------------------------------------------------
# Report previews (generate without filing)
# ---------------------------------------------------------------------------

def _preview(db: Session, row: Breach, kind: str) -> dict:
    if kind == "board-initial":
        payload = breach_service.board_initial_payload(db, row)
        clauses = breach_service.BOARD_INITIAL_CLAUSES
        title = "INTIMATION OF PERSONAL DATA BREACH TO THE DATA PROTECTION BOARD OF INDIA"
        deadline_at, basis = None, breach_service.BASIS_BOARD_INITIAL
    elif kind == "board-detailed":
        payload = breach_service.board_detailed_payload(db, row)
        clauses = breach_service.BOARD_DETAILED_SECTIONS
        title = "DETAILED REPORT OF PERSONAL DATA BREACH TO THE DATA PROTECTION BOARD OF INDIA"
        deadline_at = breach_service.effective_board_deadline(db, row)
        basis = breach_service.BASIS_BOARD_DETAILED
    else:
        payload = breach_service.cert_in_payload(db, row)
        clauses = breach_service.CERT_IN_SECTIONS
        title = "CYBER INCIDENT REPORT TO CERT-In"
        deadline_at = breach_service.cert_in_deadline(row)
        basis = breach_service.BASIS_CERT_IN
    content = breach_service.render_sections(title, payload, clauses)
    return {
        "breach_ref": row.breach_ref,
        "report_type": payload["report_type"],
        "provision": payload["provision"],
        "payload": payload,
        "content": content,
        "content_hash": breach_service.content_hash(payload),
        "missing_mandated_sections": breach_service.missing_clauses(payload, clauses),
        "deadline_at": deadline_at,
        "deadline_basis": basis,
    }


@router.get("/{breach_ref}/reports/board-initial", response_model=BreachReportPreviewOut)
def preview_board_initial(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_EXPORT)),
):
    return _preview(db, _get_breach(db, user, breach_ref), "board-initial")


@router.get("/{breach_ref}/reports/board-detailed", response_model=BreachReportPreviewOut)
def preview_board_detailed(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_EXPORT)),
):
    """The six-section R.7(2)(b) report. `missing_mandated_sections` is the
    pre-flight check: filing it is refused while that list is non-empty."""
    return _preview(db, _get_breach(db, user, breach_ref), "board-detailed")


@router.get("/{breach_ref}/reports/cert-in", response_model=BreachReportPreviewOut)
def preview_cert_in(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_EXPORT)),
):
    return _preview(db, _get_breach(db, user, breach_ref), "cert-in")


# ---------------------------------------------------------------------------
# Filings
# ---------------------------------------------------------------------------

@router.post("/{breach_ref}/notify/principals", response_model=BreachNoticeIssueOut)
def notify_principals(
    breach_ref: str,
    payload: BreachNoticeIssueIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    """R.7(1): one notice per affected principal, carrying all five mandated
    contents, queued through the shared notification service."""
    row = _get_breach(db, user, breach_ref)
    try:
        return breach_service.notify_principals(
            db, row, language=payload.language,
            actor_username=user.username, request_id=get_request_id(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{breach_ref}/notify/board-initial", response_model=BreachNotificationOut)
def notify_board_initial(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    try:
        return breach_service.file_board_initial(
            db, row, actor_username=user.username, request_id=get_request_id()
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{breach_ref}/notify/board-detailed", response_model=BreachNotificationOut)
def notify_board_detailed(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    try:
        return breach_service.file_board_detailed(
            db, row, actor_username=user.username, request_id=get_request_id()
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{breach_ref}/notify/cert-in", response_model=BreachNotificationOut)
def notify_cert_in(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    try:
        return breach_service.file_cert_in(
            db, row, actor_username=user.username, request_id=get_request_id()
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{breach_ref}/notifications", response_model=list[BreachNotificationOut])
def list_notifications(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    row = _get_breach(db, user, breach_ref)
    breach_service.sync_delivery(db, row)
    return (
        db.query(BreachNotification)
        .filter(BreachNotification.breach_id == row.id)
        .order_by(BreachNotification.id.asc())
        .all()
    )


def _get_notification(db: Session, row: Breach, notification_id: int) -> BreachNotification:
    filing = (
        db.query(BreachNotification)
        .filter(
            BreachNotification.id == notification_id,
            BreachNotification.breach_id == row.id,
        )
        .first()
    )
    if filing is None:
        raise HTTPException(status_code=404, detail="Breach notification not found")
    return filing


@router.get(
    "/{breach_ref}/notifications/{notification_id}",
    response_model=BreachNotificationContentOut,
)
def get_notification(
    breach_ref: str,
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_EXPORT)),
):
    """The filing with its content and payload - `audit.export`, because a
    principal notice quotes that principal's own data."""
    row = _get_breach(db, user, breach_ref)
    breach_service.sync_delivery(db, row)
    return _get_notification(db, row, notification_id)


@router.get(
    "/{breach_ref}/notifications/{notification_id}/verify",
    response_model=BreachHashVerifyOut,
)
def verify_notification(
    breach_ref: str,
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    """Recompute the content hash from the stored payload and compare it with
    the hash recorded when the filing was generated."""
    row = _get_breach(db, user, breach_ref)
    return breach_service.verify_notification_hash(_get_notification(db, row, notification_id))


@router.post(
    "/{breach_ref}/notifications/{notification_id}/record-filing",
    response_model=BreachNotificationOut,
)
def record_filing(
    breach_ref: str,
    notification_id: int,
    payload: BreachFilingRecordIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    """Record an out-of-band regulator filing (the Board's portal, CERT-In's
    incident form) against a generated report."""
    row = _get_breach(db, user, breach_ref)
    filing = _get_notification(db, row, notification_id)
    try:
        return breach_service.record_filing(
            db, row, filing, filing_reference=payload.filing_reference,
            filed_at=payload.filed_at, delivered=payload.delivered,
            actor_username=user.username, request_id=get_request_id(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Extension requests (R.7(2)(b) proviso)
# ---------------------------------------------------------------------------

@router.get("/{breach_ref}/extensions", response_model=list[BreachExtensionOut])
def list_extensions(
    breach_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_VIEW)),
):
    row = _get_breach(db, user, breach_ref)
    return (
        db.query(BreachExtensionRequest)
        .filter(BreachExtensionRequest.breach_id == row.id)
        .order_by(BreachExtensionRequest.id.asc())
        .all()
    )


@router.post("/{breach_ref}/extensions", response_model=BreachExtensionOut, status_code=201)
def create_extension(
    breach_ref: str,
    payload: BreachExtensionRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    """Log the written request to the Board for a longer period under the
    R.7(2)(b) proviso. Requesting does not move the deadline; only the Board
    granting it does."""
    row = _get_breach(db, user, breach_ref)
    try:
        return breach_service.request_extension(
            db, row, requested_until=payload.requested_until, reason=payload.reason,
            written_request_ref=payload.written_request_ref,
            actor_username=user.username, request_id=get_request_id(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/{breach_ref}/extensions/{extension_id}/decision",
    response_model=BreachExtensionOut,
)
def decide_extension(
    breach_ref: str,
    extension_id: int,
    payload: BreachExtensionDecisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_BREACH_MANAGE)),
):
    row = _get_breach(db, user, breach_ref)
    extension = (
        db.query(BreachExtensionRequest)
        .filter(
            BreachExtensionRequest.id == extension_id,
            BreachExtensionRequest.breach_id == row.id,
        )
        .first()
    )
    if extension is None:
        raise HTTPException(status_code=404, detail="Extension request not found")
    try:
        return breach_service.decide_extension(
            db, row, extension, status=payload.status, granted_until=payload.granted_until,
            board_reference=payload.board_reference, decision_note=payload.decision_note,
            actor_username=user.username, request_id=get_request_id(),
        )
    except BreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
