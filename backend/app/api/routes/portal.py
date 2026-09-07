import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_customer_from_context, verify_context_token
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import (
    Consent,
    ConsentContext,
    DataCategory,
    Notification,
    ProcessingActivity,
    Purpose,
)
from app.schemas.schemas import (
    MessageOut,
    NotificationOut,
    PortalActionIn,
    PortalActionOut,
    PortalOverview,
    PortalPurposeOut,
    PortalVerifyConfirmIn,
    PrincipalRecordOut,
)
from app.services import consent as consent_service
from app.services import notifications as notification_service
from app.services import principal_records
from app.services.audit import log_audit
from app.services.otp import confirm_otp, start_otp

router = APIRouter(prefix="/portal", tags=["customer-portal"])

settings = get_settings()

ACTIVE_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

# The statuses a refusal can legally be recorded from - NOT_REQUESTED,
# REQUESTED and PENDING today. Derived from CONSENT_TRANSITIONS itself and now
# defined once, in the consent service, because `/portal/deny` is no longer the
# only route that needs it: the CRM/Codex/SkillLearn cookie banner
# (`crm.py::_sync_consent_preferences`) reaches for the same rule. Re-exported
# under this module's own name so existing readers and tests keep working.
DENIABLE_STATUSES = consent_service.DENIABLE_STATUSES


def _resolve_customer_and_context(x_context_token: str, db: Session) -> tuple:
    payload = verify_context_token(x_context_token)
    customer = get_customer_from_context(db, payload)
    context = db.query(ConsentContext).filter(ConsentContext.token == x_context_token).first()
    if not context:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    # Second, independent tenant check against the PERSISTED context row
    # itself (not just the JWT claim already checked by
    # get_customer_from_context) - so a context row that was ever bound to
    # the wrong tenant's customer by any future minting path is refused
    # here too, regardless of what its token happens to claim.
    if context.source_app != customer.source_app:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    # R1-06/G-02: every /portal call is the data principal herself, holding
    # her own context token - which is exactly what DPDP Rules 2025 R.8(1)
    # read with the Third Schedule means by "last approached the Data
    # Fiduciary ... or exercised her rights", and therefore the zero point of
    # the three-year inactivity clock. Throttled to one write an hour inside
    # touch_last_interaction, and never fatal: failing to refresh a clock must
    # not deny a principal access to her own consent record.
    try:
        from app.services.erasure import touch_last_interaction

        if touch_last_interaction(db, customer, channel="portal"):
            db.commit()
    except Exception:  # noqa: BLE001 - a clock refresh must never break the portal
        db.rollback()
        logging.getLogger("app.erasure").exception(
            "Failed to refresh last_interaction_at for customer_id=%s", customer.id
        )
    return customer, context


def _require_verified(context: ConsentContext) -> None:
    if not settings.PORTAL_REQUIRE_VERIFICATION:
        return
    if context.verified_at is None:
        raise HTTPException(status_code=403, detail="Identity verification required before managing consent")


def _read_gpc_signal(request: Request) -> bool | None:
    """Global Privacy Control (https://globalprivacycontrol.org/): the
    ``Sec-GPC`` request header, read server-side rather than trusted from
    the request body (the same "claimed vs observed" split already applied
    to ip_address/user_agent below) - see ConsentEvidence.gpc_signal's
    docstring. Returns None when the header is absent (most callers), True
    when it is "1", False for any other value the header happens to carry."""
    header = request.headers.get("sec-gpc")
    if header is None:
        return None
    return header.strip() == "1"


def _is_active(consent: Consent, now: datetime) -> bool:
    if consent.status not in ACTIVE_STATUSES:
        return False
    if consent.expires_at is not None and consent.expires_at <= now:
        return False
    return True


