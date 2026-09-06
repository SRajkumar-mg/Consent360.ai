from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_PURPOSE_MANAGE, PERM_PURPOSE_VIEW
from app.models.entities import (
    DataCategory,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    User,
)
from app.schemas.schemas import (
    DataCategoryOut,
    ProcessingActivityOut,
    PurposeIn,
    PurposeOut,
    PurposeUpdate,
    PurposeVersionCreate,
    PurposeVersionOut,
)
from app.services import consent as consent_service
from app.services.audit import log_audit

router = APIRouter(prefix="/purposes", tags=["purposes"])


def _purpose_out(purpose: Purpose, db: Session) -> PurposeOut:
    pv = None
    for v in purpose.versions:
        if v.is_current:
            pv = v
            break
    data_categories = []
    activities = []
    if pv:
        data_categories = db.query(DataCategory).filter(DataCategory.id.in_(pv.data_category_ids or [])).all()
        activities = (
            db.query(ProcessingActivity)
            .filter(ProcessingActivity.id.in_(pv.processing_activity_ids or []))
            .all()
        )
    return PurposeOut(
        id=purpose.id,
        name=purpose.name,
        code=purpose.code,
        description=purpose.description,
        legal_basis=purpose.legal_basis,
        requires_consent=purpose.requires_consent,
        retention_period_days=purpose.retention_period_days,
        status=purpose.status,
        current_version=purpose.current_version,
        is_active=purpose.is_active,
        created_at=purpose.created_at,
        tenant_id=purpose.tenant_id,
        versions=[PurposeVersionOut.model_validate(v) for v in sorted(purpose.versions, key=lambda x: x.version_number, reverse=True)],
        data_categories=[DataCategoryOut.model_validate(dc) for dc in data_categories],
        processing_activities=[ProcessingActivityOut.model_validate(pa) for pa in activities],
    )


def _apply_version_to_purpose(purpose: Purpose, pv: PurposeVersion) -> None:
    purpose.name = pv.name
    purpose.description = pv.description
    purpose.legal_basis = pv.legal_basis
    purpose.requires_consent = pv.requires_consent
    purpose.retention_period_days = pv.retention_period_days
    purpose.current_version = pv.version_number


@router.get("", response_model=list[PurposeOut])
def list_purposes(
    tenant_id: Optional[int] = Query(None, description="Filter to a single tenant's purposes"),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_PURPOSE_VIEW)),
):
    query = db.query(Purpose)
    if tenant_id is not None:
        query = query.filter(Purpose.tenant_id == tenant_id)
    purposes = query.order_by(Purpose.code).all()
    return [_purpose_out(p, db) for p in purposes]


