import csv
import hashlib
import io
import json as _json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_VIEW
from app.core.utils import get_request_id, log_audit
from app.models.entities import AuditLog, Customer, User
from app.schemas.schemas import AuditEventOut
from app.services.audit_chain import verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
def list_audit_events(
    customer_name: str = Query(default=None),
    purpose_code: str = Query(default=None),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    log_audit(db, "AUDIT_QUERY", actor_username=user.username,
              actor_role=user.role.name if user.role else "",
              source_app="UI", reason="Audit log queried",
              request_id=get_request_id(),
              metadata={"customer_name": customer_name, "purpose_code": purpose_code,
                        "date_from": date_from, "date_to": date_to,
                        "returned_count": limit})
    scope = get_org_scope(user)
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if scope:
        # R1-02: prefer the tenant scope; fall back to source_app when the
        # staff user is org-scoped by source_app (backward compatibility).
        q = q.filter((AuditLog.tenant_id == getattr(user, "tenant_id", None)) | (AuditLog.source_app == scope))
    if customer_name:
        name_lower = f"%{customer_name}%".lower()
        matching_customer_ids = [
            c.id for c in db.query(Customer).all()
            if name_lower in (c.name or "").lower()
        ]
        if not matching_customer_ids:
            return []
        q = q.filter(AuditLog.customer_id.in_(matching_customer_ids))
    if purpose_code:
        q = q.filter(AuditLog.purpose_code == purpose_code)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    events = q.limit(limit).offset(offset).all()
    return [AuditEventOut.model_validate(e) for e in events]


@router.get("/verify-chain")
def verify_ledger(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    """R1-02: report whether the audit ledger hash-chain is intact."""
    return verify_chain(db)


@router.get("/export")
def export_audit(
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    """R1-11: export audit events and the ledger chain validity as JSON Lines."""
    scope = get_org_scope(user)
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if scope:
        q = q.filter((AuditLog.tenant_id == getattr(user, "tenant_id", None)) | (AuditLog.source_app == scope))
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    events = q.all()
    chain = verify_chain(db)
    lines = [AuditEventOut.model_validate(e).model_dump(mode="json") for e in events]
    # JSON Lines response with a trailing chain marker
    import json as _json
    body = "\n".join(_json.dumps(e, default=str) for e in lines)
    chain_line = _json.dumps({"__ledger_chain__": chain})
    return Response(content=body + "\n" + chain_line + "\n", media_type="application/x-ndjson")


@router.get("/events")
def list_event_types(_: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    from app.models.entities import AUDIT_EVENTS

    return AUDIT_EVENTS


@router.get("/actors")
def list_actors(db: Session = Depends(get_db), user: User = Depends(require_permission(PERM_AUDIT_VIEW))):
    log_audit(db, "ACTOR_LIST", actor_username=user.username,
              actor_role=user.role.name if user.role else "",
              source_app="UI", reason="Actor list retrieved",
              request_id=get_request_id())
    rows = db.query(AuditLog.actor_username).distinct().order_by(AuditLog.actor_username).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
#  Multi-format export (R1-02 / R1-11)
# ---------------------------------------------------------------------------

_EXPORT_FIELDS = [
    ("ID", "id"),
    ("Timestamp", "created_at"),
    ("Tenant ID", "tenant_id"),
    ("Actor ID", "actor_id"),
    ("Actor Type", "actor_type"),
    ("Event", "event"),
    ("Actor Role", "actor_role"),
    ("Source App", "source_app"),
    ("Customer ID", "customer_external_id"),
    ("Consent ID", "consent_id"),
    ("Purpose Code", "purpose_code"),
    ("Policy Code", "policy_code"),
    ("Old Status", "old_status"),
    ("New Status", "new_status"),
    ("Consent Version", "consent_version"),
    ("Policy Version", "policy_version"),
    ("Decision", "decision"),
    ("Reason", "reason"),
    ("Request ID", "request_id"),
    ("IP Address", "ip_address"),
    ("User Agent", "user_agent"),
    ("Entry Hash", "entry_hash"),
    ("Previous Hash", "prev_hash"),
    ("Details", "details"),
]


def _apply_filters(q, customer_name, purpose_code, date_from, date_to, scope):
    if scope:
        q = q.filter((AuditLog.tenant_id == scope) | (AuditLog.source_app == scope))
    if customer_name:
        name_lower = f"%{customer_name}%".lower()
        ids = [c.id for c in q.session.query(Customer).all()
               if name_lower in (c.name or "").lower()]
        if not ids:
            return q.filter(AuditLog.id == -1)
        q = q.filter(AuditLog.customer_id.in_(ids))
    if purpose_code:
        q = q.filter(AuditLog.purpose_code == purpose_code)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    return q


def _event_rows(db, user, customer_name, purpose_code, date_from, date_to):
    scope = get_org_scope(user)
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    q = _apply_filters(q, customer_name, purpose_code, date_from, date_to, scope)
    return q.all()


def _row_dict(event: AuditLog) -> dict:
    d = {}
    for label, attr in _EXPORT_FIELDS:
        val = getattr(event, attr, None)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, dict):
            val = _json.dumps(val, default=str)
        d[label] = val if val is not None else ""
    return d


def _compute_manifest(rows: list[dict], chain_info: dict) -> str:
    payload = _json.dumps({"rows": rows, "chain": chain_info}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _local_chain_verify(events: list) -> dict:
    """Compute hash-chain integrity over the given filtered audit rows only."""
    from app.services.audit_chain import compute_entry_hash
    prev_hash = None
    for i, row in enumerate(events):
        if row.prev_hash is not None and row.prev_hash != prev_hash:
            return {"verified": False, "checked": i, "first_broken": row.id}
        recomputed = compute_entry_hash(row, row.prev_hash)
        if recomputed != row.entry_hash:
            return {"verified": False, "checked": i + 1, "first_broken": row.id}
        prev_hash = row.entry_hash
    return {"verified": True, "checked": len(events), "first_broken": None}


@router.get("/export-formats")
def export_audit_formats(
    format: str = Query(default="json", regex="^(csv|excel|pdf|json)$"),
    customer_name: str = Query(default=None),
    purpose_code: str = Query(default=None),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    """Export audit events in CSV, Excel, PDF, or JSON format with integrity hash."""
    log_audit(db, "AUDIT_EXPORT", actor_username=user.username,
              actor_role=user.role.name if user.role else "",
              source_app="UI", reason=f"Audit exported as {format}",
              request_id=get_request_id(), commit=False,
              metadata={"format": format, "customer_name": customer_name,
                        "purpose_code": purpose_code,
                        "date_from": date_from, "date_to": date_to})

    events = _event_rows(db, user, customer_name, purpose_code, date_from, date_to)
    rows = [_row_dict(e) for e in events]
    content_payload = _json.dumps(rows, sort_keys=True, default=str)
    content_hash = hashlib.sha256(content_payload.encode("utf-8")).hexdigest()
    chain = {"verified": True, "checked": len(rows), "content_hash": content_hash}
    manifest = _compute_manifest(rows, chain)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = {"exported_at": datetime.now(timezone.utc).isoformat(),
            "format": format, "total_records": len(rows),
            "content_hash": manifest, "ledger_chain": chain,
            "filters": {"customer_name": customer_name, "purpose_code": purpose_code,
                        "date_from": date_from, "date_to": date_to}}

    # ---- JSON ----
    if format == "json":
        body = _json.dumps({"meta": meta, "records": rows}, indent=2, default=str)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="audit-export-{now_str}.json"'},
        )

    # ---- CSV ----
    if format == "csv":
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        buf.write(f"\n# content_hash: {manifest}\n")
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="audit-export-{now_str}.csv"'},
        )

    # ---- Excel ----
    if format == "excel":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = Workbook()
        ws = wb.active
        ws.title = "Audit Export"

        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin", color="D1D5DB"),
            right=Side(style="thin", color="D1D5DB"),
            top=Side(style="thin", color="D1D5DB"),
            bottom=Side(style="thin", color="D1D5DB"),
        )

        for col_idx, (label, _) in enumerate(_EXPORT_FIELDS, 1):
            cell = ws.cell(row=1, column=col_idx, value=label)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        alt_fill = PatternFill(start_color="F3F4F6", end_color="F3F4F6", fill_type="solid")
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, (_, attr) in enumerate(_EXPORT_FIELDS, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=row_data.get(attr, ""))
                cell.border = thin_border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if row_idx % 2 == 0:
                    cell.fill = alt_fill

        ws.auto_filter.ref = ws.dimensions
        for col_idx, (label, _) in enumerate(_EXPORT_FIELDS, 1):
            max_len = max(len(label), max((len(str(ws.cell(row=r, column=col_idx).value or "")) for r in range(2, ws.max_row + 1)), default=0))
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 4, 40)

        meta_ws = wb.create_sheet("Manifest")
        meta_ws["A1"] = "Field"
        meta_ws["B1"] = "Value"
        meta_ws["A1"].font = Font(bold=True)
        meta_ws["B1"].font = Font(bold=True)
        meta_items = [("Content Hash", manifest), ("Total Records", len(rows)),
                      ("Exported At", meta["exported_at"]), ("Verified", chain.get("verified", False)),
                      ("Entries Checked", chain.get("checked", 0))]
        for i, (k, v) in enumerate(meta_items, 2):
            meta_ws[f"A{i}"] = k
            meta_ws[f"B{i}"] = str(v)
        meta_ws.column_dimensions["A"].width = 20
        meta_ws.column_dimensions["B"].width = 60

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="audit-export-{now_str}.xlsx"'},
        )

    # ---- PDF ----
    if format == "pdf":
        from fpdf import FPDF

        class AuditPDF(FPDF):
            def header(self):
                self.set_font("Helvetica", "B", 14)
                self.cell(0, 10, "Consent360 - Audit Export Report", new_x="LMARGIN", new_y="NEXT", align="C")
                self.set_font("Helvetica", "", 9)
                info = f"Generated: {meta['exported_at']}  |  Records: {len(rows)}  |  Hash: {manifest[:16]}..."
                self.cell(0, 6, info, new_x="LMARGIN", new_y="NEXT", align="C")
                self.ln(4)

            def footer(self):
                self.set_y(-15)
                self.set_font("Helvetica", "I", 8)
                chain_label = "Verified" if chain.get("verified") else "Broken"
                self.cell(0, 10, f"Page {self.page_no()}  |  Chain: {chain_label}", align="C")

        pdf = AuditPDF(orientation="L", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()

        display_cols = [
            ("ID", 12), ("Timestamp", 38), ("Actor ID", 28), ("Actor Type", 18),
            ("Event", 35), ("Customer", 25), ("Purpose", 22), ("Decision", 18),
            ("Old", 18), ("New", 18), ("Request ID", 22), ("Entry Hash", 30),
        ]

        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(37, 99, 235)
        pdf.set_text_color(255, 255, 255)
        for label, w in display_cols:
            pdf.cell(w, 7, label, border=1, fill=True, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(0, 0, 0)
        for idx, row in enumerate(rows):
            fill = idx % 2 == 0
            if fill:
                pdf.set_fill_color(243, 244, 246)
            vals = [
                str(row.get("ID", "")),
                str(row.get("Timestamp", ""))[:19],
                str(row.get("Actor ID", ""))[:26],
                str(row.get("Actor Type", ""))[:16],
                str(row.get("Event", ""))[:33],
                str(row.get("Customer ID", ""))[:23],
                str(row.get("Purpose Code", ""))[:20],
                str(row.get("Decision", ""))[:16],
                str(row.get("Old Status", ""))[:16],
                str(row.get("New Status", ""))[:16],
                str(row.get("Request ID", ""))[:20],
                str(row.get("Entry Hash", ""))[:28],
            ]
            for val, (_, w) in zip(vals, display_cols):
                pdf.cell(w, 5.5, val, border=1, fill=fill, align="L")
            pdf.ln()

        pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Export Manifest", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        manifest_items = [
            ("Content Hash", manifest),
            ("Total Records", str(len(rows))),
            ("Exported At", meta["exported_at"]),
            ("Chain Verified", str(chain.get("verified", False))),
            ("Entries Checked", str(chain.get("checked", 0))),
            ("First Broken Entry", str(chain.get("first_broken", "None"))),
        ]
        for k, v in manifest_items:
            pdf.cell(50, 6, k + ":", border=0)
            pdf.cell(0, 6, v, new_x="LMARGIN", new_y="NEXT")

        pdf_bytes = bytes(pdf.output())
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="audit-export-{now_str}.pdf"'},
        )