def _ensure_consent_matrix(db: Session, customer, source_app: str, actor_username: str = "system") -> None:
    """Lazily materialize NOT_REQUESTED consent rows for every active purpose x category x activity.

    Scoped to ``source_app`` (the calling context's own tenant), mirroring
    app/api/routes/integration.py::_ensure_source_consent_matrix's per-source
    independence: both the "does a row already exist" check and any row
    created here are pinned to this one source_app, never a synthetic
    "PORTAL" bucket. A Customer identity can be shared across a family of
    source_apps by design (see the CRM/Codex/SkillLearn note in crm.py), but
    its Consent rows must not leak across them - docs/ARCHITECTURE.md's tenancy note is
    explicit that consent is per customer x purpose x category x activity x
    source_app.
    """
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
                        Consent.source_app == source_app,
                    )
                    .first()
                )
                if not existing:
                    consent_service.get_or_create_consent(
                        db, customer, purpose, dc, pa, actor_username=actor_username,
                        source_app=source_app, exact_source=True,
                    )


@router.get("/overview", response_model=PortalOverview)
def portal_overview(
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    _require_verified(context)
    _ensure_consent_matrix(db, customer, context.source_app, actor_username=f"principal:{customer.external_id}")
    now = datetime.now(timezone.utc)

    consents = (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.source_app == context.source_app)
        .all()
    )
    by_purpose: dict[int, list[Consent]] = {}
    for c in consents:
        by_purpose.setdefault(c.purpose_id, []).append(c)

    purposes = (
        db.query(Purpose)
        .filter(Purpose.is_active.is_(True), Purpose.requires_consent.is_(True))
        .order_by(Purpose.name)
        .all()
    )
    # R2-11/R1-09 (P-01): the campaign behind each flagged consent, looked up
    # once for the whole overview rather than per purpose.
    flagged_campaign_ids = {
        c.re_consent_campaign_id for cs in by_purpose.values() for c in cs
        if c.re_consent_required and c.re_consent_campaign_id
    }
    campaigns = {}
    if flagged_campaign_ids:
        from app.models.reconsent import ReConsentCampaign

        campaigns = {
            row.id: row for row in db.query(ReConsentCampaign)
            .filter(ReConsentCampaign.id.in_(flagged_campaign_ids)).all()
        }

    items: list[PortalPurposeOut] = []
    for purpose in purposes:
        pv = consent_service.get_current_purpose_version(purpose)
        cs = by_purpose.get(purpose.id, [])
        total = len(cs)
        granted = sum(1 for c in cs if _is_active(c, now))
        if total and granted == total:
            status = "GRANTED"
        elif granted > 0:
            status = "PARTIAL"
        else:
            status = "NOT_GRANTED"

        # A consent the decision engine is refusing to rely on must not be
        # reported to its own principal as simply granted. The re-pointed row
        # sits in UPDATED - which _is_active counts as live - so without this
        # the one person who needs to act is the one person not told.
        flagged = [c for c in cs if c.re_consent_required]
        campaign = None
        requested_at = None
        for c in flagged:
            if requested_at is None or (c.re_consent_requested_at and c.re_consent_requested_at > requested_at):
                requested_at = c.re_consent_requested_at
            if campaign is None and c.re_consent_campaign_id:
                campaign = campaigns.get(c.re_consent_campaign_id)

        items.append(
            PortalPurposeOut(
                code=purpose.code,
                name=purpose.name,
                description=pv.description or purpose.description,
                legal_basis=pv.legal_basis,
                requires_consent=pv.requires_consent,
                retention_period_days=pv.retention_period_days,
                consent_text=pv.consent_text,
                translations=pv.translations or {},
                status=status,
                granted_count=granted,
                total_count=total,
                re_consent_required=bool(flagged),
                re_consent_count=len(flagged),
                re_consent_requested_at=requested_at,
                re_consent_campaign_ref=campaign.campaign_ref if campaign else None,
                re_consent_reason=campaign.reason if campaign else "",
                purpose_version_number=pv.version_number,
            )
        )
    return PortalOverview(customer=customer, purposes=items)


