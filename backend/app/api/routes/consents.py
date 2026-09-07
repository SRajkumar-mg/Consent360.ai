from datetime import datetime, timedelta, timezone
from typing import Optional
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission, user_has_permission
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE, PERM_CONSENT_VIEW, PERM_CUSTOMER_CONTACT_VIEW
from app.core.utils import mask_identifier
from app.models.entities import (
    Consent,
    ConsentContext,
    ConsentEvidence,
    ConsentHistory,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
    User,
)
from app.schemas.schemas import (
    ConsentAction,
    ConsentDetailOut,
    ConsentEvidenceOut,
    ConsentHistoryOut,
    ConsentOut,
    CustomerConsentSummary,
)
from app.services import consent as consent_service
from app.services.audit import log_audit
from app.services.tenancy import ANY_TENANT, resolve_customer

router = APIRouter(prefix="/consents", tags=["consents"])


def _consent_out(consent: Consent) -> ConsentOut:
    return ConsentOut(
        id=consent.id,
        customer_id=consent.customer_id,
        customer_external_id=consent.customer.external_id if consent.customer else "",
        purpose_id=consent.purpose_id,
        purpose_name=consent.purpose.name if consent.purpose else "",
        purpose_code=consent.purpose.code if consent.purpose else "",
        purpose_version=consent.purpose_version.version_number if consent.purpose_version else 1,
        data_category_id=consent.data_category_id,
        data_category_name=consent.data_category.name if consent.data_category else "",
        processing_activity_id=consent.processing_activity_id,
        processing_activity_name=consent.processing_activity.name if consent.processing_activity else "",
        consent_version=consent.consent_version,
        status=consent.status,
        granted_at=consent.granted_at,
        expires_at=consent.expires_at,
        denied_at=consent.denied_at,
        withdrawn_at=consent.withdrawn_at,
        renewed_at=consent.renewed_at,
        requested_at=consent.requested_at,
        collection_method=consent.collection_method,
        source_app=consent.source_app,
        policy_code=consent.policy.code if consent.policy else "",
        policy_version=consent.policy_version.version_number if consent.policy_version else None,
        consent_text=consent.consent_text or "",
        created_at=consent.created_at,
    )


def _ensure_consent_matrix(db: Session, customer: Customer, actor_username: str = "system") -> None:
    """Lazily materialize NOT_REQUESTED consent rows for every active purpose x category x activity."""
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    for purpose in purposes:
        try:
            pv = consent_service.get_current_purpose_version(purpose)
        except Exception:
            continue
        cat_ids = pv.data_category_ids or []
        act_ids = pv.processing_activity_ids or []
        categories = db.query(DataCategory).filter(DataCategory.id.in_(cat_ids)).all() if cat_ids else []
        activities = db.query(ProcessingActivity).filter(ProcessingActivity.id.in_(act_ids)).all() if act_ids else []
        for dc in categories:
            for pa in activities:
                existing = (
                    db.query(Consent)
                    .filter(
                        Consent.customer_id == customer.id,
                        Consent.purpose_id == purpose.id,
                        Consent.data_category_id == dc.id,
                        Consent.processing_activity_id == pa.id,
                    )
                    .first()
                )
                if not existing:
                    consent_service.get_or_create_consent(
                        db, customer, purpose, dc, pa, actor_username=actor_username, source_app="SYSTEM"
                    )


