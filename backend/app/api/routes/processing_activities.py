from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_ACTIVITY_MANAGE, PERM_PURPOSE_VIEW
from app.models.entities import ProcessingActivity, User
from app.schemas.schemas import ProcessingActivityIn, ProcessingActivityOut, ProcessingActivityUpdate
from app.services.audit import log_audit

router = APIRouter(prefix="/processing-activities", tags=["processing-activities"])


@router.get("", response_model=list[ProcessingActivityOut])
def list_activities(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    return db.query(ProcessingActivity).order_by(ProcessingActivity.name).all()


@router.post("", response_model=ProcessingActivityOut, status_code=201)
def create_activity(payload: ProcessingActivityIn, db: Session = Depends(get_db),
                    current_user: User = Depends(require_permission(PERM_ACTIVITY_MANAGE))):
    if db.query(ProcessingActivity).filter(ProcessingActivity.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Processing activity code already exists")
    pa = ProcessingActivity(name=payload.name, code=payload.code, description=payload.description,
                            is_active=payload.is_active)
    db.add(pa)
    db.flush()
    log_audit(db, "ACTIVITY_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", reason=f"Processing activity {payload.code} created")
    db.commit()
    db.refresh(pa)
    return pa


@router.put("/{activity_id}", response_model=ProcessingActivityOut)
def update_activity(activity_id: int, payload: ProcessingActivityUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(require_permission(PERM_ACTIVITY_MANAGE))):
    pa = db.get(ProcessingActivity, activity_id)
    if not pa:
        raise HTTPException(status_code=404, detail="Processing activity not found")
    if payload.name is not None:
        pa.name = payload.name
    if payload.description is not None:
        pa.description = payload.description
    if payload.is_active is not None:
        pa.is_active = payload.is_active
    log_audit(db, "ACTIVITY_UPDATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", reason=f"Processing activity {pa.code} updated")
    db.commit()
    db.refresh(pa)
    return pa