@router.post("/grant", response_model=PortalActionOut)
def portal_grant(
    payload: PortalActionIn,
    request: Request,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    _require_verified(context)
    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail=f"Purpose {payload.purpose_code} not found")

    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    observed_gpc = _read_gpc_signal(request)

    _ensure_consent_matrix(db, customer, context.source_app, actor_username=f"principal:{customer.external_id}")
    consents = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
            Consent.source_app == context.source_app,
        )
        .all()
    )
    affected = 0
    gpc_refused = 0
    for c in consents:
        if _is_active(c, datetime.now(timezone.utc)):
            # R2-11/R1-09 (P-01). A consent flagged by a material change is
            # STILL "active" by status - `update_consent_for_purpose_version`
            # moves it to UPDATED, which _is_active counts as live - while the
            # decision engine refuses to process under it. Skipping it here,
            # as this loop used to, meant the one act that lifts the block
            # (`clear_re_consent`, called only from grant and renew) could
            # never be performed from the portal: the principal pressed
            # "agree", got "0 records", and stayed blocked forever.
            #
            # GRANTED is not a legal transition out of UPDATED/ACTIVE/RENEWED
            # (see CONSENT_TRANSITIONS), and forcing one would be rewriting
            # the state machine to suit a screen. RENEWED is - and a renewal
            # is exactly what this is: a fresh affirmative act, with its own
            # evidence row, its own receipt and its own consent_version, given
            # against the new purpose version. So re-affirmation goes through
            # renew_consent, and the flag clears there for the same reason it
            # clears on a grant.
            if not c.re_consent_required:
                continue
            consent_service.renew_consent(
                db,
                c,
                reason=(
                    "Re-affirmed by the data principal via self-service portal after a "
                    "material change to the purpose"
                ),
                actor_username=f"principal:{customer.external_id}",
                source_app="PORTAL",
                collection_method="PORTAL",
                client_context=payload.context,
                actor_type="PRINCIPAL",
                actor_id=customer.external_id,
                ip_address=observed_ip,
                user_agent=observed_user_agent,
                verified_context=context,
                gpc_signal=observed_gpc,
            )
            # R2-11/Q-07: renew_consent refuses under a server-observed
            # Sec-GPC: 1, so it can come back without having renewed
            # anything. Counting it regardless would tell the principal
            # "re-affirmed 1 record" for a record the server declined -
            # the same client/server disagreement the GPC fix exists to
            # remove, moved one layer up.
            if c.status == "RENEWED":
                affected += 1
            else:
                gpc_refused += 1
            continue
        granted = consent_service.grant_consent(
            db,
            c,
            reason="Granted by the data principal via self-service portal",
            actor_username=f"principal:{customer.external_id}",
            source_app="PORTAL",
            collection_method="PORTAL",
            client_context=payload.context,
            actor_type="PRINCIPAL",
            actor_id=customer.external_id,
            ip_address=observed_ip,
            user_agent=observed_user_agent,
            verified_context=context,
            gpc_signal=observed_gpc,
        )
        activated = consent_service.activate_consent(
            db,
            granted,
            actor_username=f"principal:{customer.external_id}",
            source_app="PORTAL",
            actor_type="PRINCIPAL",
            actor_id=customer.external_id,
        )
        # `activate_consent` returns the consent whatever it did (it is a
        # no-op unless the status is GRANTED), so the old `if activated:`
        # was always true and counted refusals as successes. R2-11/Q-07 made
        # that reachable: under a server-observed Sec-GPC: 1 the grant is
        # recorded as DENIED, and reporting it as granted is precisely the
        # client/server disagreement being fixed. Count what the record says.
        if activated.status in ACTIVE_STATUSES:
            affected += 1
        else:
            gpc_refused += 1
    # `action` says what the LEDGER now records, not which button was pressed.
    # It was hard-coded to "granted", so a purpose that GPC caused to be
    # refused came back as `action="granted", affected=0` with a message
    # saying it was refused - the label contradicting the record, and
    # contradicting `affected` and `message` in the same response. A caller
    # reading the machine-readable field rather than the prose would conclude
    # consent was given. Under GPC the rows are written DENIED, so "denied" is
    # the same word `/portal/deny` returns for the same recorded outcome.
    if gpc_refused and affected == 0:
        action = "denied"
        message = (
            f"Consent for {purpose.name} was not recorded: your browser is sending a Global "
            "Privacy Control objection (Sec-GPC: 1), which is honoured as a refusal for "
            f"purposes that rest on consent ({gpc_refused} records)."
        )
    else:
        action = "granted"
        message = f"Granted consent for {purpose.name} ({affected} records)"
        if gpc_refused:
            # Mixed outcome: something really was granted, so the action stays
            # "granted", but the refusals are said out loud rather than left
            # to be inferred from a count that does not add up.
            message += (
                f"; {gpc_refused} record(s) were refused instead - your browser is sending a "
                "Global Privacy Control objection (Sec-GPC: 1) for those"
            )
    return PortalActionOut(
        purpose_code=purpose.code,
        action=action,
        affected=affected,
        message=message,
    )


