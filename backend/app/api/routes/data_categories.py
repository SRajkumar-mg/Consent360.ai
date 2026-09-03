from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_CATEGORY_MANAGE, PERM_PURPOSE_VIEW
from app.models.entities import DataCategory, User
from app.schemas.schemas import DataCategoryIn, DataCategoryOut, DataCategoryUpdate
from app.services.audit import log_audit

router = APIRouter(prefix="/data-categories", tags=["data-categories"])


@router.get("", response_model=list[DataCategoryOut])
def list_categories(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    return db.query(DataCategory).order_by(DataCategory.name).all()


@router.post("", response_model=DataCategoryOut, status_code=201)
def create_category(payload: DataCategoryIn, db: Session = Depends(get_db),
                    current_user: User = Depends(require_permission(PERM_CATEGORY_MANAGE))):
    if db.query(DataCategory).filter(DataCategory.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Data category code already exists")
    dc = DataCategory(name=payload.name, code=payload.code, description=payload.description,
                      is_active=payload.is_active)
    db.add(dc)
    db.flush()
    log_audit(db, "DATA_CATEGORY_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", reason=f"Data category {payload.code} created")
    db.commit()
    db.refresh(dc)
    return dc


@router.put("/{category_id}", response_model=DataCategoryOut)
def update_category(category_id: int, payload: DataCategoryUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(require_permission(PERM_CATEGORY_MANAGE))):
    dc = db.get(DataCategory, category_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Data category not found")
    if payload.name is not None:
        dc.name = payload.name
    if payload.description is not None:
        dc.description = payload.description
    if payload.is_active is not None:
        dc.is_active = payload.is_active
    log_audit(db, "DATA_CATEGORY_UPDATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", reason=f"Data category {dc.code} updated")
    db.commit()
    db.refresh(dc)
    return dc
