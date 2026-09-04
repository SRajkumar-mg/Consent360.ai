"""R1-04/R4-001: Notice management, rendering, legal documents."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_NOTICE_MANAGE
from app.core.security import sign_payload
from app.models.entities import (
    LegalDocument, Notice, Purpose, Tenant, User, SUPPORTED_LANGUAGES,
)

router = APIRouter(prefix="/notices", tags=["notices"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class NoticeCreate(BaseModel):
    tenant_id: int
    purpose_id: int
    version_number: int = 1
    language: str = "en"
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    data_items: list[dict] = []
    services_enabled: str = ""
    retention_text: str = ""
    consent_text: str = ""
    terms_document_id: Optional[int] = None
    privacy_document_id: Optional[int] = None
    status: str = "DRAFT"
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


class NoticeVersionCreate(BaseModel):
    notice_id: int
    version_number: int
    language: str = "en"
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    retention_text: str = ""
    services_enabled: str = ""
    data_items: list[dict] = []
    consent_text: str = ""
    terms_document_id: Optional[int] = None
    privacy_document_id: Optional[int] = None
    is_current: bool = True


class NoticeOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    purpose_id: int
    version_number: Optional[int] = 1
    language: Optional[str] = "en"
    title: Optional[str] = ""
    body: Optional[str] = ""
    data_items: Optional[list] = []
    services_enabled: Optional[str] = ""
    retention_text: Optional[str] = ""
    consent_text: Optional[str] = ""
    withdraw_url: Optional[str] = ""
    rights_url: Optional[str] = ""
    grievance_url: Optional[str] = ""
    board_complaint_url: Optional[str] = ""
    dpo_name: Optional[str] = ""
    dpo_contact: Optional[str] = ""
    terms_document_id: Optional[int] = None
    privacy_document_id: Optional[int] = None
    content_hash: Optional[str] = ""
    status: Optional[str] = "DRAFT"
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    published_by: Optional[str] = "system"
    created_at: Optional[datetime] = None
    created_by: Optional[str] = "system"


class NoticeRenderOut(BaseModel):
    notice_id: int
    version_number: int
    language: str
    title: str
    body: str
    data_items: list = []
    services_enabled: str = ""
    retention_text: str = ""
    consent_text: str = ""
    content_hash: str = ""
    purpose_id: int
    purpose_name: str = ""
    purpose_code: str = ""
    tenant_id: int
    tenant_code: str = ""
    legal_documents: dict = {}
    tenant_contact: dict = {}
    links: dict = {}
    supported_languages: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_notice_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_tenant(tenant: Tenant, notice: Notice) -> dict:
    return {
        "withdraw_url": tenant.withdraw_url or notice.withdraw_url or "",
        "rights_url": tenant.rights_url or notice.rights_url or "",
        "grievance_url": tenant.grievance_url or notice.grievance_url or "",
        "board_complaint_url": tenant.board_complaint_url or notice.board_complaint_url or "",
        "dpo_name": tenant.dpo_name or notice.dpo_name or "",
        "dpo_contact": tenant.dpo_contact or notice.dpo_contact or "",
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[NoticeOut], dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def list_notices(
    tenant_id: Optional[int] = Query(default=None),
    purpose_id: Optional[int] = Query(default=None),
    language: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Notice).order_by(Notice.tenant_id, Notice.purpose_id, Notice.version_number.desc())
    if tenant_id:
        q = q.filter(Notice.tenant_id == tenant_id)
    if purpose_id:
        q = q.filter(Notice.purpose_id == purpose_id)
    if language:
        q = q.filter(Notice.language == language)
    if status:
        q = q.filter(Notice.status == status)
    return q.limit(500).all()


@router.post("", response_model=NoticeOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def create_notice(body: NoticeCreate, db: Session = Depends(get_db)):
    if not db.get(Purpose, body.purpose_id):
        raise HTTPException(status_code=404, detail="Purpose not found")
    if body.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=422, detail=f"Unsupported language: {body.language}")

    if body.terms_document_id:
        td = db.get(LegalDocument, body.terms_document_id)
        if not td or td.document_type != "TERMS_AND_CONDITIONS":
            raise HTTPException(status_code=422, detail="Invalid terms document")
    if body.privacy_document_id:
        pd = db.get(LegalDocument, body.privacy_document_id)
        if not pd or pd.document_type != "PRIVACY_POLICY":
            raise HTTPException(status_code=422, detail="Invalid privacy document")

    tenant = db.get(Tenant, body.tenant_id)
    snapshot = _snapshot_tenant(tenant, Notice()) if tenant else {}

    content_hash = _compute_notice_hash({
        "tenant_id": body.tenant_id, "purpose_id": body.purpose_id,
        "language": body.language, "title": body.title, "body": body.body,
        "retention": body.retention_text, "services": body.services_enabled,
        "data_items": body.data_items, "consent_text": body.consent_text,
    })

    n = Notice(
        tenant_id=body.tenant_id,
        purpose_id=body.purpose_id,
        version_number=body.version_number,
        language=body.language,
        title=body.title,
        body=body.body,
        data_items=body.data_items,
        services_enabled=body.services_enabled,
        retention_text=body.retention_text,
        consent_text=body.consent_text,
        terms_document_id=body.terms_document_id,
        privacy_document_id=body.privacy_document_id,
        content_hash=content_hash,
        status=body.status,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        withdraw_url=snapshot.get("withdraw_url", ""),
        rights_url=snapshot.get("rights_url", ""),
        grievance_url=snapshot.get("grievance_url", ""),
        board_complaint_url=snapshot.get("board_complaint_url", ""),
        dpo_name=snapshot.get("dpo_name", ""),
        dpo_contact=snapshot.get("dpo_contact", ""),
        published_by="system",
        created_by="system",
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@router.post("/versions", response_model=NoticeOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def create_notice_version(body: NoticeVersionCreate, db: Session = Depends(get_db)):
    parent = db.query(Notice).filter(Notice.id == body.notice_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Notice not found")

    tenant = db.get(Tenant, parent.tenant_id)

    # Retire current PUBLISHED for same language
    current = db.query(Notice).filter(
        Notice.tenant_id == parent.tenant_id,
        Notice.purpose_id == parent.purpose_id,
        Notice.language == body.language,
        Notice.status == "PUBLISHED",
    ).first()
    if body.is_current and current:
        current.status = "RETIRED"
        current.effective_to = datetime.now(timezone.utc)

    snapshot = _snapshot_tenant(tenant, parent) if tenant else {}
    content_hash = _compute_notice_hash({
        "tenant_id": parent.tenant_id, "purpose_id": parent.purpose_id,
        "language": body.language, "title": body.title, "body": body.body,
        "retention": body.retention_text, "services": body.services_enabled,
        "data_items": body.data_items, "consent_text": body.consent_text or parent.consent_text,
    })

    nv = Notice(
        tenant_id=parent.tenant_id,
        purpose_id=parent.purpose_id,
        version_number=body.version_number,
        language=body.language,
        title=body.title,
        body=body.body,
        data_items=body.data_items,
        services_enabled=body.services_enabled,
        retention_text=body.retention_text,
        consent_text=body.consent_text or parent.consent_text,
        terms_document_id=body.terms_document_id or parent.terms_document_id,
        privacy_document_id=body.privacy_document_id or parent.privacy_document_id,
        content_hash=content_hash,
        status="PUBLISHED" if body.is_current else "DRAFT",
        effective_from=datetime.now(timezone.utc) if body.is_current else None,
        withdraw_url=snapshot.get("withdraw_url", ""),
        rights_url=snapshot.get("rights_url", ""),
        grievance_url=snapshot.get("grievance_url", ""),
        board_complaint_url=snapshot.get("board_complaint_url", ""),
        dpo_name=snapshot.get("dpo_name", ""),
        dpo_contact=snapshot.get("dpo_contact", ""),
        published_by="system",
        created_by="system",
    )
    db.add(nv)
    db.commit()
    db.refresh(nv)
    return nv


# ---------------------------------------------------------------------------
# Notice rendering endpoint
# ---------------------------------------------------------------------------

@router.get("/render/{notice_id}", response_model=NoticeRenderOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def render_notice(
    notice_id: int,
    language: str = Query(default="en"),
    db: Session = Depends(get_db),
):
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    target = notice
    if language != notice.language:
        target = db.query(Notice).filter(
            Notice.tenant_id == notice.tenant_id,
            Notice.purpose_id == notice.purpose_id,
            Notice.language == language,
            Notice.status == "PUBLISHED",
        ).first() or notice

    tenant = db.get(Tenant, target.tenant_id)
    purpose = db.get(Purpose, target.purpose_id)

    terms_info = {}
    if target.terms_document_id:
        td = db.get(LegalDocument, target.terms_document_id)
        if td:
            terms_info = {"document_id": td.id, "version": td.version, "title": td.title, "content_hash": td.content_hash}

    privacy_info = {}
    if target.privacy_document_id:
        pd = db.get(LegalDocument, target.privacy_document_id)
        if pd:
            privacy_info = {"document_id": pd.id, "version": pd.version, "title": pd.title, "content_hash": pd.content_hash}

    tenant_contact = {}
    links = {}
    if tenant:
        tenant_contact = {"dpo_name": tenant.dpo_name, "dpo_contact": tenant.dpo_contact or ""}
        links = {
            "withdraw": target.withdraw_url or tenant.withdraw_url or "",
            "rights": target.rights_url or tenant.rights_url or "",
            "grievance": target.grievance_url or tenant.grievance_url or "",
            "board_complaint": target.board_complaint_url or tenant.board_complaint_url or "",
        }

    return NoticeRenderOut(
        notice_id=target.id,
        version_number=target.version_number,
        language=target.language,
        title=target.title,
        body=target.body,
        data_items=target.data_items or [],
        services_enabled=target.services_enabled or "",
        retention_text=target.retention_text or "",
        consent_text=target.consent_text or "",
        content_hash=target.content_hash,
        purpose_id=target.purpose_id,
        purpose_name=purpose.name if purpose else "",
        purpose_code=purpose.code if purpose else "",
        tenant_id=target.tenant_id,
        tenant_code=tenant.code if tenant else "",
        legal_documents={"terms_and_conditions": terms_info, "privacy_policy": privacy_info},
        tenant_contact=tenant_contact,
        links=links,
        supported_languages=SUPPORTED_LANGUAGES,
    )


# ---------------------------------------------------------------------------
# Public notice endpoint (no auth required, for portal rendering)
# ---------------------------------------------------------------------------

@router.get("/public/{notice_id}", response_model=NoticeRenderOut)
def render_notice_public(
    notice_id: int,
    language: str = Query(default="en"),
    db: Session = Depends(get_db),
):
    """Public endpoint for consent portal banner rendering."""
    return render_notice(notice_id, language, db)