@router.get("/summary/{customer_id}", response_model=CustomerConsentSummary)
def customer_summary(
    customer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(current_user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    _ensure_consent_matrix(db, customer, actor_username=current_user.username)
    log_audit(db, "CONSENT_VIEWED", actor_username=current_user.username,
              actor_type="USER", actor_id=str(current_user.id),
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", customer_id=customer.id, customer_external_id=customer.external_id,
              reason="Customer consent profile viewed")
    return _build_summary(
        db, customer, scope=scope,
        contact_visible=user_has_permission(current_user, PERM_CUSTOMER_CONTACT_VIEW),
    )


def _build_summary(db: Session, customer: Customer, scope: str | None = None,
                   contact_visible: bool = False) -> CustomerConsentSummary:
    q = db.query(Consent).filter(Consent.customer_id == customer.id)
    if scope:
        q = q.filter(Consent.source_app == scope)
    consents = q.order_by(Consent.purpose_id, Consent.data_category_id, Consent.processing_activity_id).all()
    now = datetime.now(timezone.utc)
    status_counts: dict[str, int] = {}
    expiring_soon = []
    for c in consents:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1
        if (
            c.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")
            and c.expires_at
            and now < c.expires_at <= now + timedelta(days=30)
        ):
            expiring_soon.append(_consent_out(c))
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).count()
    return CustomerConsentSummary(
        customer=_customer_out(customer, contact_visible=contact_visible),
        total_purposes=purposes,
        status_counts=status_counts,
        expiring_soon=expiring_soon,
        consents=[_consent_out(c) for c in consents],
    )


def _customer_out(customer: Customer, *, contact_visible: bool = False):
    """The customer header on a consent summary, masked by the same rule as
    GET /customers.

    This embeds a full `CustomerOut`, so leaving it unmasked would have made
    `customer.contact.view` decorative: any holder of `consent.view` could
    read the contact details the customer directory had just stopped showing
    them by asking for the same principal's consent summary instead.
    Defaults to masked so a future caller that forgets the argument fails
    closed.
    """
    from app.schemas.schemas import CustomerOut

    return CustomerOut.for_staff(customer, contact_visible=contact_visible)


@router.get("", response_model=list[ConsentOut])
def list_consents(
    customer_id: str = Query(default=None),
    purpose_id: int = Query(default=None),
    status: str = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    q = db.query(Consent).order_by(Consent.customer_id, Consent.purpose_id)
    if scope:
        q = q.filter(Consent.source_app == scope)
    if customer_id:
        customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_id)
        if not customer:
            return []
        q = q.filter(Consent.customer_id == customer.id)
    if purpose_id:
        q = q.filter(Consent.purpose_id == purpose_id)
    if status:
        q = q.filter(Consent.status == status.upper())
    return [_consent_out(c) for c in q.limit(500).all()]


@router.get("/expiring", response_model=list[ConsentOut])
def expiring_consents(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    q = db.query(Consent).filter(
        Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]),
        Consent.expires_at.isnot(None),
        Consent.expires_at > now,
        Consent.expires_at <= horizon,
    )
    if scope:
        q = q.filter(Consent.source_app == scope)
    consents = q.order_by(Consent.expires_at).all()
    return [_consent_out(c) for c in consents]


