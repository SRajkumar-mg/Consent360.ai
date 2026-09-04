"""R3-12: SDF readiness - algorithm register, DPIA records, data residency."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE
from app.models.entities import AlgorithmRegister, DataResidencyFlag, DPIARecord, User

router = APIRouter(prefix="/sdf", tags=["sdf-readiness"])


class AlgorithmRegisterCreate(BaseModel):
    system_name: str = Field(min_length=1, max_length=128)
    description: str = ""
    risk_level: str = "MEDIUM"
    findings_summary: str = ""


class DPIARecordCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    scope: str = ""
    findings_summary: str = ""
    next_review_date: datetime | None = None


class DataResidencyFlagCreate(BaseModel):
    data_category_code: str = Field(min_length=1, max_length=64)
    requires_localization: bool = False
    country: str = "IN"
    regulation_ref: str = ""
    notes: str = ""


# Algorithm Register
@router.get("/algorithms")
def list_algorithms(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    return db.query(AlgorithmRegister).order_by(AlgorithmRegister.id).all()


@router.post("/algorithms", status_code=201)
def create_algorithm(payload: AlgorithmRegisterCreate, db: Session = Depends(get_db),
                     _: User = Depends(require_permission(PERM_USER_MANAGE))):
    alg = AlgorithmRegister(**payload.model_dump())
    db.add(alg)
    db.commit()
    db.refresh(alg)
    return alg


@router.put("/algorithms/{alg_id}")
def update_algorithm(alg_id: int, payload: AlgorithmRegisterCreate, db: Session = Depends(get_db),
                     _: User = Depends(require_permission(PERM_USER_MANAGE))):
    alg = db.get(AlgorithmRegister, alg_id)
    if not alg:
        raise HTTPException(status_code=404, detail="Algorithm register entry not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(alg, k, v)
    alg.last_reviewed_at = datetime.utcnow()
    db.commit()
    return alg


# DPIA Records
@router.get("/dpia")
def list_dpia(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    return db.query(DPIARecord).order_by(DPIARecord.id).all()


@router.post("/dpia", status_code=201)
def create_dpia(payload: DPIARecordCreate, db: Session = Depends(get_db),
                _: User = Depends(require_permission(PERM_USER_MANAGE))):
    dpia = DPIARecord(**payload.model_dump())
    db.add(dpia)
    db.commit()
    db.refresh(dpia)
    return dpia


# Data Residency Flags
@router.get("/data-residency")
def list_data_residency(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    return db.query(DataResidencyFlag).order_by(DataResidencyFlag.id).all()


@router.post("/data-residency", status_code=201)
def create_data_residency_flag(payload: DataResidencyFlagCreate, db: Session = Depends(get_db),
                                _: User = Depends(require_permission(PERM_USER_MANAGE))):
    existing = db.query(DataResidencyFlag).filter(
        DataResidencyFlag.data_category_code == payload.data_category_code
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Data residency flag already exists for this category")
    flag = DataResidencyFlag(**payload.model_dump())
    db.add(flag)
    db.commit()
    db.refresh(flag)
    return flag
