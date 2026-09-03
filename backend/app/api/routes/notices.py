from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE
from app.models.entities import Notice, Purpose, TenantSettings, User
from app.services.audit import log_audit

router = APIRouter(prefix="/notices", tags=["notices"])
public_router = APIRouter(prefix="/public", tags=["notices"])

DEFAULT_TENANT_ID = 1


class NoticeIn(BaseModel):
    tenant_id: int = DEFAULT_TENANT_ID
    purpose_id: int | None = None
    title: str = ""
    body: str = ""
    language: str = "en"
    status: str = "DRAFT"
    data_items: list = []
    services_enabled: list = []
    retention_text: str = ""


class NoticeUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    language: str | None = None
    status: str | None = None
    purpose_id: int | None = None
    data_items: list | None = None
    services_enabled: list | None = None
    retention_text: str | None = None


class NoticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: int | None = None
    purpose_id: int | None = None
    title: str
    body: str
    language: str
    status: str
    version: int
    data_items: list
    services_enabled: list
    retention_text: str
    checklist_passed: bool
    checklist_reviewer: str
    checklist_at: datetime | None = None
    published_at: datetime | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class NoticePreviewOut(BaseModel):
    id: int
    title: str
    body: str
    language: str
    version: int
    data_items: list
    services_enabled: list
    retention_text: str


def _notice_out(n: Notice) -> NoticeOut:
    return NoticeOut(
        id=n.id, tenant_id=n.tenant_id, purpose_id=n.purpose_id,
        title=n.title, body=n.body, language=n.language,
        status=n.status, version=n.version,
        data_items=n.data_items or [], services_enabled=n.services_enabled or [],
        retention_text=n.retention_text,
        checklist_passed=n.checklist_passed, checklist_reviewer=n.checklist_reviewer,
        checklist_at=n.checklist_at, published_at=n.published_at,
        created_by=n.created_by, created_at=n.created_at, updated_at=n.updated_at,
    )


@router.get("", response_model=list[NoticeOut])
def list_notices(
    status: str | None = Query(default=None),
    language: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    q = db.query(Notice).order_by(Notice.created_at.desc())
    if status:
        q = q.filter(Notice.status == status.upper())
    if language:
        q = q.filter(Notice.language == language)
    return [_notice_out(n) for n in q.limit(200).all()]


@router.get("/{notice_id}", response_model=NoticeOut)
def get_notice(notice_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    return _notice_out(notice)


@router.post("", response_model=NoticeOut)
def create_notice(
    payload: NoticeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    notice = Notice(**payload.model_dump(), created_by=user.username)
    if payload.purpose_id:
        purpose = db.get(Purpose, payload.purpose_id)
        if not purpose:
            raise HTTPException(status_code=400, detail="Purpose not found")
    if payload.tenant_id:
        tenant = db.get(TenantSettings, payload.tenant_id)
        if not tenant:
            raise HTTPException(status_code=400, detail="Tenant not found")
    db.add(notice)
    log_audit(db, "NOTICE_CREATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              reason="Notice created")
    db.commit()
    db.refresh(notice)
    return _notice_out(notice)


@router.put("/{notice_id}", response_model=NoticeOut)
def update_notice(
    notice_id: int,
    payload: NoticeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(notice, field, value)
    notice.version += 1
    log_audit(db, "NOTICE_UPDATED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              reason=f"Notice {notice_id} updated")
    db.commit()
    db.refresh(notice)
    return _notice_out(notice)


@router.post("/{notice_id}/publish", response_model=NoticeOut)
def publish_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    now = datetime.now(timezone.utc)
    notice.status = "PUBLISHED"
    notice.published_at = now
    notice.checklist_passed = True
    notice.checklist_reviewer = user.username
    notice.checklist_at = now
    notice.version += 1
    log_audit(db, "NOTICE_PUBLISHED", actor_username=user.username,
              actor_role=user.role.name if user.role else "", source_app="UI",
              reason=f"Notice {notice_id} published")
    db.commit()
    db.refresh(notice)
    return _notice_out(notice)


@router.get("/{notice_id}/preview", response_model=NoticePreviewOut)
def preview_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    return NoticePreviewOut(
        id=notice.id, title=notice.title, body=notice.body,
        language=notice.language, version=notice.version,
        data_items=notice.data_items or [],
        services_enabled=notice.services_enabled or [],
        retention_text=notice.retention_text,
    )


@public_router.get("/{tenant_code}/notices/{purpose_code}")
def get_public_notice(
    tenant_code: str,
    purpose_code: str,
    lang: str = Query(default="en"),
    db: Session = Depends(get_db),
):
    tenant = db.query(TenantSettings).filter(TenantSettings.tenant_code == tenant_code).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    purpose = db.query(Purpose).filter(Purpose.code == purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    notice = (
        db.query(Notice)
        .filter(
            Notice.tenant_id == tenant.id,
            Notice.purpose_id == purpose.id,
            Notice.status == "PUBLISHED",
            Notice.language == lang,
        )
        .order_by(Notice.version.desc())
        .first()
    )
    if not notice:
        notice = (
            db.query(Notice)
            .filter(
                Notice.tenant_id == tenant.id,
                Notice.purpose_id == purpose.id,
                Notice.status == "PUBLISHED",
            )
            .order_by(Notice.version.desc())
            .first()
        )
    if not notice:
        raise HTTPException(status_code=404, detail="No published notice found")
    log_audit(db, "CONSENT_VIEWED", actor_username="anonymous", source_app="PUBLIC",
              purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Public notice viewed for {purpose_code}")
    return {
        "id": notice.id,
        "title": notice.title,
        "body": notice.body,
        "language": notice.language,
        "version": notice.version,
        "data_items": notice.data_items or [],
        "services_enabled": notice.services_enabled or [],
        "retention_text": notice.retention_text,
        "purpose_code": purpose.code,
        "purpose_name": purpose.name,
    }