@router.post("/withdraw", response_model=PortalActionOut)
def portal_withdraw(
    payload: PortalActionIn,
    request: Request,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    _require_verified(context)
    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail=f"Purpose {payload.purpose_code} not found")

    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    observed_gpc = _read_gpc_signal(request)

    consents = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
            Consent.source_app == context.source_app,
        )
        .all()
    )
    affected = 0
    for c in consents:
        if not _is_active(c, datetime.now(timezone.utc)):
            continue
        consent_service.withdraw_consent(
            db,
            c,
            reason="Withdrawn by the data principal via self-service portal",
            actor_username=f"principal:{customer.external_id}",
            source_app="PORTAL",
            collection_method="PORTAL",
            client_context=payload.context,
            actor_type="PRINCIPAL",
            actor_id=customer.external_id,
            ip_address=observed_ip,
            user_agent=observed_user_agent,
            gpc_signal=observed_gpc,
        )
        affected += 1
    return PortalActionOut(
        purpose_code=purpose.code,
        action="withdrawn",
        affected=affected,
        message=f"Withdrawn consent for {purpose.name} ({affected} records)",
    )


@router.post("/deny", response_model=PortalActionOut)
def portal_deny(
    payload: PortalActionIn,
    request: Request,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """Record a refusal - the missing third verb (B-01/B-02, A-09, s.6(10)).

    `/portal/*` exposed `grant` and `withdraw` and nothing else, so a
    first-time visitor pressing "Reject all" - every consent row still
    NOT_REQUESTED, nothing to withdraw - produced no server call at all. The
    control was present and equal-weight, but the decision behind it existed
    only in that browser's localStorage: the fiduciary could not discharge
    s.6(10)'s burden of proving the person refused, and on a new device the
    banner asked again as though nothing had ever been decided. A refusal
    that leaves no trace is indistinguishable from never having been asked.

    Shape, auth and tenant scoping mirror `/portal/withdraw` exactly - the
    same `X-Context-Token`, the same double tenant check inside
    `_resolve_customer_and_context`, the same `source_app` filter on the
    consents it touches, the same `PortalActionIn` body. The one addition is
    `_ensure_consent_matrix`, which `/portal/grant` also calls and
    `/portal/withdraw` does not need: a refusal is most often the *first*
    thing a principal ever does, so the rows to deny may not exist yet.

    Rows that cannot legally reach DENIED (see `DENIABLE_STATUSES`) are left
    exactly as they are, never rewritten - an already-active consent is
    withdrawn, not denied (that is `/portal/withdraw`'s job, and the banner
    routes each purpose to the right verb), and a principal's own earlier
    withdrawal is hers, not something a later "reject all" may overwrite.
    """
    customer, context = _resolve_customer_and_context(x_context_token, db)
    _require_verified(context)
    purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
    if not purpose:
        raise HTTPException(status_code=404, detail=f"Purpose {payload.purpose_code} not found")

    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    observed_gpc = _read_gpc_signal(request)
    actor_username = f"principal:{customer.external_id}"

    _ensure_consent_matrix(db, customer, context.source_app, actor_username=actor_username)
    consents = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
            Consent.source_app == context.source_app,
        )
        .all()
    )
    affected = 0
    active_left = 0
    for c in consents:
        if c.status not in DENIABLE_STATUSES:
            if _is_active(c, datetime.now(timezone.utc)):
                active_left += 1
            continue
        # `deny_consent` writes the ConsentHistory row, the ConsentEvidence row
        # carrying this ClientContext and the server-observed GPC signal, and
        # the audit row - all in one transaction. That used to be a route-local
        # helper here; it now lives at the service layer so the CRM/Codex/
        # SkillLearn cookie banner produces the same evidence for the same act.
        consent_service.deny_consent(
            db,
            c,
            reason="Refused by the data principal via self-service portal",
            actor_username=actor_username,
            source_app="PORTAL",
            collection_method="PORTAL",
            client_context=payload.context,
            actor_type="PRINCIPAL",
            actor_id=customer.external_id,
            ip_address=observed_ip,
            user_agent=observed_user_agent,
            verified_context=context,
            gpc_signal=observed_gpc,
        )
        affected += 1

    message = f"Refused consent for {purpose.name} ({affected} records)"
    if active_left:
        # Said out loud rather than folded into a bare count, so a caller
        # cannot read "0 records" as "nothing needed doing" when what
        # actually happened is that a live consent is still live and needs
        # withdrawing instead.
        message += (
            f"; {active_left} already-active record(s) left unchanged - withdraw those instead"
        )
    return PortalActionOut(
        purpose_code=purpose.code,
        action="denied",
        affected=affected,
        message=message,
    )


