"""R1-10 (S-05, D-08): the retention schedule, the retention scan, the
sanctioned enforcement path, and the regulator evidence pack.

Permissions, and why these ones:

* Reading the schedule and running the scan need `audit.view` - they are
  compliance reporting, available to every role that can already read the
  audit trail (admin, DPO, auditor, and the operational roles).
* The evidence pack needs `audit.export`: it is a bulk export of the ledger
  and therefore held to the same bar as `GET /audit/export` (admin, DPO,
  auditor only).
* Changing a retention period, or running an enforcement pass that can
  actually destroy records, needs `policy.manage` - admin and DPO. Deciding
  how long personal data is kept is a DPO judgement, not an operational one.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_EXPORT, PERM_AUDIT_VIEW, PERM_POLICY_MANAGE
from app.core.utils import get_request_id
from app.models.entities import Organization, User
from app.api.deps import require_permission
from app.schemas.schemas import (
    EvidencePackOut,
    RetentionEnforceIn,
    RetentionEnforceOut,
    RetentionScanOut,
    RetentionScheduleRow,
    RetentionScheduleUpdateIn,
)
from app.services.evidence_pack import build_evidence_pack
from app.services.retention import (
    RetentionFloorViolation,
    get_schedule,
    retention_scan,
    set_retention_days,
)
from app.services.retention import enforce_retention as _enforce_retention

router = APIRouter(prefix="/retention", tags=["retention"])


@router.get("/schedule", response_model=list[RetentionScheduleRow])
def read_schedule(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    """The retention schedule per record class: the statutory floor, the
    period this deployment has configured, and how (or whether) each class is
    actually enforced."""
    return get_schedule(db)


@router.put("/schedule/{record_class}", response_model=RetentionScheduleRow)
def update_schedule(
    record_class: str,
    payload: RetentionScheduleUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Raise (or restate) a class's retention period. A value below the floor
    in force is refused with 422 - that refusal is the point of the endpoint."""
    try:
        set_retention_days(
            db, record_class, payload.retention_days,
            actor_username=user.username, request_id=get_request_id(),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown record class '{record_class}'")
    except RetentionFloorViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    rows = {r["record_class"]: r for r in get_schedule(db)}
    return rows[record_class]


@router.get("/scan", response_model=RetentionScanOut)
def scan(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    """The DoD's scan: reports zero records deleted before their floor, or
    names every action that broke one."""
    return retention_scan(db, actor_username=user.username, request_id=get_request_id())


@router.post("/enforce", response_model=RetentionEnforceOut)
def enforce(
    payload: RetentionEnforceIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Apply retention to one record class.

    Defaults to `dry_run=true`, which counts and records what would be removed
    without touching a row. A cutoff inside the floor is refused with 422, as
    is a wet run against a class this platform never deletes.
    """
    try:
        return _enforce_retention(
            db, payload.record_class, cutoff=payload.cutoff, dry_run=payload.dry_run,
            actor_username=user.username, request_id=get_request_id(), reason=payload.reason,
        )
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown record class '{payload.record_class}'"
        )
    except RetentionFloorViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/evidence-pack", response_model=EvidencePackOut)
def evidence_pack(
    period_start: Optional[datetime] = Query(default=None),
    period_end: Optional[datetime] = Query(default=None),
    tenant_code: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_EXPORT)),
):
    """The regulator evidence pack for a chosen period.

    Defaults to the last 90 days when no period is given. Read
    `unavailable_sections` first: a section listed there could not be produced
    at all and its absence is not a nil finding (today: the breach register,
    which R3-08 has not built).
    """
    now = datetime.now(timezone.utc)
    end = period_end or now
    start = period_start or (end - timedelta(days=90))

    tenant_id = None
    if tenant_code:
        organization = db.query(Organization).filter(Organization.code == tenant_code).first()
        if not organization:
            raise HTTPException(status_code=404, detail="Unknown tenant_code")
        tenant_id = organization.id

    try:
        return build_evidence_pack(
            db, period_start=start, period_end=end, tenant_id=tenant_id,
            generated_by=user.username, request_id=get_request_id(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