@router.get("/{purpose_id}", response_model=PurposeOut)
def get_purpose(purpose_id: int, tenant_id: Optional[int] = Query(None),
                db: Session = Depends(get_db),
                _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    purpose = db.get(Purpose, purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    if tenant_id is not None and purpose.tenant_id != tenant_id:
        # Prevents IDOR via purpose_id enumeration across tenants when the
        # caller asserts a tenant_id: treat cross-tenant access as not-found,
        # not 403, to avoid confirming the resource's existence.
        raise HTTPException(status_code=404, detail="Purpose not found")
    return _purpose_out(purpose, db)


@router.post("", response_model=PurposeOut, status_code=201)
def create_purpose(payload: PurposeIn, db: Session = Depends(get_db),
                   current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    if db.query(Purpose).filter(Purpose.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Purpose code already exists")
    purpose = Purpose(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        legal_basis=payload.legal_basis,
        requires_consent=payload.requires_consent,
        retention_period_days=payload.retention_period_days,
        status="ACTIVE",
        current_version=1,
        is_active=True,
        tenant_id=payload.tenant_id,
    )
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id,
        version_number=1,
        name=payload.name,
        description=payload.description,
        legal_basis=payload.legal_basis,
        requires_consent=payload.requires_consent,
        retention_period_days=payload.retention_period_days,
        data_category_ids=payload.data_category_ids,
        processing_activity_ids=payload.processing_activity_ids,
        consent_text=payload.consent_text,
        is_current=True,
        created_by=current_user.username,
    )
    db.add(pv)
    db.flush()
    log_audit(db, "PURPOSE_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Purpose {purpose.code} created with version 1")
    db.commit()
    db.refresh(purpose)
    return _purpose_out(purpose, db)


@router.put("/{purpose_id}", response_model=PurposeOut)
def update_purpose(purpose_id: int, payload: PurposeUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    purpose = db.get(Purpose, purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    pv = consent_service.get_current_purpose_version(purpose)
    versioned_fields_changed = any([
        payload.name is not None and payload.name != pv.name,
        payload.description is not None and payload.description != pv.description,
        payload.legal_basis is not None and payload.legal_basis != pv.legal_basis,
        payload.requires_consent is not None and payload.requires_consent != pv.requires_consent,
        payload.retention_period_days is not None and payload.retention_period_days != pv.retention_period_days,
        payload.data_category_ids is not None and payload.data_category_ids != pv.data_category_ids,
        payload.processing_activity_ids is not None and payload.processing_activity_ids != pv.processing_activity_ids,
        payload.consent_text is not None and payload.consent_text != pv.consent_text,
    ])
    if versioned_fields_changed:
        pv.is_current = False
        pv.effective_to = datetime.now(timezone.utc)
        new_version = pv.version_number + 1
        new_pv = PurposeVersion(
            purpose_id=purpose.id,
            version_number=new_version,
            name=payload.name if payload.name is not None else pv.name,
            description=payload.description if payload.description is not None else pv.description,
            legal_basis=payload.legal_basis if payload.legal_basis is not None else pv.legal_basis,
            requires_consent=payload.requires_consent if payload.requires_consent is not None else pv.requires_consent,
            retention_period_days=payload.retention_period_days if payload.retention_period_days is not None else pv.retention_period_days,
            data_category_ids=payload.data_category_ids if payload.data_category_ids is not None else pv.data_category_ids,
            processing_activity_ids=payload.processing_activity_ids if payload.processing_activity_ids is not None else pv.processing_activity_ids,
            consent_text=payload.consent_text if payload.consent_text is not None else pv.consent_text,
            is_current=True,
            created_by=current_user.username,
        )
        db.add(new_pv)
        db.flush()
        _apply_version_to_purpose(purpose, new_pv)
        log_audit(db, "PURPOSE_UPDATED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
                  reason=f"Purpose versioned to v{new_version}",
                  metadata={"new_version": new_version, "old_version": pv.version_number})
    else:
        if payload.is_active is not None:
            purpose.is_active = payload.is_active
        if payload.status is not None:
            purpose.status = payload.status
        log_audit(db, "PURPOSE_UPDATED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
                  reason="Purpose metadata updated")
    db.commit()
    db.refresh(purpose)
    return _purpose_out(purpose, db)


@router.post("/{purpose_id}/versions", response_model=PurposeVersionOut)
def create_purpose_version(purpose_id: int, payload: PurposeVersionCreate,
                           db: Session = Depends(get_db),
                           current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    purpose = db.get(Purpose, purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    pv = consent_service.get_current_purpose_version(purpose)
    pv.is_current = False
    pv.effective_to = datetime.now(timezone.utc)
    new_pv = PurposeVersion(
        purpose_id=purpose.id,
        version_number=pv.version_number + 1,
        name=pv.name,
        description=pv.description,
        legal_basis=pv.legal_basis,
        requires_consent=pv.requires_consent,
        retention_period_days=pv.retention_period_days,
        data_category_ids=pv.data_category_ids,
        processing_activity_ids=pv.processing_activity_ids,
        consent_text=pv.consent_text,
        is_current=True,
        created_by=current_user.username,
    )
    db.add(new_pv)
    db.flush()
    _apply_version_to_purpose(purpose, new_pv)
    log_audit(db, "PURPOSE_UPDATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Purpose versioned to v{new_pv.version_number} manually: {payload.reason}",
              metadata={"new_version": new_pv.version_number})
    db.commit()
    db.refresh(new_pv)
    return new_pv