@router.get("/export/{customer_id}")
def export_customer_consents(
    customer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    from fpdf import FPDF

    scope = get_org_scope(current_user)
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    _ensure_consent_matrix(db, customer, actor_username=current_user.username)
    q = db.query(Consent).filter(Consent.customer_id == customer.id)
    if scope:
        q = q.filter(Consent.source_app == scope)
    consents = q.order_by(Consent.purpose_id, Consent.data_category_id, Consent.processing_activity_id).all()
    now = datetime.now(timezone.utc)
    status_counts: dict[str, int] = {}
    for c in consents:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1

    class ConsentPDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 16)
            self.cell(0, 10, "Consent 360 - Consent Report", align="C", new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Page {self.page_no()}/{{nb}}  |  Generated by Consent 360", align="C")

        def section_title(self, title):
            self.set_font("Helvetica", "B", 12)
            self.set_fill_color(240, 240, 240)
            self.cell(0, 8, f"  {title}", new_x="LMARGIN", new_y="NEXT", fill=True)
            self.ln(3)

        def field(self, label, value, w_label=55):
            self.set_font("Helvetica", "B", 9)
            self.cell(w_label, 6, label + ":")
            self.set_font("Helvetica", "", 9)
            self.set_text_color(50, 50, 50)
            self.multi_cell(0, 6, str(value or "-"), new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)

        def status_color(self, status):
            colors = {
                "ACTIVE": (34, 139, 34), "GRANTED": (34, 139, 34), "RENEWED": (34, 139, 34),
                "DENIED": (200, 30, 30), "WITHDRAWN": (200, 30, 30), "EXPIRED": (150, 150, 150),
                "PENDING": (200, 150, 0), "REQUESTED": (200, 150, 0), "NOT_REQUESTED": (150, 150, 150),
            }
            return colors.get(status, (0, 0, 0))

    pdf = ConsentPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # -- Report header info --
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, f"Exported: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}  |  By: {current_user.username}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # -- Customer information --
    pdf.section_title("Customer Information")
    pdf.field("Name", customer.name)
    pdf.field("External ID", customer.external_id)
    # Same rule as the JSON responses: a PDF is just another rendering of the
    # record, and it would be an odd control that masked the screen but let
    # the same viewer download the contact details as a file.
    _contact_visible = user_has_permission(current_user, PERM_CUSTOMER_CONTACT_VIEW)
    pdf.field("Email", (customer.email if _contact_visible else mask_identifier(customer.email)) or "-")
    pdf.field("Phone", (customer.phone if _contact_visible else mask_identifier(customer.phone)) or "-")
    pdf.field("Status", customer.status)
    pdf.field("Source App", customer.source_app or "-")
    pdf.field("Created At", str(customer.created_at)[:19] if customer.created_at else "-")
    pdf.ln(4)

    # -- Consent summary --
    pdf.section_title("Consent Summary")
    pdf.field("Total Consent Records", str(len(consents)))
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(50, 6, "  Status")
    pdf.cell(0, 6, "Count", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for status, count in sorted(status_counts.items()):
        r, g, b = pdf.status_color(status)
        pdf.set_text_color(r, g, b)
        pdf.cell(50, 6, f"  {status}")
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 6, str(count), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # -- Consent records --
    pdf.section_title("Consent Records")
    if not consents:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "No consent records found.", new_x="LMARGIN", new_y="NEXT")
    else:
        for i, c in enumerate(consents):
            if pdf.get_y() > 240:
                pdf.add_page()
            purpose_name = c.purpose.name if c.purpose else "-"
            dc_name = c.data_category.name if c.data_category else "-"
            pa_name = c.processing_activity.name if c.processing_activity else "-"
            r, g, b = pdf.status_color(c.status)

            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 7, f"{i+1}. {purpose_name}  /  {dc_name}  /  {pa_name}",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(r, g, b)
            pdf.cell(0, 5, f"    Status: {c.status}", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)

            pdf.set_font("Helvetica", "", 8)
            pdf.cell(0, 5, f"    Data Category: {dc_name}  |  Processing Activity: {pa_name}",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 5, f"    Collection Method: {c.collection_method or '-'}  |  Source App: {c.source_app or '-'}",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 5, f"    Consent Version: {c.consent_version}  |  Policy: {c.policy.code if c.policy else '-'} v{c.policy_version.version_number if c.policy_version else '-'}",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 5, f"    Granted: {str(c.granted_at)[:19] if c.granted_at else '-'}  |  Expires: {str(c.expires_at)[:19] if c.expires_at else '-'}",
                     new_x="LMARGIN", new_y="NEXT")
            if c.consent_text:
                pdf.set_font("Helvetica", "I", 8)
                text = c.consent_text[:200] + ("..." if len(c.consent_text or "") > 200 else "")
                pdf.cell(0, 5, f"    Consent Text: {text}", new_x="LMARGIN", new_y="NEXT")

            # History
            history = list(c.history)
            if history:
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(0, 5, f"    History ({len(history)} records):", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 7)
                for h in history:
                    detail = f"      {h.action}: {h.from_status or '->'} -> {h.to_status or '-'}"
                    detail += f"  |  Actor: {h.actor_username or '-'}  |  {str(h.created_at)[:19] if h.created_at else '-'}"
                    if h.reason:
                        detail += f"  |  Reason: {h.reason[:60]}"
                    pdf.cell(0, 4, detail, new_x="LMARGIN", new_y="NEXT")

            # Evidence
            evidence = list(c.evidence)
            if evidence:
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(0, 5, f"    Evidence ({len(evidence)} records):", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 7)
                for e in evidence:
                    detail = f"      Ref: {e.evidence_ref}  |  Method: {e.collection_method}  |  By: {e.collected_by}"
                    detail += f"  |  Version: {e.consent_version}  |  {str(e.collected_at)[:19] if e.collected_at else '-'}"
                    pdf.cell(0, 4, detail, new_x="LMARGIN", new_y="NEXT")

            pdf.ln(3)

    log_audit(db, "CONSENT_EXPORTED", actor_username=current_user.username,
              actor_type="USER", actor_id=str(current_user.id),
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", customer_id=customer.id, customer_external_id=customer.external_id,
              reason="Customer consent data exported as PDF")

    buf = io.BytesIO()
    pdf_bytes = pdf.output()
    if isinstance(pdf_bytes, str):
        pdf_bytes = pdf_bytes.encode("latin-1")
    buf.write(pdf_bytes)
    buf.seek(0)

    filename = f"consent-report-{customer.external_id}-{now.strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{consent_id}", response_model=ConsentDetailOut)
def consent_detail(consent_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_permission(PERM_CONSENT_VIEW))):
    scope = get_org_scope(current_user)
    consent = db.get(Consent, consent_id)
    if not consent:
        raise HTTPException(status_code=404, detail="Consent not found")
    if scope and consent.source_app != scope:
        raise HTTPException(status_code=404, detail="Consent not found")
    log_audit(db, "CONSENT_VIEWED", actor_username=current_user.username,
              actor_type="USER", actor_id=str(current_user.id),
              actor_role=current_user.role.name if current_user.role else "",
              source_app="UI", customer_id=consent.customer_id,
              customer_external_id=consent.customer.external_id if consent.customer else None,
              consent_id=consent.id, purpose_id=consent.purpose_id,
              purpose_code=consent.purpose.code if consent.purpose else None,
              reason="Consent detail viewed", commit=False)
    db.commit()
    history = [ConsentHistoryOut.model_validate(h) for h in consent.history]
    evidence = [ConsentEvidenceOut.model_validate(e) for e in consent.evidence]
    return ConsentDetailOut(consent=_consent_out(consent), history=history, evidence=evidence)


