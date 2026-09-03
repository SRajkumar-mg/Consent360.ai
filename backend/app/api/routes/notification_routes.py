"""R3-06: Notification management endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_USER_MANAGE
from app.models.entities import Notification, NotificationTemplate, User
from app.services.notification import send_notification, retry_pending_notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationTemplateCreate(BaseModel):
    tenant_id: int
    event_type: str = Field(min_length=1, max_length=64)
    channel: str = Field(pattern=r"^(EMAIL|SMS|IN_APP)$")
    language: str = "en"
    subject: str = ""
    body: str = Field(min_length=1)


class NotificationOut(BaseModel):
    id: int
    tenant_id: int
    event_type: str
    channel: str
    language: str
    reference_type: str
    reference_id: str
    subject: str
    status: str
    retry_count: int
    sent_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime


class NotificationTemplateOut(BaseModel):
    id: int
    tenant_id: int
    event_type: str
    channel: str
    language: str
    subject: str
    body: str
    is_active: bool


@router.get("/templates", response_model=list[NotificationTemplateOut])
def list_templates(
    tenant_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    q = db.query(NotificationTemplate).order_by(NotificationTemplate.id)
    if tenant_id:
        q = q.filter(NotificationTemplate.tenant_id == tenant_id)
    return q.all()


@router.post("/templates", response_model=NotificationTemplateOut, status_code=201)
def create_template(
    payload: NotificationTemplateCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission(PERM_USER_MANAGE)),
):
    template = NotificationTemplate(**payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("/pending", response_model=list[NotificationOut])
def list_pending(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    return db.query(Notification).filter(Notification.status == "PENDING").order_by(Notification.created_at).limit(100).all()


@router.post("/retry")
def retry_notifications(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_USER_MANAGE))):
    retried = retry_pending_notifications(db)
    return {"retried": retried}
