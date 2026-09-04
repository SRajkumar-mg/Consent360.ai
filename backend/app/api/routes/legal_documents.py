"""R4-001: Legal document versioning (Terms & Conditions + Privacy Policy)."""

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
from app.models.entities import LegalDocument, Tenant, User, LEGAL_DOCUMENT_TYPES

router = APIRouter(prefix="/legal-documents", tags=["legal-documents"])


class LegalDocCreate(BaseModel):
    tenant_id: int
    document_type: str = Field(description="TERMS_AND_CONDITIONS or PRIVACY_POLICY")
    version: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=256)
    content: str = ""
    language: str = "en"
    status: str = "DRAFT"
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


class LegalDocUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


class LegalDocOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    document_type: str
    version: str
    title: str
    content: str
    language: str
    content_hash: str
    status: str
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    created_at: object
    created_by: str


def _compute_doc_hash(doc_type: str, title: str, content: str, version: str, lang: str) -> str:
    canonical = json.dumps({
        "document_type": doc_type, "title": title, "content": content,
        "version": version, "language": lang,
    }, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.get("", response_model=list[LegalDocOut], dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def list_legal_documents(
    tenant_id: Optional[int] = Query(default=None),
    document_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    language: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(LegalDocument).order_by(LegalDocument.tenant_id, LegalDocument.document_type, LegalDocument.version.desc())
    if tenant_id:
        q = q.filter(LegalDocument.tenant_id == tenant_id)
    if document_type:
        q = q.filter(LegalDocument.document_type == document_type)
    if status:
        q = q.filter(LegalDocument.status == status)
    if language:
        q = q.filter(LegalDocument.language == language)
    return q.limit(200).all()


@router.post("", response_model=LegalDocOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def create_legal_document(body: LegalDocCreate, db: Session = Depends(get_db)):
    if body.document_type not in LEGAL_DOCUMENT_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid document_type: {body.document_type}. Must be one of {LEGAL_DOCUMENT_TYPES}")

    existing = db.query(LegalDocument).filter(
        LegalDocument.tenant_id == body.tenant_id,
        LegalDocument.document_type == body.document_type,
        LegalDocument.version == body.version,
        LegalDocument.language == body.language,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="A document with this tenant/type/version/language already exists")

    content_hash = _compute_doc_hash(body.document_type, body.title, body.content, body.version, body.language)

    doc = LegalDocument(
        tenant_id=body.tenant_id,
        document_type=body.document_type,
        version=body.version,
        title=body.title,
        content=body.content,
        language=body.language,
        content_hash=content_hash,
        status=body.status,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        created_by="system",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/{doc_id}", response_model=LegalDocOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def get_legal_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(LegalDocument, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found")
    return doc


@router.patch("/{doc_id}", response_model=LegalDocOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def update_legal_document(doc_id: int, body: LegalDocUpdate, db: Session = Depends(get_db)):
    doc = db.get(LegalDocument, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found")
    if doc.status == "PUBLISHED":
        raise HTTPException(status_code=422, detail="Cannot modify a published document. Create a new version instead.")

    for field in ("title", "content", "status", "effective_from", "effective_to"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(doc, field, val)

    doc.content_hash = _compute_doc_hash(doc.document_type, doc.title, doc.content, doc.version, doc.language)
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/{doc_id}/publish", response_model=LegalDocOut, dependencies=[Depends(require_permission(PERM_NOTICE_MANAGE))])
def publish_legal_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(LegalDocument, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found")
    if doc.status == "PUBLISHED":
        raise HTTPException(status_code=422, detail="Already published")

    # Retire previous published of same type/tenant/language
    prev = db.query(LegalDocument).filter(
        LegalDocument.tenant_id == doc.tenant_id,
        LegalDocument.document_type == doc.document_type,
        LegalDocument.language == doc.language,
        LegalDocument.status == "PUBLISHED",
    ).first()
    if prev:
        prev.status = "RETIRED"
        prev.effective_to = datetime.now(timezone.utc)

    doc.status = "PUBLISHED"
    doc.effective_from = datetime.now(timezone.utc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/public/{doc_id}")
def get_legal_document_public(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(LegalDocument, doc_id)
    if not doc or doc.status != "PUBLISHED":
        raise HTTPException(status_code=404, detail="Legal document not found")
    return {
        "document_id": doc.id,
        "document_type": doc.document_type,
        "version": doc.version,
        "title": doc.title,
        "content": doc.content,
        "language": doc.language,
        "content_hash": doc.content_hash,
        "effective_from": doc.effective_from.isoformat() if doc.effective_from else None,
    }