@router.post("/verify/start", response_model=MessageOut)
def verify_start(x_context_token: str = Header(alias="X-Context-Token"), db: Session = Depends(get_db)):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    start_otp(db, context, customer)
    return MessageOut(message="Verification code sent")


@router.post("/verify/confirm", response_model=MessageOut)
def verify_confirm(
    payload: PortalVerifyConfirmIn,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    if not confirm_otp(db, context, payload.code):
        # Deliberately generic: a wrong code, an expired challenge, an
        # exhausted attempt cap and "no challenge at all" all land here
        # identically so the response never tells a caller which applied.
        raise HTTPException(status_code=400, detail="Incorrect or expired verification code")
    customer.last_verified_at = datetime.now(timezone.utc)
    db.commit()
    return MessageOut(message="Identity verified")


# ---------------------------------------------------------------------------
# R1-11 (D-06, CM-04, T-04): principal record access and export
# ---------------------------------------------------------------------------
def _principal_record_out(record: principal_records.PrincipalRecord) -> PrincipalRecordOut:
    from app.api.routes.consents import _consent_out

    return PrincipalRecordOut(
        customer=record.customer,
        source_app=record.source_app,
        generated_at=record.generated_at,
        consents=[_consent_out(c) for c in record.consents],
        history=[
            {
                "id": h.id, "action": h.action, "from_status": h.from_status, "to_status": h.to_status,
                "reason": h.reason, "consent_version": h.consent_version, "actor_username": h.actor_username,
                "source_app": h.source_app, "request_id": h.request_id, "details": h.details,
                "created_at": h.created_at,
            }
            for h in record.history
        ],
        evidence=[
            {
                "id": e.id, "evidence_ref": e.evidence_ref, "collected_at": e.collected_at,
                "collected_by": e.collected_by, "collection_method": e.collection_method,
                "source_app": e.source_app, "consent_version": e.consent_version,
                "purpose_version": e.purpose_version, "policy_version": e.policy_version,
                "notice_version_id": e.notice_version_id, "notice_hash": e.notice_hash,
                "language": e.language, "content_hash": e.content_hash, "request_id": e.request_id,
                "gpc_signal": e.gpc_signal, "details": e.details,
            }
            for e in record.evidence
        ],
        receipts=[
            {
                "id": r.id, "receipt_ref": r.receipt_ref, "consent_id": r.consent_id,
                "evidence_id": r.evidence_id, "customer_id": r.customer_id, "source_app": r.source_app,
                "action": r.action, "consent_version": r.consent_version,
                "notice_version_id": r.notice_version_id, "payload": r.payload,
                "payload_hash": r.payload_hash, "signature": r.signature, "issued_at": r.issued_at,
            }
            for r in record.receipts
        ],
    )


@router.get("/history", response_model=PrincipalRecordOut)
def portal_history(x_context_token: str = Header(alias="X-Context-Token"), db: Session = Depends(get_db)):
    """D-06/CM-04: a verified principal's complete consent record - every
    consent, its full lifecycle history, the evidence behind each
    grant/withdrawal, and every receipt issued - scoped to the one
    source_app this context token belongs to."""
    customer, context = _resolve_customer_and_context(x_context_token, db)
    _require_verified(context)
    record = principal_records.build_principal_record(db, customer, context.source_app)
    log_audit(
        db, "PRINCIPAL_RECORD_VIEWED", actor_username=f"principal:{customer.external_id}",
        actor_id=customer.external_id, actor_type="PRINCIPAL", source_app=context.source_app,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason="Principal viewed their own consent record via self-service portal",
    )
    return _principal_record_out(record)


@router.get("/export")
def portal_export(
    format: str = Query("json", pattern="^(json|csv|pdf)$"),
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    """D-06/CM-04/T-04: the same complete consent record as GET
    /portal/history, in a downloadable, machine-readable form."""
    customer, context = _resolve_customer_and_context(x_context_token, db)
    _require_verified(context)
    record = principal_records.build_principal_record(db, customer, context.source_app)
    log_audit(
        db, "PRINCIPAL_RECORD_EXPORTED", actor_username=f"principal:{customer.external_id}",
        actor_id=customer.external_id, actor_type="PRINCIPAL", source_app=context.source_app,
        customer_id=customer.id, customer_external_id=customer.external_id,
        reason=f"Principal exported their own consent record ({format}) via self-service portal",
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"consent-record-{customer.external_id}-{stamp}.{format}"
    if format == "json":
        content, media_type = principal_records.record_to_json_bytes(record), "application/json"
    elif format == "csv":
        content, media_type = principal_records.record_to_csv_bytes(record), "text/csv"
    else:
        content, media_type = principal_records.record_to_pdf_bytes(record), "application/pdf"

    import io as _io

    return StreamingResponse(
        _io.BytesIO(content), media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# R3-06 (O-01/O-02): in-app notification channel
# ---------------------------------------------------------------------------
@router.get("/notifications", response_model=list[NotificationOut])
def portal_notifications(x_context_token: str = Header(alias="X-Context-Token"), db: Session = Depends(get_db)):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    rows = (
        db.query(Notification)
        .filter(Notification.customer_id == customer.id, Notification.source_app == context.source_app)
        .order_by(Notification.created_at.desc())
        .limit(200)
        .all()
    )
    return [NotificationOut.model_validate(n) for n in rows]


@router.post("/notifications/{notification_id}/acknowledge", response_model=NotificationOut)
def portal_acknowledge_notification(
    notification_id: int,
    x_context_token: str = Header(alias="X-Context-Token"),
    db: Session = Depends(get_db),
):
    customer, context = _resolve_customer_and_context(x_context_token, db)
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.customer_id == customer.id,
            Notification.source_app == context.source_app,
        )
        .first()
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    try:
        notification_service.acknowledge_notification(
            db, notification, actor_username=f"principal:{customer.external_id}"
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NotificationOut.model_validate(notification)
