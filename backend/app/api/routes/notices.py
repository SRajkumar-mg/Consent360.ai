"""R1-04 Notice management (CRUD for notices and notice versions)."""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_NOTICE_MANAGE
from app.models.entities import Notice, NoticeVersion, Purpose, Tenant, User

router = APIRouter(prefix="/notices", tags=["notices"])


class NoticeCreate(BaseModel):
    tenant_id: int
    purpose_id: int


class NoticeVersionCreate(BaseModel):
    notice_id: int
    version_number: int
    language: str = "en"
    title: str = Field(min_length=1, max_length=256)
    body: str
    retention_text: str = ""
    services_enabled: str = ""
    data_items: list[dict] = []
    is_current: bool = True


class NoticeVersionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    notice_id: int
    version_number: int
    language: str
    title: str
    body: str
    retention_text: str = ""
    services_enabled: str = ""
    data_items: list = []
    status: str = "DRAFT"
    effective_from: object = None
    created_at: object


class NoticeOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    purpose_id: int
    versions: list[NoticeVersionOut] = []


@router.get("", response_model=list[NoticeOut], dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def list_notices(db: Session = Depends(get_db)):
    return db.query(Notice).order_by(Notice.id).all()


@router.post("", response_model=NoticeOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def create_notice(body: NoticeCreate, db: Session = Depends(get_db)):
    if not db.get(Purpose, body.purpose_id):
        raise HTTPException(status_code=404, detail="Purpose not found")
    n = Notice(tenant_id=body.tenant_id, purpose_id=body.purpose_id)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@router.post("/versions", response_model=NoticeVersionOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def create_notice_version(body: NoticeVersionCreate, db: Session = Depends(get_db)):
    notice = db.get(Notice, body.notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    tenant = db.get(Tenant, notice.tenant_id)
    current = None
    for v in notice.versions:
        if v.language == body.language and v.status == "PUBLISHED":
            current = v
            break
    if body.is_current and current:
        current.status = "RETIRED"
        current.effective_to = datetime.now(timezone.utc)

    content = body.body or body.title
    content_hash = hashlib.sha256(json.dumps(
        {"tenant": notice.tenant_id, "purpose": notice.purpose_id, "lang": body.language,
         "title": body.title, "body": content, "retention": body.retention_text,
         "services": body.services_enabled, "data_items": body.data_items},
        sort_keys=True).encode()).hexdigest()

    nv = NoticeVersion(
        notice_id=body.notice_id,
        version_number=body.version_number,
        language=body.language,
        title=body.title,
        body=body.body,
        retention_text=body.retention_text,
        services_enabled=body.services_enabled,
        data_items=body.data_items,
        status="PUBLISHED" if body.is_current else "DRAFT",
        effective_from=datetime.now(timezone.utc) if body.is_current else None,
        content_hash=content_hash,
        # Snapshot the tenant's mandatory links + DPO contact onto the version
        withdraw_url=tenant.withdraw_url or "" if tenant else "",
        rights_url=tenant.rights_url or "" if tenant else "",
        board_complaint_url=tenant.board_complaint_url or "" if tenant else "",
        dpo_contact=tenant.dpo_contact or "" if tenant else "",
        published_by="system",
        created_by="system",
    )
    db.add(nv)
    db.commit()
    db.refresh(nv)
    return nv