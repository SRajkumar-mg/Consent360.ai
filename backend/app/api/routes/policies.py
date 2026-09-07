from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_POLICY_MANAGE, PERM_POLICY_VIEW
from app.core.utils import get_request_id
from app.models.entities import (
    AuditLog,
    Consent,
    DataCategory,
    Policy,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    User,
)
from app.schemas.reconsent import ChangeClassificationOut
from app.schemas.schemas import (
    PolicyIn,
    PolicyOut,
    PolicyUpdate,
    PolicyVersionOut,
)
from app.services.audit import log_audit
from app.services.material_change import MaterialityError, publish_policy_change
from app.services.tenancy import platform_tenant_id

router = APIRouter(prefix="/policies", tags=["policies"])


def _enrich_rules(rules: list[dict], db: Session) -> list[dict]:
    purposes = {p.code: p.name for p in db.query(Purpose).all()}
    cats = {c.code: c.name for c in db.query(DataCategory).all()}
    acts = {a.code: a.name for a in db.query(ProcessingActivity).all()}
    enriched = []
    for rule in rules or []:
        r = dict(rule)
        r["purpose_name"] = purposes.get(r.get("purpose_code", ""), "")
        r["data_category_name"] = cats.get(r.get("data_category_code", ""), "")
        r["processing_activity_name"] = acts.get(r.get("processing_activity_code", ""), "")
        enriched.append(r)
    return enriched


def _version_out(v: PolicyVersion, db: Session) -> PolicyVersionOut:
    return PolicyVersionOut(
        id=v.id,
        policy_id=v.policy_id,
        version_number=v.version_number,
        rules=_enrich_rules(v.rules, db),
        default_decision=v.default_decision,
        checklist=v.checklist,
        effective_from=v.effective_from,
        effective_to=v.effective_to,
        is_current=v.is_current,
        created_by=v.created_by,
    )


def _policy_out(policy: Policy, db: Session) -> PolicyOut:
    versions = sorted(policy.versions, key=lambda x: x.version_number, reverse=True)
    return PolicyOut(
        id=policy.id,
        name=policy.name,
        code=policy.code,
        description=policy.description,
        status=policy.status,
        current_version=policy.current_version,
        is_active=policy.is_active,
        created_at=policy.created_at,
        versions=[_version_out(v, db) for v in versions],
    )


@router.get("", response_model=list[PolicyOut])
def list_policies(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_POLICY_VIEW))):
    policies = db.query(Policy).order_by(Policy.code).all()
    return [_policy_out(p, db) for p in policies]


@router.get("/{policy_id}", response_model=PolicyOut)
def get_policy(policy_id: int, db: Session = Depends(get_db),
               _: User = Depends(require_permission(PERM_POLICY_VIEW))):
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _policy_out(policy, db)


