from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_VIEW
from app.models.entities import BannerEvent, User
from app.services.audit import log_audit

router = APIRouter(prefix="/banner-events", tags=["banner-events"])

BANNER_EVENT_TYPES = ["NOTICE_SHOWN", "ACCEPT_ALL", "REJECT_ALL", "GRANULAR_DECISION"]


class BannerEventIn(BaseModel):
    tenant_id: int = 1
    customer_id: int | None = None
    session_id: str = ""
    purpose_id: int | None = None
    event_type: str
    language: str = "en"
    banner_version: str = "1.0"
    control_id: str = ""
    notice_version: str = ""


class BannerEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int
    customer_id: int | None = None
    session_id: str
    purpose_id: int | None = None
    event_type: str
    language: str
    banner_version: str
    control_id: str
    notice_version: str
    occurred_at: datetime
    created_at: datetime


@router.post("", response_model=BannerEventOut)
def ingest_banner_event(
    payload: BannerEventIn,
    db: Session = Depends(get_db),
):
    if payload.event_type not in BANNER_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid event_type. Must be one of {BANNER_EVENT_TYPES}")
    now = datetime.now(timezone.utc)
    event = BannerEvent(
        tenant_id=payload.tenant_id,
        customer_id=payload.customer_id,
        session_id=payload.session_id,
        purpose_id=payload.purpose_id,
        event_type=payload.event_type,
        language=payload.language,
        banner_version=payload.banner_version,
        control_id=payload.control_id,
        notice_version=payload.notice_version,
        occurred_at=now,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("", response_model=list[BannerEventOut])
def list_banner_events(
    event_type: str | None = Query(default=None),
    tenant_id: int | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    q = db.query(BannerEvent).order_by(BannerEvent.created_at.desc())
    if event_type:
        q = q.filter(BannerEvent.event_type == event_type.upper())
    if tenant_id is not None:
        q = q.filter(BannerEvent.tenant_id == tenant_id)
    if session_id:
        q = q.filter(BannerEvent.session_id == session_id)
    return q.limit(limit).all()
