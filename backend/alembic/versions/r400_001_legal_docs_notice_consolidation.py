"""R4-001: Legal documents, notice consolidation, evidence extension

Revision ID: r400_001
Revises: r312_003
Create Date: 2026-09-04
"""

import hashlib
import json as _json
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "r400_001"
down_revision = "r312_003"
branch_labels = None
depends_on = None


def _compute_content_hash(row_data: dict) -> str:
    canonical = _json.dumps(row_data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Create legal_documents table ──
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("document_type", sa.String(32), nullable=False, index=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), default=""),
        sa.Column("language", sa.String(8), default="en", index=True),
        sa.Column("content_hash", sa.String(64), default=""),
        sa.Column("status", sa.String(32), default="DRAFT", index=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), default="system"),
    )

    # ── 2. Extend notices table (consolidate notice_version fields into notice) ──
    op.add_column("notices", sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("notices", sa.Column("language", sa.String(8), nullable=False, server_default="en", index=True))
    op.add_column("notices", sa.Column("title", sa.String(256), nullable=False, server_default=""))
    op.add_column("notices", sa.Column("body", sa.Text(), default=""))
    op.add_column("notices", sa.Column("data_items", sa.JSON(), default=list))
    op.add_column("notices", sa.Column("services_enabled", sa.Text(), default=""))
    op.add_column("notices", sa.Column("retention_text", sa.Text(), default=""))
    op.add_column("notices", sa.Column("consent_text", sa.Text(), default=""))
    op.add_column("notices", sa.Column("withdraw_url", sa.String(512), default=""))
    op.add_column("notices", sa.Column("rights_url", sa.String(512), default=""))
    op.add_column("notices", sa.Column("grievance_url", sa.String(512), default=""))
    op.add_column("notices", sa.Column("board_complaint_url", sa.String(512), default=""))
    op.add_column("notices", sa.Column("dpo_name", sa.String(256), default=""))
    op.add_column("notices", sa.Column("dpo_contact", sa.Text(), default=""))
    op.add_column("notices", sa.Column("terms_document_id", sa.Integer(), sa.ForeignKey("legal_documents.id"), nullable=True))
    op.add_column("notices", sa.Column("privacy_document_id", sa.Integer(), sa.ForeignKey("legal_documents.id"), nullable=True))
    op.add_column("notices", sa.Column("content_hash", sa.String(64), default=""))
    op.add_column("notices", sa.Column("status", sa.String(32), default="DRAFT", index=True))
    op.add_column("notices", sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notices", sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notices", sa.Column("published_by", sa.String(64), default="system"))
    op.add_column("notices", sa.Column("created_by", sa.String(64), default="system"))

    # ── 3. Migrate data from notice_versions into notices ──
    rows = conn.execute(text(
        "SELECT id, notice_id, version_number, language, title, body, data_items, "
        "services_enabled, retention_text, withdraw_url, rights_url, board_complaint_url, "
        "dpo_contact, content_hash, status, effective_from, effective_to, published_by, "
        "created_at, created_by FROM notice_versions ORDER BY id"
    )).fetchall()

    for r in rows:
        (nv_id, notice_id, ver, lang, title, body, data_items, svc, retention,
         withdraw, rights, board, dpo, chash, status, eff_from, eff_to,
         pub_by, created_at, created_by) = r
        # Update the parent notice row with this version's data
        conn.execute(text(
            "UPDATE notices SET version_number=:ver, language=:lang, title=:title, body=:body, "
            "data_items=:data_items, services_enabled=:svc, retention_text=:ret, "
            "withdraw_url=:withdraw, rights_url=:rights, board_complaint_url=:board, "
            "dpo_contact=:dpo, content_hash=:chash, status=:status, "
            "effective_from=:eff_from, effective_to=:eff_to, published_by=:pub_by, "
            "created_at=:created_at, created_by=:created_by "
            "WHERE id=:notice_id"
        ), {
            "ver": ver, "lang": lang, "title": title or "", "body": body or "",
            "data_items": _json.dumps(data_items) if data_items else "[]",
            "svc": svc or "", "ret": retention or "", "withdraw": withdraw or "",
            "rights": rights or "", "board": board or "", "dpo": dpo or "",
            "chash": chash or "", "status": status or "DRAFT",
            "eff_from": eff_from, "eff_to": eff_to, "pub_by": pub_by or "system",
            "created_at": created_at, "created_by": created_by or "system",
            "notice_id": notice_id,
        })

    # ── 4. Add new FK columns to consent_evidence ──
    op.add_column("consent_evidence", sa.Column("notice_id", sa.Integer(), sa.ForeignKey("notices.id"), nullable=True))
    op.add_column("consent_evidence", sa.Column("terms_document_id", sa.Integer(), sa.ForeignKey("legal_documents.id"), nullable=True))
    op.add_column("consent_evidence", sa.Column("privacy_document_id", sa.Integer(), sa.ForeignKey("legal_documents.id"), nullable=True))
    op.add_column("consent_evidence", sa.Column("terms_hash", sa.String(64), default=""))
    op.add_column("consent_evidence", sa.Column("privacy_hash", sa.String(64), default=""))

    # ── 5. Add new FK columns to consents ──
    op.add_column("consents", sa.Column("notice_id", sa.Integer(), sa.ForeignKey("notices.id"), nullable=True))
    op.add_column("consents", sa.Column("terms_document_id", sa.Integer(), sa.ForeignKey("legal_documents.id"), nullable=True))
    op.add_column("consents", sa.Column("privacy_document_id", sa.Integer(), sa.ForeignKey("legal_documents.id"), nullable=True))
    op.add_column("consents", sa.Column("terms_hash", sa.String(64), default=""))
    op.add_column("consents", sa.Column("privacy_hash", sa.String(64), default=""))

    # ── 6. Add notice_id to consent_receipts ──
    op.add_column("consent_receipts", sa.Column("notice_id", sa.Integer(), sa.ForeignKey("notices.id"), nullable=True))

    # ── 7. Backfill consent_evidence.notice_id from notice_version_id ──
    conn.execute(text("""
        UPDATE consent_evidence ce
        SET notice_id = nv.notice_id
        FROM notice_versions nv
        WHERE ce.notice_version_id = nv.id AND ce.notice_id IS NULL
    """))

    # ── 8. Backfill consents.notice_id from notice_version_id ──
    conn.execute(text("""
        UPDATE consents c
        SET notice_id = nv.notice_id
        FROM notice_versions nv
        WHERE c.notice_version_id = nv.id AND c.notice_id IS NULL
    """))

    # ── 9. Backfill consent_receipts.notice_id from notice_version_id ──
    conn.execute(text("""
        UPDATE consent_receipts cr
        SET notice_id = nv.notice_id
        FROM notice_versions nv
        WHERE cr.notice_version_id = nv.id AND cr.notice_id IS NULL
    """))

    # ── 10. Add consent_text from purpose_versions into notices if empty ──
    conn.execute(text("""
        UPDATE notices n
        SET consent_text = pv.consent_text
        FROM purpose_versions pv
        WHERE n.purpose_id = pv.purpose_id
        AND pv.is_current = true
        AND (n.consent_text IS NULL OR n.consent_text = '')
    """))

    # ── 11. Add grievance_url from tenants into notices if empty ──
    conn.execute(text("""
        UPDATE notices n
        SET grievance_url = t.grievance_url, dpo_name = t.dpo_name
        FROM tenants t
        WHERE n.tenant_id = t.id
        AND (n.grievance_url IS NULL OR n.grievance_url = '')
    """))


def downgrade() -> None:
    op.drop_column("consent_receipts", "notice_id")
    op.drop_column("consents", "privacy_hash")
    op.drop_column("consents", "terms_hash")
    op.drop_column("consents", "privacy_document_id")
    op.drop_column("consents", "terms_document_id")
    op.drop_column("consents", "notice_id")
    op.drop_column("consent_evidence", "privacy_hash")
    op.drop_column("consent_evidence", "terms_hash")
    op.drop_column("consent_evidence", "privacy_document_id")
    op.drop_column("consent_evidence", "terms_document_id")
    op.drop_column("consent_evidence", "notice_id")

    for col in [
        "version_number", "language", "title", "body", "data_items",
        "services_enabled", "retention_text", "consent_text", "withdraw_url",
        "rights_url", "grievance_url", "board_complaint_url", "dpo_name",
        "dpo_contact", "terms_document_id", "privacy_document_id",
        "content_hash", "status", "effective_from", "effective_to",
        "published_by", "created_by",
    ]:
        op.drop_column("notices", col)

    op.drop_table("legal_documents")