def _get_consent_or_404(db: Session, consent_id: int, scope: str | None = None) -> Consent:
    consent = db.get(Consent, consent_id)
    if not consent:
        raise HTTPException(status_code=404, detail="Consent not found")
    if scope and consent.source_app != scope:
        raise HTTPException(status_code=403, detail="Access denied: consent outside your organization scope")
    return consent


@router.post("/{consent_id}/grant", response_model=ConsentOut)
def grant(consent_id: int, action: ConsentAction, request: Request, db: Session = Depends(get_db),
          current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    consent = _get_consent_or_404(db, consent_id, scope=scope)
    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    result = consent_service.grant_consent(
        db, consent,
        expires_in_days=action.expires_in_days,
        reason=action.reason,
        actor_username=current_user.username,
        source_app="UI",
        collection_method=action.collection_method or "UI",
        consent_text=action.consent_text,
        client_context=action.context,
        actor_type="USER",
        actor_id=str(current_user.id),
        ip_address=observed_ip,
        user_agent=observed_user_agent,
    )
    consent_service.activate_consent(db, result, actor_username=current_user.username, source_app="UI",
                                      actor_type="USER", actor_id=str(current_user.id))
    return _consent_out(result)


@router.post("/{consent_id}/deny", response_model=ConsentOut)
def deny(consent_id: int, action: ConsentAction, request: Request, db: Session = Depends(get_db),
         current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    consent = _get_consent_or_404(db, consent_id, scope=scope)
    # B-01/B-02: the ClientContext and the observed IP / user agent are passed
    # here for the same reason `grant` and `withdraw` above pass them - now
    # that `deny_consent` evidences a refusal, dropping them would leave the
    # staff console's refusals the one weakly-evidenced transition left.
    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    result = consent_service.deny_consent(
        db, consent, reason=action.reason, actor_username=current_user.username,
        source_app="UI", collection_method=action.collection_method or "UI",
        client_context=action.context,
        actor_type="USER", actor_id=str(current_user.id),
        ip_address=observed_ip, user_agent=observed_user_agent,
    )
    return _consent_out(result)


@router.post("/{consent_id}/withdraw", response_model=ConsentOut)
def withdraw(consent_id: int, action: ConsentAction, request: Request, db: Session = Depends(get_db),
             current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    consent = _get_consent_or_404(db, consent_id, scope=scope)
    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    result = consent_service.withdraw_consent(
        db, consent, reason=action.reason, actor_username=current_user.username, source_app="UI",
        collection_method=action.collection_method or "UI",
        client_context=action.context,
        actor_type="USER", actor_id=str(current_user.id),
        ip_address=observed_ip, user_agent=observed_user_agent,
    )
    return _consent_out(result)


@router.post("/{consent_id}/renew", response_model=ConsentOut)
def renew(consent_id: int, action: ConsentAction, request: Request, db: Session = Depends(get_db),
          current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    consent = _get_consent_or_404(db, consent_id, scope=scope)
    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    result = consent_service.renew_consent(
        db, consent,
        expires_in_days=action.expires_in_days,
        reason=action.reason,
        actor_username=current_user.username,
        source_app="UI",
        collection_method=action.collection_method or "UI",
        client_context=action.context,
        actor_type="USER",
        actor_id=str(current_user.id),
        ip_address=observed_ip,
        user_agent=observed_user_agent,
    )
    consent_service.activate_consent(db, result, actor_username=current_user.username, source_app="UI",
                                      actor_type="USER", actor_id=str(current_user.id))
    return _consent_out(result)


@router.post("/{consent_id}/request", response_model=ConsentOut)
def request_consent_endpoint(consent_id: int, action: ConsentAction, db: Session = Depends(get_db),
                             current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    scope = get_org_scope(current_user)
    consent = _get_consent_or_404(db, consent_id, scope=scope)
    result = consent_service.request_consent(
        db, consent, reason=action.reason, actor_username=current_user.username,
        source_app="UI", collection_method=action.collection_method or "UI",
        actor_type="USER", actor_id=str(current_user.id),
    )
    return _consent_out(result)


@router.post("/expire-batch")
def expire_batch(db: Session = Depends(get_db),
                 current_user: User = Depends(require_permission(PERM_CONSENT_MANAGE))):
    count = consent_service.expire_consents(db, actor_username=current_user.username, source_app="SYSTEM")
    return {"message": f"{count} consent(s) expired", "expired": count}
