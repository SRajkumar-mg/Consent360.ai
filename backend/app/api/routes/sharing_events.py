"""R1-08 — data-sharing events (track and record consent-linked sharing actions)."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_INTEGRATION
from app.models.entities import Consent, DataSharingEvent, User

router = APIRouter(prefix="/sharing-events", tags=["sharing-events"])


class SharingEventCreate(BaseModel):
    consent_id: int
    processor_id: int
    purpose_id: int
    data_category_ids: list[int] = []
    event_type: str = "REQUESTED"


class SharingEventOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    consent_id: int
    processor_id: int
    purpose_id: int
    data_category_ids: list
    event_type: str
    occurred_at: object
    hash: str


@router.post("", response_model=SharingEventOut, dependencies=[Depends(require_permission(PERM_INTEGRATION))])
def record_sharing_event(body: SharingEventCreate, db: Session = Depends(get_db)):
    import hashlib
    consent = db.get(Consent, body.consent_id)
    tenant_id = consent.tenant_id if consent else 1
    hash_input = f"{body.consent_id}:{body.processor_id}:{body.purpose_id}:{body.event_type}"
    event = DataSharingEvent(
        consent_id=body.consent_id,
        tenant_id=tenant_id or 1,
        processor_id=body.processor_id,
        purpose_id=body.purpose_id,
        data_category_ids=body.data_category_ids,
        event_type=body.event_type,
        hash=hashlib.sha256(hash_input.encode()).hexdigest(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("", response_model=list[SharingEventOut], dependencies=[Depends(require_permission(PERM_INTEGRATION))])
def list_sharing_events(
    consent_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(DataSharingEvent).order_by(DataSharingEvent.occurred_at.desc())
    if consent_id:
        q = q.filter(DataSharingEvent.consent_id == consent_id)
    return q.limit(200).all()