@router.post("", response_model=PolicyOut, status_code=201)
def create_policy(payload: PolicyIn, db: Session = Depends(get_db),
                  current_user: User = Depends(require_permission(PERM_POLICY_MANAGE))):
    if db.query(Policy).filter(Policy.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Policy code already exists")
    policy = Policy(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        status="ACTIVE",
        current_version=1,
        is_active=True,
        tenant_id=platform_tenant_id(db),
    )
    db.add(policy)
    db.flush()
    pv = PolicyVersion(
        policy_id=policy.id,
        version_number=1,
        rules=[r.model_dump() for r in payload.rules],
        default_decision=payload.default_decision,
        checklist=payload.checklist.model_dump(mode="json") if payload.checklist is not None else None,
        is_current=True,
        created_by=current_user.username,
    )
    db.add(pv)
    db.flush()
    log_audit(db, "POLICY_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", policy_id=policy.id, policy_code=policy.code,
              reason=f"Policy {policy.code} created with version 1")
    db.commit()
    db.refresh(policy)
    return _policy_out(policy, db)


@router.put("/{policy_id}", response_model=PolicyOut)
def update_policy(policy_id: int, payload: PolicyUpdate, db: Session = Depends(get_db),
                  current_user: User = Depends(require_permission(PERM_POLICY_MANAGE))):
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    current = None
    for v in policy.versions:
        if v.is_current:
            current = v
            break
    if current is None:
        raise HTTPException(status_code=500, detail="Policy has no current version")

    rules_changed = payload.rules is not None and payload.rules != current.rules
    default_changed = payload.default_decision is not None and payload.default_decision != current.default_decision
    change_result = None
    if rules_changed or default_changed:
        current.is_current = False
        current.effective_to = datetime.now(timezone.utc)
        new_v = PolicyVersion(
            policy_id=policy.id,
            version_number=current.version_number + 1,
            rules=[r.model_dump() for r in payload.rules] if payload.rules is not None else current.rules,
            default_decision=payload.default_decision if payload.default_decision is not None else current.default_decision,
            # Content changed - never silently carry a prior review forward
            # onto different rules, same rationale as purposes.py's own
            # update_purpose.
            checklist=payload.checklist.model_dump(mode="json") if payload.checklist is not None else None,
            is_current=True,
            created_by=current_user.username,
        )
        db.add(new_v)
        db.flush()
        policy.current_version = new_v.version_number
        # commit=False, and no db.commit() here: this row and the version
        # bump above stay uncommitted until publish_policy_change below
        # either commits them together with its own classification (the
        # COSMETIC/NARROWING/MATERIAL paths) or - on a refused downgrade -
        # nothing commits at all and db.rollback() below discards the whole
        # publication, version bump included. A commit here would already
        # have persisted the new version by the time classify_change had a
        # chance to refuse it, and db.rollback() afterwards would undo
        # nothing.
        log_audit(db, "POLICY_VERSIONED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", policy_id=policy.id, policy_code=policy.code,
                  reason=f"Policy versioned to v{new_v.version_number}",
                  metadata={"new_version": new_v.version_number, "old_version": current.version_number},
                  commit=False)
        # P-02: classify the change, log it whatever it turns out to be, and -
        # if it is material - flag every affected consent and tell those
        # principals. Mirrors routes/purposes.py::update_purpose in spirit,
        # but goes further: nothing above is committed yet, so a downgrade
        # attempt on a field nothing here can classify as cosmetic
        # (POLICY_OVERRIDABLE_FIELDS is empty) rolls back the version bump
        # itself, not just the classification - the publication never half-
        # applies.
        try:
            change_result = publish_policy_change(
                db, policy, current, new_v, actor_username=current_user.username,
                source_app="UI", request_id=get_request_id(),
                cosmetic_overrides=payload.cosmetic_overrides or None,
            )
        except MaterialityError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        if payload.name is not None:
            policy.name = payload.name
        if payload.description is not None:
            policy.description = payload.description
        if payload.status is not None:
            policy.status = payload.status
        if payload.is_active is not None:
            policy.is_active = payload.is_active
        log_audit(db, "POLICY_UPDATED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", policy_id=policy.id, policy_code=policy.code,
                  reason="Policy metadata updated")
        db.commit()
    db.refresh(policy)
    out = _policy_out(policy, db)
    # Echoed back on the publisher's own response, same as PurposeOut.change.
    out.change = ChangeClassificationOut(**change_result) if change_result else None
    return out


@router.delete("/{policy_id}")
def delete_policy(policy_id: int, db: Session = Depends(get_db),
                  current_user: User = Depends(require_permission(PERM_POLICY_MANAGE))):
    """Delete a policy. If any consent record references the policy (or its
    versions) it cannot be hard-deleted - it is retired instead."""
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    version_ids = [v.id for v in policy.versions]
    in_use = (
        db.query(Consent)
        .filter(Consent.policy_id == policy.id)
        .first()
    ) is not None
    if not in_use and version_ids:
        in_use = db.query(Consent).filter(Consent.policy_version_id.in_(version_ids)).first() is not None
    if not in_use:
        in_use = db.query(AuditLog).filter(AuditLog.policy_id == policy.id).first() is not None

    if in_use:
        policy.is_active = False
        policy.status = "RETIRED"
        log_audit(db, "POLICY_RETIRED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", policy_id=policy.id, policy_code=policy.code,
                  reason="Policy retired because consent records reference it")
        db.commit()
        return {"deleted": False, "retired": True,
                "reason": "Policy is referenced by consent records and was retired instead of deleted",
                "policy_id": policy_id}

    # No audit row has ever referenced this policy_id (checked above), so it
    # is genuinely safe to hard-delete. Deliberately omit policy_id on this
    # audit entry (policy_code is kept for traceability): audit_logs is
    # append-only now, so nothing can retroactively null out a reference the
    # way the old code did, and setting policy_id here would immediately
    # dangle once the policy row below is gone, which the FK on
    # audit_logs.policy_id would reject.
    log_audit(db, "POLICY_DELETED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", policy_code=policy.code,
              reason=f"Policy {policy.code} deleted")
    db.query(PolicyVersion).filter(PolicyVersion.policy_id == policy.id).delete(synchronize_session=False)
    db.delete(policy)
    db.commit()
    return {"deleted": True, "policy_id": policy_id}
