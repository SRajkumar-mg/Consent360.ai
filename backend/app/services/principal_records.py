"""R1-11 (D-06, CM-04): builds the "complete consent record" a verified
principal can view (GET /portal/history) or download (GET /portal/export)
in JSON, CSV or PDF - everything recorded about them for the one
source_app their consent-context token belongs to: consents, the lifecycle
history behind them, the evidence proving how each grant/withdrawal was
collected, and every receipt issued to them.

Kept as a single builder shared by the JSON view and every export format so
"what a principal can see" and "what a principal can download" never drift
apart.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.entities import (
    Consent,
    ConsentEvidence,
    ConsentHistory,
    ConsentReceipt,
    Customer,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PrincipalRecord:
    customer: Customer
    source_app: str
    generated_at: datetime
    consents: list[Consent] = field(default_factory=list)
    history: list[ConsentHistory] = field(default_factory=list)
    evidence: list[ConsentEvidence] = field(default_factory=list)
    receipts: list[ConsentReceipt] = field(default_factory=list)


def build_principal_record(db: Session, customer: Customer, source_app: str) -> PrincipalRecord:
    consents = (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.source_app == source_app)
        .order_by(Consent.purpose_id.asc(), Consent.data_category_id.asc(), Consent.processing_activity_id.asc())
        .all()
    )
    consent_ids = [c.id for c in consents]
    history = (
        db.query(ConsentHistory)
        .filter(ConsentHistory.consent_id.in_(consent_ids))
        .order_by(ConsentHistory.created_at.asc())
        .all()
        if consent_ids
        else []
    )
    evidence = (
        db.query(ConsentEvidence)
        .filter(ConsentEvidence.consent_id.in_(consent_ids))
        .order_by(ConsentEvidence.collected_at.asc())
        .all()
        if consent_ids
        else []
    )
    receipts = (
        db.query(ConsentReceipt)
        .filter(ConsentReceipt.customer_id == customer.id, ConsentReceipt.source_app == source_app)
        .order_by(ConsentReceipt.issued_at.asc())
        .all()
    )
    return PrincipalRecord(
        customer=customer, source_app=source_app, generated_at=utcnow(),
        consents=consents, history=history, evidence=evidence, receipts=receipts,
    )


def _consent_row(c: Consent) -> dict:
    return {
        "id": c.id,
        "purpose_code": c.purpose.code if c.purpose else "",
        "purpose_name": c.purpose.name if c.purpose else "",
        "data_category": c.data_category.name if c.data_category else "",
        "processing_activity": c.processing_activity.name if c.processing_activity else "",
        "status": c.status,
        "consent_version": c.consent_version,
        "granted_at": c.granted_at.isoformat() if c.granted_at else "",
        "expires_at": c.expires_at.isoformat() if c.expires_at else "",
        "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else "",
        "collection_method": c.collection_method,
    }


def record_to_json_bytes(record: PrincipalRecord) -> bytes:
    payload = {
        "customer": {
            "external_id": record.customer.external_id,
            "name": record.customer.name,
            "email": record.customer.email,
            "phone": record.customer.phone,
            "source_app": record.source_app,
        },
        "generated_at": record.generated_at.isoformat(),
        "consents": [_consent_row(c) for c in record.consents],
        "history": [
            {
                "consent_id": h.consent_id, "action": h.action, "from_status": h.from_status,
                "to_status": h.to_status, "reason": h.reason, "created_at": h.created_at.isoformat(),
            }
            for h in record.history
        ],
        "evidence": [
            {
                "consent_id": e.consent_id, "evidence_ref": e.evidence_ref,
                "collected_at": e.collected_at.isoformat(), "collection_method": e.collection_method,
                "affirmative_action": e.affirmative_action, "language": e.language,
                "content_hash": e.content_hash, "notice_hash": e.notice_hash,
                "gpc_signal": e.gpc_signal,
            }
            for e in record.evidence
        ],
        "receipts": [
            {
                "receipt_ref": r.receipt_ref, "action": r.action, "consent_id": r.consent_id,
                "issued_at": r.issued_at.isoformat(), "payload_hash": r.payload_hash,
            }
            for r in record.receipts
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")


def record_to_csv_bytes(record: PrincipalRecord) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Consent360 consent record for {record.customer.external_id}",
                      f"generated {record.generated_at.isoformat()}"])
    writer.writerow([])
    writer.writerow(["CONSENTS"])
    writer.writerow(["id", "purpose_code", "purpose_name", "data_category", "processing_activity",
                      "status", "consent_version", "granted_at", "expires_at", "withdrawn_at",
                      "collection_method"])
    for c in record.consents:
        row = _consent_row(c)
        writer.writerow([row[k] for k in ("id", "purpose_code", "purpose_name", "data_category",
                                           "processing_activity", "status", "consent_version",
                                           "granted_at", "expires_at", "withdrawn_at", "collection_method")])
    writer.writerow([])
    writer.writerow(["HISTORY"])
    writer.writerow(["consent_id", "action", "from_status", "to_status", "reason", "created_at"])
    for h in record.history:
        writer.writerow([h.consent_id, h.action, h.from_status or "", h.to_status or "", h.reason, h.created_at.isoformat()])
    writer.writerow([])
    writer.writerow(["EVIDENCE"])
    writer.writerow(["consent_id", "evidence_ref", "collected_at", "collection_method", "affirmative_action",
                      "language", "content_hash", "notice_hash", "gpc_signal"])
    for e in record.evidence:
        writer.writerow([e.consent_id, e.evidence_ref, e.collected_at.isoformat(), e.collection_method,
                          e.affirmative_action, e.language, e.content_hash or "", e.notice_hash or "",
                          "" if e.gpc_signal is None else e.gpc_signal])
    writer.writerow([])
    writer.writerow(["RECEIPTS"])
    writer.writerow(["receipt_ref", "action", "consent_id", "issued_at", "payload_hash"])
    for r in record.receipts:
        writer.writerow([r.receipt_ref, r.action, r.consent_id, r.issued_at.isoformat(), r.payload_hash])
    return buf.getvalue().encode("utf-8")


def record_to_pdf_bytes(record: PrincipalRecord) -> bytes:
    """Uses fpdf2 (see requirements.txt - T-04 fixed its missing pin here)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Consent360 - Your Consent Record", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Generated: {record.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Your details", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"External ID: {record.customer.external_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Name: {record.customer.name}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Source: {record.source_app}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Consents ({len(record.consents)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    if not record.consents:
        pdf.cell(0, 5, "No consent records found.", new_x="LMARGIN", new_y="NEXT")
    for c in record.consents:
        row = _consent_row(c)
        if pdf.get_y() > 250:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, f"{row['purpose_name']} / {row['data_category']} / {row['processing_activity']}",
                  new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, f"  Status: {row['status']}  (v{row['consent_version']})", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 5, f"  Granted: {row['granted_at'] or '-'}  Expires: {row['expires_at'] or '-'}  "
                  f"Withdrawn: {row['withdrawn_at'] or '-'}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"History ({len(record.history)} events)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    for h in record.history:
        if pdf.get_y() > 260:
            pdf.add_page()
        pdf.cell(0, 5, f"{h.created_at.strftime('%Y-%m-%d %H:%M')}  {h.action}  "
                  f"{h.from_status or '-'} -> {h.to_status or '-'}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Receipts ({len(record.receipts)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    for r in record.receipts:
        if pdf.get_y() > 260:
            pdf.add_page()
        pdf.cell(0, 5, f"{r.receipt_ref}  {r.action}  issued {r.issued_at.strftime('%Y-%m-%d %H:%M')}",
                  new_x="LMARGIN", new_y="NEXT")

    out = pdf.output()
    if isinstance(out, str):
        out = out.encode("latin-1")
    return bytes(out)
