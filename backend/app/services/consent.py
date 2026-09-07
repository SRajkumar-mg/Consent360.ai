import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.encryption import hmac_signature
from app.models.entities import (
    CONSENT_STATUSES,
    CONSENT_TRANSITIONS,
    Consent,
    ConsentContext,
    ConsentEvidence,
    ConsentHistory,
    Customer,
    DataCategory,
    Notice,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.schemas.schemas import ClientContext
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# The statuses a refusal can legally be recorded from (NOT_REQUESTED,
# REQUESTED, PENDING today) and the statuses a withdrawal can legally be
# recorded from (GRANTED, ACTIVE, RENEWED, UPDATED). Both are derived from
# the state machine itself rather than restated, so they cannot drift the
# next time CONSENT_TRANSITIONS is reconciled - `_validate_transition` stays
# the authority either way, these only decide which verb a route reaches for.
#
# They live here, not in a route, because the same "deny what was never
# granted, withdraw what is live" rule is needed by BOTH `/portal/deny` and
# `crm.py::_sync_consent_preferences` (the CRM/Codex/SkillLearn cookie
# banner). A copy per route is how three of the four demo sites ended up
# recording no refusal at all.
DENIABLE_STATUSES = tuple(
    status for status, allowed in CONSENT_TRANSITIONS.items() if "DENIED" in allowed
)
WITHDRAWABLE_STATUSES = tuple(
    status for status, allowed in CONSENT_TRANSITIONS.items() if "WITHDRAWN" in allowed
)


def _resolve_policy_version(db: Session) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (policy_id, policy_version_id, policy_version_number) of active policy current version."""
    from app.services.decision_engine import get_active_policy

    policy = get_active_policy(db)
    if not policy:
        return None, None, None
    for v in sorted(policy.versions, key=lambda x: x.version_number, reverse=True):
        if v.is_current:
            return policy.id, v.id, v.version_number
    return policy.id, None, None


def get_current_purpose_version(purpose: Purpose) -> PurposeVersion:
    for v in purpose.versions:
        if v.is_current:
            return v
    raise HTTPException(status_code=500, detail=f"Purpose {purpose.code} has no current version")


def get_or_create_consent(
    db: Session,
    customer: Customer,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    *,
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
    exact_source: bool = False,
) -> tuple[Consent, bool]:
    """Find existing consent for the identity; create a NOT_REQUESTED one if missing.

    By default the lookup is source-agnostic (one row per purpose x category x activity).
    When ``exact_source`` is True the lookup is restricted to the given ``source_app`` so
    that independent consent records can exist per website/app source.
    """
    base = db.query(Consent).filter(
        Consent.customer_id == customer.id,
        Consent.purpose_id == purpose.id,
        Consent.data_category_id == data_category.id,
        Consent.processing_activity_id == processing_activity.id,
        Consent.status.in_(["NOT_REQUESTED", "REQUESTED", "PENDING", "GRANTED", "ACTIVE", "DENIED", "WITHDRAWN", "EXPIRED"]),
    )
    if exact_source:
        base = base.filter(Consent.source_app == source_app)
    existing = base.order_by(Consent.consent_version.desc()).first()
    if existing:
        return existing, False

    pv = get_current_purpose_version(purpose)
    policy_id, policy_version_id, policy_version_number = _resolve_policy_version(db)
    consent = Consent(
        customer_id=customer.id,
        purpose_id=purpose.id,
        purpose_version_id=pv.id,
        data_category_id=data_category.id,
        processing_activity_id=processing_activity.id,
        policy_id=policy_id,
        policy_version_id=policy_version_id,
        consent_version=1,
        status="NOT_REQUESTED",
        collection_method=collection_method,
        source_app=source_app,
        actor_username=actor_username,
        consent_text=pv.consent_text,
        tenant_id=resolve_tenant_id(db, source_app),
    )
    db.add(consent)
    db.flush()

    history = ConsentHistory(
        consent_id=consent.id,
        action="CONSENT_CREATED",
        from_status=None,
        to_status="NOT_REQUESTED",
        reason="Consent record initialized",
        consent_version=1,
        policy_version_id=policy_version_id,
        actor_username=actor_username,
        source_app=source_app,
        request_id=request_id,
    )
    db.add(history)
    log_audit(
        db,
        "CONSENT_CREATED",
        actor_username=actor_username,
        source_app=source_app,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        consent_id=consent.id,
        purpose_id=purpose.id,
        purpose_code=purpose.code,
        policy_id=policy_id,
        policy_code=None,
        old_status=None,
        new_status="NOT_REQUESTED",
        consent_version=1,
        policy_version=policy_version_number,
        reason="Consent record created for customer",
        request_id=request_id,
        commit=False,
    )
    db.commit()
    db.refresh(consent)
    return consent, True


def _validate_transition(from_status: str, to_status: str, action: str) -> None:
    allowed = CONSENT_TRANSITIONS.get(from_status, [])
    if from_status not in CONSENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid consent status: {from_status}")
    if to_status not in CONSENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid target consent status: {to_status}")
    if to_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid consent lifecycle transition {from_status} -> {to_status} for action {action}",
        )


def _record_transition(
    db: Session,
    consent: Consent,
    *,
    action: str,
    to_status: str,
    from_status: Optional[str] = None,
    reason: str,
    actor_username: str,
    source_app: str,
    request_id: Optional[str] = None,
    policy_version_id: Optional[int] = None,
    policy_version_number: Optional[int] = None,
    metadata: Optional[dict] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    history = ConsentHistory(
        consent_id=consent.id,
        action=action,
        from_status=from_status or consent.status,
        to_status=to_status,
        reason=reason,
        consent_version=consent.consent_version,
        policy_version_id=policy_version_id,
        actor_username=actor_username,
        source_app=source_app,
        request_id=request_id,
        details=metadata or {},
    )
    db.add(history)
    log_audit(
        db,
        action,
        actor_username=actor_username,
        actor_id=actor_id,
        actor_type=actor_type,
        actor_role="",
        source_app=source_app,
        customer_id=consent.customer_id,
        customer_external_id=consent.customer.external_id,
        consent_id=consent.id,
        purpose_id=consent.purpose_id,
        purpose_code=consent.purpose.code,
        policy_id=consent.policy_id,
        policy_code=consent.policy.code if consent.policy else None,
        old_status=from_status or consent.status,
        new_status=to_status,
        consent_version=consent.consent_version,
        policy_version=policy_version_number,
        reason=reason,
        request_id=request_id,
        metadata=metadata or {},
        ip_address=ip_address,
        user_agent=user_agent,
        commit=False,
    )


def _assert_child_consent_permitted(
    db: Session,
    consent: Consent,
    *,
    action: str,
    actor_username: str,
    source_app: str,
    request_id: Optional[str],
) -> Optional[dict]:
    """R1-14 (F-01/F-02/F-04, DPDP s.9 + Rules 2025 R.10-R.12).

    A thin adapter over `app/services/guardian.py::assert_consent_permitted`,
    imported lazily for the same reason `issue_receipt`, `clear_re_consent`
    and `queue_notification` are imported lazily in this module: the guardian
    service imports `app.models.entities` inside its own functions and this
    module is imported from routes that the models package pulls in, so a
    module-scope import here would tighten an already delicate cycle for no
    benefit.

    Unlike those three, this one is deliberately NOT wrapped in a
    try/except. Every other cross-service call in this file is bookkeeping
    that must never fail a consent action; this one is the opposite - it is
    the check whose failure IS the point. Swallowing an exception here would
    turn "we could not confirm this child has parental consent" into "consent
    recorded", which is the failure mode the s.9 penalty head exists for. If
    the guardian service raises anything other than its own HTTPException,
    the consent action fails loudly rather than proceeding blind.

    Returns the facts to stamp on the resulting ConsentEvidence row, or None
    when s.9 does not apply to this principal.
    """
    from app.services.guardian import assert_consent_permitted

    return assert_consent_permitted(
        db,
        consent,
        action=action,
        actor_username=actor_username,
        source_app=source_app,
        request_id=request_id,
    )


def _refuse_under_gpc(
    db: Session,
    consent: Consent,
    *,
    action: str,
    actor_username: str,
    source_app: str,
    collection_method: str,
    request_id: Optional[str],
    client_context: Optional[ClientContext],
    ip_address: Optional[str],
    user_agent: Optional[str],
    verified_context: Optional[ConsentContext],
) -> Consent:
    """R2-11/Q-07 - act on a server-observed `Sec-GPC: 1`.

    THE CHOICE, of the three on the table (refuse to record the grant at all,
    record it as DENIED, or record it and immediately withdraw it):
    **record it as DENIED**, non-fatally, per consent row.

    *Not* "refuse to record": raising would 403 the whole request. On the CRM
    cookie banner that request carries every category at once, so one objected
    purpose would abort the principal's other choices half-applied, and the
    ledger would hold an audit line with no consent artefact attached to it -
    failing "visible in evidence". A refusal that loses the record of the
    refusal is the wrong trade for a compliance ledger.

    *Not* "record and withdraw": that writes GRANTED at t and WITHDRAWN at
    t+e, which asserts in an append-only ledger that valid consent existed and
    was later revoked. It never existed - s.6(1) was not satisfied at t - and
    the window between the two rows is a real interval in which
    `evaluate_decision` answers ALLOW, i.e. it reintroduces the very
    client/server disagreement this fixes.

    DENIED says exactly what happened: the principal's agent objected, so the
    affirmative act was not accepted as consent. It needs no new status, no
    new decision outcome and no change to `CONSENT_TRANSITIONS` -
    NOT_REQUESTED/REQUESTED/PENDING -> DENIED is already the "decline" edge
    the cookie banner's reject-all uses. `evaluate_decision` then returns DENY
    off the status it already reads.

    When DENIED is *not* a legal transition from the current status
    (WITHDRAWN, EXPIRED, or already DENIED) the status is left exactly as it
    is: every one of those is already non-affirmative, and rewriting a
    principal's own withdrawal into a system denial would overwrite her act
    with ours. The refusal is still evidenced and audited - GPC may block a
    consent, it may never weaken a record the principal already made.

    Either way a `ConsentEvidence` row is written carrying the server-observed
    `gpc_signal=True`, and a `GPC_OBJECTION_ENFORCED` audit row records the
    outcome, so the objection is visible in evidence and audit rather than
    silently suppressed.
    """
    from app.services.gpc import GPC_ENFORCED_EVENT, refusal_reason

    purpose = consent.purpose
    reason = refusal_reason(purpose, action)
    from_status = consent.status
    denied = "DENIED" in CONSENT_TRANSITIONS.get(from_status, [])
    if denied:
        consent.status = "DENIED"
        consent.denied_at = utcnow()
        consent.granted_at = None
        consent.withdrawn_at = None
        _record_transition(
            db, consent, action="CONSENT_DENIED", to_status="DENIED", from_status=from_status,
            reason=reason, actor_username=actor_username, source_app=source_app,
            request_id=request_id, policy_version_id=consent.policy_version_id,
            policy_version_number=(
                consent.policy_version.version_number if consent.policy_version else None
            ),
            metadata={"gpc_objection_enforced": True, "refused_action": action},
            actor_type="SYSTEM", actor_id=None, ip_address=ip_address, user_agent=user_agent,
        )
    evidence = _create_evidence(
        db, consent, collected_by=actor_username, collection_method=collection_method,
        source_app=source_app, request_id=request_id,
        extra_metadata={
            "gpc_objection_enforced": True,
            "refused_action": action,
            "status_after_refusal": consent.status,
        },
        client_context=client_context, ip_address=ip_address, user_agent=user_agent,
        verified_context=verified_context,
        # The server's own read of the header, never ClientContext.gpc_signal.
        gpc_signal=True,
    )
    if denied:
        consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    log_audit(
        db,
        GPC_ENFORCED_EVENT,
        actor_username=actor_username,
        actor_type="SYSTEM",
        source_app=source_app or consent.source_app or "",
        tenant_id=consent.tenant_id,
        customer_id=consent.customer_id,
        customer_external_id=consent.customer.external_id,
        consent_id=consent.id,
        purpose_id=consent.purpose_id,
        purpose_code=purpose.code,
        old_status=from_status,
        new_status=consent.status,
        decision="DENY",
        reason=reason,
        request_id=request_id,
        metadata={
            "gpc_signal": True,
            "refused_action": action,
            "status_before": from_status,
            "status_after": consent.status,
            "denied_recorded": denied,
            "evidence_ref": evidence.evidence_ref,
        },
        commit=False,
    )
    db.commit()
    db.refresh(consent)
    return consent


# Affirmative actions that assert some external proof of the principal's act
# (a signed document, a recorded call, or a one-time-password challenge) and
# therefore require a reference to that proof - a bare claim is not evidence.
_REFERENCE_REQUIRED_ACTIONS = ("DOCUMENT", "CALL", "OTP")


def _create_evidence(
    db: Session,
    consent: Consent,
    *,
    collected_by: str,
    collection_method: str,
    source_app: str,
    request_id: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
    client_context: Optional[ClientContext] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    verified_context: Optional[ConsentContext] = None,
    gpc_signal: Optional[bool] = None,
) -> ConsentEvidence:
    ctx = client_context or ClientContext()
    affirmative_reference = ctx.affirmative_reference

    # When the caller claims the consent was OTP-verified AND this collection
    # path has an actual verified ConsentContext in hand (the self-service
    # portal), do not trust a client-supplied reference string for that claim
    # - derive it from the server's own verification record instead. A claim
    # that isn't backed by a real OTP-verified context is rejected outright
    # rather than silently accepted with an empty/fabricated reference, even
    # though PORTAL collection is otherwise exempt from the reference
    # requirement below (see the module note on affirmative_action="OTP").
    if ctx.affirmative_action == "OTP" and verified_context is not None:
        if not verified_context.verified_at or verified_context.verification_method != "EMAIL_OTP":
            raise HTTPException(
                status_code=422,
                detail="affirmative_action OTP was claimed but this context has no completed OTP verification",
            )
        affirmative_reference = (
            f"otp-verified-context:{verified_context.id}:{verified_context.verified_at.isoformat()}"
        )

    if (
        collection_method != "PORTAL"
        and ctx.affirmative_action in _REFERENCE_REQUIRED_ACTIONS
        and not affirmative_reference
    ):
        raise HTTPException(
            status_code=422,
            detail=f"affirmative_reference is required when affirmative_action is {ctx.affirmative_action} "
                   "and collection_method is not PORTAL",
        )
    pv = consent.purpose_version
    text = consent.consent_text or pv.consent_text
    # The real, published NoticeVersion for this purpose (R1-04/A-07/D-04) -
    # resolved by the server from the Notice table, never from the client.
    # ctx.notice_version (whatever the caller's request body claims it
    # showed) is never trusted as the id to store: a caller cannot assert
    # which notice it displayed any more than it can assert its own IP/UA
    # below, so it is recorded only as an unverified "claimed" signal.
    notice = (
        db.query(Notice)
        .filter(Notice.purpose_id == consent.purpose_id, Notice.is_active.is_(True))
        .first()
    )
    current_notice_version = next((v for v in notice.versions if v.is_current), None) if notice else None
    notice_version_id = current_notice_version.id if current_notice_version else None
    notice_hash = current_notice_version.content_hash if current_notice_version else None
    content_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    details: dict = {
        **(extra_metadata or {}),
        "purpose_code": consent.purpose.code,
        "data_category_code": consent.data_category.code,
        "processing_activity_code": consent.processing_activity.code,
        **({"affirmative_reference": affirmative_reference} if affirmative_reference else {}),
        # interaction_step is the client's self-reported click depth for the
        # action that produced this evidence row (e.g. 1 for a single-click
        # grant/withdraw toggle). Stored in `details` rather than as a column
        # since it is comparative/analytical, not evidentiary, and this task
        # takes no migration - see click-count comparison in the task report.
        **({"interaction_step": ctx.interaction_step} if ctx.interaction_step is not None else {}),
        **({"claimed_notice_version": ctx.notice_version} if ctx.notice_version is not None else {}),
    }
    # ctx.ip_address / ctx.user_agent are whatever the caller's request body
    # claimed - never trustworthy as fact (a reviewer proved a fabricated IP
    # and UA are accepted verbatim if stored directly). They are kept here,
    # clearly namespaced as "claimed", purely as a secondary signal (e.g. to
    # flag a claimed IP that disagrees with the server-observed one); the
    # authoritative ip_address/user_agent columns below are always populated
    # from the server's own view of the request instead.
    if ctx.ip_address:
        details["claimed_ip_address"] = ctx.ip_address
    if ctx.user_agent:
        details["claimed_user_agent"] = ctx.user_agent
    # Same "claimed vs server-observed" split as ip_address/user_agent
    # above: ctx.gpc_signal is whatever the client's request body claims,
    # kept here only as a secondary signal; gpc_signal (the parameter,
    # below) is the server's own read of the Sec-GPC request header and is
    # what actually gets stored as fact on the evidence row's own column.
    if ctx.gpc_signal is not None:
        details["claimed_gpc_signal"] = ctx.gpc_signal
    evidence = ConsentEvidence(
        consent_id=consent.id,
        evidence_ref=f"EV-{uuid.uuid4().hex[:16].upper()}",
        collected_at=utcnow(),
        collected_by=collected_by,
        collection_method=collection_method,
        source_app=source_app,
        tenant_id=consent.tenant_id,
        consent_text=text,
        consent_version=consent.consent_version,
        purpose_version=pv.version_number,
        policy_version=consent.policy_version.version_number if consent.policy_version else None,
        request_id=request_id,
        notice_version_id=notice_version_id,
        notice_hash=notice_hash,
        language=ctx.language,
        ip_address=ip_address,
        user_agent=user_agent,
        session_id=ctx.session_id,
        ui_control_id=ctx.ui_control_id,
        banner_version=ctx.banner_version,
        screen_id=ctx.screen_id,
        affirmative_action=ctx.affirmative_action,
        content_hash=content_hash,
        # HMAC-SHA256 over (content_hash, notice_hash), keyed by server
        # secret material (see app.core.encryption.hmac_signature) - not a
        # digital signature in the asymmetric sense, but it makes tampering
        # with either hash after the fact (e.g. a direct DB edit bypassing
        # the ORM) detectable by recomputing and comparing, which is what
        # this column was always meant to assert and never did (it was
        # written but never populated).
        signature=hmac_signature(content_hash, notice_hash),
        gpc_signal=gpc_signal,
        details=details,
    )
    db.add(evidence)
    db.flush()
    return evidence


def _apply_status_timestamps(consent: Consent, to_status: str, now: datetime) -> None:
    if to_status == "GRANTED" or to_status == "ACTIVE":
        consent.granted_at = consent.granted_at or now
        consent.denied_at = None
        consent.withdrawn_at = None
    elif to_status == "DENIED":
        consent.denied_at = now
        consent.withdrawn_at = None
    elif to_status == "WITHDRAWN":
        consent.withdrawn_at = now
    elif to_status == "RENEWED":
        consent.renewed_at = now
    elif to_status == "REQUESTED":
        consent.requested_at = now


def request_consent(
    db: Session,
    consent: Consent,
    *,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
) -> Consent:
    _validate_transition(consent.status, "REQUESTED", "request")
    from_status = consent.status
    consent.status = "REQUESTED"
    _apply_status_timestamps(consent, "REQUESTED", utcnow())
    _record_transition(db, consent, action="CONSENT_REQUESTED", to_status="REQUESTED", from_status=from_status, reason=reason or "Consent requested from data principal",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       actor_type=actor_type, actor_id=actor_id)
    db.commit()
    db.refresh(consent)
    return consent


def grant_consent(
    db: Session,
    consent: Consent,
    *,
    expires_in_days: Optional[int] = None,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    consent_text: Optional[str] = None,
    request_id: Optional[str] = None,
    client_context: Optional[ClientContext] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    verified_context: Optional[ConsentContext] = None,
    gpc_signal: Optional[bool] = None,
) -> Consent:
    # R1-14/F-01,F-02,F-04 (s.9, Rules 2025 R.10-R.12). BEFORE the transition
    # is validated or anything is mutated: a child's consent cannot be
    # recorded without a verified parental/guardian consent record, and a
    # purpose s.9(3) prohibits cannot be granted for a child at all. This is
    # the choke point every grant path already shares - staff console, portal,
    # CRM cookie banner, integration handoff, re-consent campaign - which is
    # why the check is here and not in five routes. Returns None (and changes
    # nothing) for every principal with no age-assurance record, which is
    # every existing customer. See app/services/guardian.py.
    guardian_evidence = _assert_child_consent_permitted(
        db, consent, action="GRANT", actor_username=actor_username,
        source_app=source_app, request_id=request_id,
    )
    # R2-11/Q-07 - Global Privacy Control. Enforced HERE, at the same choke
    # point the s.9 guard uses and for the same reason: every grant path
    # (portal, CRM/Codex/SkillLearn cookie banner, staff console, integration
    # handoff, re-consent campaign) already passes through this function, and
    # a rule enforced in four routes out of five is a rule with a back door.
    # `gpc_signal` is the SERVER-OBSERVED header only - routes read it from
    # `request.headers`, never from the request body - so a client claiming
    # GPC in `ClientContext.gpc_signal` cannot force this outcome.
    from app.services.gpc import objection_blocks_consent

    if objection_blocks_consent(consent, gpc_signal):
        return _refuse_under_gpc(
            db, consent, action="GRANT", actor_username=actor_username, source_app=source_app,
            collection_method=collection_method, request_id=request_id,
            client_context=client_context, ip_address=ip_address, user_agent=user_agent,
            verified_context=verified_context,
        )
    _validate_transition(consent.status, "GRANTED", "grant")
    from_status = consent.status
    now = utcnow()
    consent.status = "GRANTED"
    consent.granted_at = now
    consent.denied_at = None
    consent.withdrawn_at = None
    consent.renewed_at = None
    consent.requested_at = consent.requested_at or now
    if expires_in_days is not None:
        consent.expires_at = now + timedelta(days=expires_in_days)
    elif consent.purpose and consent.purpose.retention_period_days:
        consent.expires_at = now + timedelta(days=consent.purpose.retention_period_days)
    if consent_text is not None:
        consent.consent_text = consent_text
    _record_transition(db, consent, action="CONSENT_GRANTED", to_status="GRANTED", from_status=from_status, reason=reason or "Consent granted",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       metadata={"expires_at": consent.expires_at.isoformat() if consent.expires_at else None},
                       actor_type=actor_type, actor_id=actor_id, ip_address=ip_address, user_agent=user_agent)
    evidence = _create_evidence(db, consent, collected_by=actor_username, collection_method=collection_method,
                                source_app=source_app, request_id=request_id,
                                extra_metadata={"expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
                                                **(guardian_evidence or {})},
                                client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                                verified_context=verified_context, gpc_signal=gpc_signal)
    consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    # Pin the notice version in force at grant, exactly like purpose_version_id
    # and policy_version_id (R1-04/A-07) - kept equal to this same evidence
    # row's own notice_version_id so Consent and ConsentEvidence never disagree.
    consent.notice_version_id = evidence.notice_version_id
    from app.services.receipts import issue_receipt

    # R1-09/P-01: this is the fresh, affirmative act that a re-consent
    # campaign was waiting for, so the block on processing lifts here and
    # NOWHERE else. Re-pointing a consent at a new purpose version does not
    # clear it, and neither does a staff user editing the row - either would
    # be assuming the consent this machinery exists to obtain (BRD 4.1.3,
    # "consent cannot be assumed"). Best-effort against an already-valid
    # transition: a bookkeeping failure must not fail a grant.
    try:
        from app.services.material_change import clear_re_consent

        clear_re_consent(db, consent, actor_username=actor_username, request_id=request_id)
    except Exception:  # noqa: BLE001 - never let campaign bookkeeping break a grant
        import logging

        logging.getLogger("app.reconsent").exception(
            "Failed to clear re_consent_required for consent_id=%s", consent.id
        )

    issue_receipt(db, consent, evidence, action="GRANTED")
    db.commit()
    db.refresh(consent)
    _notify_consent_acknowledgement(db, consent, actor_username=actor_username, source_app=source_app, request_id=request_id)
    return consent


def _notify_consent_acknowledgement(
    db: Session, consent: Consent, *, actor_username: str, source_app: str, request_id: Optional[str]
) -> None:
    """O-02 trigger: consent acknowledgement, fired from the one choke
    point every grant path (portal, CRM banner, staff console) already
    shares. Never allowed to fail the grant itself - queuing/rendering a
    notification is best-effort against an already-committed consent."""
    try:
        from app.services.notifications import queue_notification

        queue_notification(
            db, customer=consent.customer, event_type="CONSENT_ACKNOWLEDGEMENT", source_app=source_app,
            context={"purpose_name": consent.purpose.name, "purpose_code": consent.purpose.code},
            request_id=request_id, actor_username=actor_username,
        )
    except Exception:  # noqa: BLE001 - never let a notification failure break a consent grant
        import logging

        logging.getLogger("app.notifications").exception(
            "Failed to queue CONSENT_ACKNOWLEDGEMENT notification for consent_id=%s", consent.id
        )


def activate_consent(db: Session, consent: Consent, *, reason: str = "", actor_username: str = "system",
                     source_app: str = "", request_id: Optional[str] = None,
                     actor_type: str = "SYSTEM", actor_id: Optional[str] = None) -> Consent:
    if consent.status == "GRANTED":
        # R1-14: defence in depth. A grant is already gated above, but a
        # consent can sit in GRANTED across a change of circumstance - the
        # principal is re-assessed as a child, or the parental consent is
        # revoked - and promoting it to ACTIVE afterwards would be recording
        # an affirmative consent state that s.9 no longer permits. This is a
        # no-op for every principal who is not a child.
        _assert_child_consent_permitted(
            db, consent, action="ACTIVATE", actor_username=actor_username,
            source_app=source_app, request_id=request_id,
        )
        from_status = consent.status
        consent.status = "ACTIVE"
        _record_transition(db, consent, action="CONSENT_ACTIVATED", to_status="ACTIVE", from_status=from_status,
                           reason=reason or "Consent became active",
                           actor_username=actor_username, source_app=source_app, request_id=request_id,
                           actor_type=actor_type, actor_id=actor_id)
        db.commit()
        db.refresh(consent)
    return consent


def deny_consent(
    db: Session,
    consent: Consent,
    *,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
    client_context: Optional[ClientContext] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    verified_context: Optional[ConsentContext] = None,
    gpc_signal: Optional[bool] = None,
) -> Consent:
    """Record a refusal - and evidence it exactly as a grant is evidenced.

    B-01/B-02 (s.6(10)): a refusal used to write ConsentHistory and an audit
    row and nothing else. It took no ClientContext, so the language, session,
    banner/screen/control id, click depth and server-observed GPC signal behind
    the refusal were all dropped, and no ConsentEvidence row was written at
    all. That made a refusal's record materially weaker than a grant's -
    precisely the asymmetry that makes s.6(10)'s burden of proving a refusal
    impossible to discharge.

    `POST /portal/deny` worked around this with a route-local helper because
    the change that introduced it was barred from editing this file; that
    helper is now gone and its work lives here, which is the layer that owns
    "every transition is evidenced". Keeping it here (rather than in each
    route) is what lets `/portal/deny`, the CRM/Codex/SkillLearn cookie
    banner's `_sync_consent_preferences` and the staff console all produce the
    same evidence for the same act.

    Ordering note: the evidence row is written inside the same transaction as
    the status change and the history row, so a refusal and its evidence are
    committed together or not at all.
    """
    _validate_transition(consent.status, "DENIED", "deny")
    from_status = consent.status
    consent.status = "DENIED"
    consent.denied_at = utcnow()
    consent.granted_at = None
    consent.withdrawn_at = None
    _record_transition(db, consent, action="CONSENT_DENIED", to_status="DENIED", from_status=from_status, reason=reason or "Consent denied",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       actor_type=actor_type, actor_id=actor_id, ip_address=ip_address, user_agent=user_agent)
    evidence = _create_evidence(db, consent, collected_by=actor_username, collection_method=collection_method,
                                source_app=source_app, request_id=request_id,
                                client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                                verified_context=verified_context, gpc_signal=gpc_signal)
    consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    db.commit()
    db.refresh(consent)
    return consent


def withdraw_consent(
    db: Session,
    consent: Consent,
    *,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
    client_context: Optional[ClientContext] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    gpc_signal: Optional[bool] = None,
) -> Consent:
    _validate_transition(consent.status, "WITHDRAWN", "withdraw")
    from_status = consent.status
    consent.status = "WITHDRAWN"
    consent.withdrawn_at = utcnow()
    _record_transition(db, consent, action="CONSENT_WITHDRAWN", to_status="WITHDRAWN", from_status=from_status,
                       reason=reason or "Consent withdrawn by data principal",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       actor_type=actor_type, actor_id=actor_id, ip_address=ip_address, user_agent=user_agent)
    # Withdrawal previously recorded a ConsentHistory row but never a
    # ConsentEvidence row, so the client-reported interaction_step (and every
    # other ClientContext field) was silently dropped for withdrawals - the
    # one place click-depth instrumentation matters most for the
    # grant-vs-withdraw parity requirement. Mirror grant_consent/renew_consent
    # here so withdrawal is evidenced exactly like every other transition.
    evidence = _create_evidence(db, consent, collected_by=actor_username, collection_method=collection_method,
                                source_app=source_app, request_id=request_id,
                                client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                                gpc_signal=gpc_signal)
    consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    db.commit()
    db.refresh(consent)
    _notify_withdrawal_confirmation(db, consent, actor_username=actor_username, source_app=source_app, request_id=request_id)
    _propagate_cease_processing(db, consent, actor_username=actor_username, source_app=source_app, request_id=request_id)
    _raise_erasure_on_withdrawal(db, consent, actor_username=actor_username, source_app=source_app,
                                 request_id=request_id, actor_type=actor_type, actor_id=actor_id)
    return consent


def _raise_erasure_on_withdrawal(
    db: Session, consent: Consent, *, actor_username: str, source_app: str,
    request_id: Optional[str], actor_type: str = "SYSTEM", actor_id: Optional[str] = None,
) -> None:
    """R1-06/s.8(7) trigger: a withdrawal that leaves no lawful basis makes
    erasure due.

    This raises an `erasure_jobs` row; it erases nothing. The row is the
    authorisation and the evidence, and the actual destruction happens only
    after the R.8(2) pre-erasure notice has gone out and its period has
    elapsed (app/services/erasure.py). The principal's own withdrawal supplies
    the authorisation - it is their statutory act, already evidenced by the
    ConsentHistory and ConsentEvidence rows written above - so the job is
    created already authorised rather than waiting for a staff decision the
    Act does not require.

    `erasure_due_after_withdrawal` is what decides: `customers` holds one
    identity row shared across every purpose, so a withdrawal only makes
    erasure due once NO consent remains active. See that function's docstring
    for why that is the faithful reading of s.8(7) at this data model's grain.

    FAIL SOFT, for the same reason `_propagate_cease_processing` above does:
    it runs after the withdrawal's own commit, it performs no network I/O, and
    any failure is caught, rolled back and logged. A withdrawal is the
    principal exercising a right; it must not fail because a downstream duty
    of ours did. `propose_erasure_job` is idempotent per (customer,
    trigger_ref), so the job can be raised again later.
    """
    try:
        from app.services.erasure import erasure_due_after_withdrawal, propose_erasure_job

        due, why = erasure_due_after_withdrawal(db, consent)
        if not due:
            import logging

            logging.getLogger("app.erasure").info(
                "Withdrawal of consent_id=%s does not yet make erasure due: %s", consent.id, why
            )
            return
        propose_erasure_job(
            db, consent.customer, trigger="WITHDRAWAL",
            trigger_ref=f"withdrawal:{consent.id}:v{consent.consent_version}",
            purpose_id=consent.purpose_id,
            reason=f"Consent withdrawn: {why}",
            source_app=source_app or consent.source_app, actor_username=actor_username,
            request_id=request_id,
            authorised_by=actor_username,
            authorisation_basis=(
                f"DPDP Act s.8(7): the data principal withdrew consent "
                f"(consent_id={consent.id} v{consent.consent_version}, actor_type={actor_type}"
                + (f", actor_id={actor_id}" if actor_id else "")
                + f"). {why}"
            ),
        )
    except Exception:  # noqa: BLE001 - never let the erasure trigger break a withdrawal
        import logging

        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - a rollback failure must not mask the original error
            logging.getLogger("app.erasure").exception(
                "Rollback after a failed erasure-job proposal itself failed"
            )
        logging.getLogger("app.erasure").exception(
            "Failed to raise an erasure job for consent_id=%s - the withdrawal itself is "
            "already committed and stands; the job can be raised again later", consent.id
        )


def _propagate_cease_processing(
    db: Session, consent: Consent, *, actor_username: str, source_app: str, request_id: Optional[str]
) -> None:
    """R3-07/C-02 trigger: s.6(6) requires the fiduciary to *cause its
    processors to cease*, not merely to record the withdrawal. This raises one
    tracked, signed instruction per processor we have disclosed this
    principal's data to under the withdrawn purpose.

    FAIL SOFT, DELIBERATELY. Withdrawal is a data principal exercising a
    statutory right; propagation is this fiduciary's own downstream duty. If
    the second fails, the first must still succeed, or a third party's outage
    silently denies people a right the Act guarantees. Three things enforce
    that here:

    * It runs AFTER withdraw_consent's own `db.commit()`, so the withdrawal is
      already durable before a single line of this executes.
    * It performs no network I/O. `raise_cease_processing_alerts` only inserts
      PENDING rows; the HMAC-signed HTTP POST happens later, in
      app/jobs/processor_alert_job.py, with retries and exponential backoff -
      the same queue-then-dispatch shape as the notification service. A dead
      processor endpoint cannot reach this code path at all.
    * Any failure is caught, rolled back and logged. The rollback matters as
      much as the catch: a failed statement (a missing table on a database
      that has not run the R3-07 migration yet, say) leaves the Session in an
      aborted transaction, and swallowing the exception without rolling back
      would poison every subsequent statement on that Session - turning a
      best-effort notification into a 500 for the caller anyway, which is
      exactly the coupling this docstring exists to forbid.

    Nothing is lost by failing here: raise_cease_processing_alerts is
    idempotent per (consent, consent_version), so the alerts can be re-raised
    later, and a cease-processing instruction that was never acknowledged is
    what K-08 and the DPO escalation already exist to surface.
    """
    try:
        from app.services.processors import raise_cease_processing_alerts

        raise_cease_processing_alerts(
            db, consent, actor_username=actor_username, source_app=source_app, request_id=request_id
        )
    except Exception:  # noqa: BLE001 - never let propagation break a withdrawal
        import logging

        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - a rollback failure must not mask the original error
            logging.getLogger("app.processors").exception(
                "Rollback after a failed cease-processing fan-out itself failed"
            )
        logging.getLogger("app.processors").exception(
            "Failed to raise cease-processing alerts for consent_id=%s - the withdrawal itself "
            "is already committed and stands; the alerts can be re-raised later", consent.id
        )


def _notify_withdrawal_confirmation(
    db: Session, consent: Consent, *, actor_username: str, source_app: str, request_id: Optional[str]
) -> None:
    """O-02/C-04 trigger: confirm withdrawal to the principal and explain
    consequences (s.6(5)). Same best-effort, never-fail-the-transition
    pattern as _notify_consent_acknowledgement."""
    try:
        from app.services.notifications import queue_notification

        queue_notification(
            db, customer=consent.customer, event_type="WITHDRAWAL_CONFIRMATION", source_app=source_app,
            context={"purpose_name": consent.purpose.name, "purpose_code": consent.purpose.code},
            request_id=request_id, actor_username=actor_username,
        )
    except Exception:  # noqa: BLE001 - never let a notification failure break a withdrawal
        import logging

        logging.getLogger("app.notifications").exception(
            "Failed to queue WITHDRAWAL_CONFIRMATION notification for consent_id=%s", consent.id
        )


def renew_consent(
    db: Session,
    consent: Consent,
    *,
    expires_in_days: Optional[int] = None,
    reason: str = "",
    actor_username: str = "system",
    source_app: str = "",
    collection_method: str = "UI",
    request_id: Optional[str] = None,
    client_context: Optional[ClientContext] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    verified_context: Optional[ConsentContext] = None,
    # R2-11: the server-observed Sec-GPC header, stamped on the evidence row
    # exactly as grant_consent already stamps it. A renewal is the affirmative
    # act the portal now uses to re-affirm a consent flagged by a material
    # change (routes/portal.py::portal_grant), so its evidence has to be as
    # complete as a grant's - a re-consent recorded with the GPC signal
    # silently dropped would be weaker evidence than the consent it replaces.
    gpc_signal: Optional[bool] = None,
) -> Consent:
    # R1-14: same gate as grant_consent. A renewal re-establishes an
    # affirmative consent for a fresh term, so it needs the s.9(1) parental
    # consent to still be in force at the moment of renewal - a guardian
    # consent revoked last month must not be renewable through this door.
    guardian_evidence = _assert_child_consent_permitted(
        db, consent, action="RENEW", actor_username=actor_username,
        source_app=source_app, request_id=request_id,
    )
    # R2-11/Q-07 - same GPC gate as grant_consent, for the same reason the
    # s.9 gate above is repeated here: a renewal is itself a fresh affirmative
    # act (the portal re-affirms a material-change-flagged consent through
    # this door), so an objection carried by the renewing request blocks it
    # exactly as it blocks a first grant. `clear_re_consent` is therefore
    # never reached, and the decision engine keeps refusing on
    # `re_consent_required` - which is the correct end state: the principal
    # has not re-consented.
    from app.services.gpc import objection_blocks_consent

    if objection_blocks_consent(consent, gpc_signal):
        return _refuse_under_gpc(
            db, consent, action="RENEW", actor_username=actor_username, source_app=source_app,
            collection_method=collection_method, request_id=request_id,
            client_context=client_context, ip_address=ip_address, user_agent=user_agent,
            verified_context=verified_context,
        )
    _validate_transition(consent.status, "RENEWED", "renew")
    from_status = consent.status
    now = utcnow()
    consent.status = "RENEWED"
    consent.renewed_at = now
    consent.granted_at = now
    consent.withdrawn_at = None
    consent.denied_at = None
    if expires_in_days is not None:
        consent.expires_at = now + timedelta(days=expires_in_days)
    elif consent.purpose and consent.purpose.retention_period_days:
        consent.expires_at = now + timedelta(days=consent.purpose.retention_period_days)
    consent.consent_version += 1
    _record_transition(db, consent, action="CONSENT_RENEWED", to_status="RENEWED", from_status=from_status,
                       reason=reason or "Consent renewed", actor_username=actor_username,
                       source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       metadata={"new_expires_at": consent.expires_at.isoformat() if consent.expires_at else None},
                       actor_type=actor_type, actor_id=actor_id, ip_address=ip_address, user_agent=user_agent)
    evidence = _create_evidence(db, consent, collected_by=actor_username, collection_method=collection_method,
                                source_app=source_app, request_id=request_id,
                                extra_metadata={"renewed": True,
                                                "expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
                                                **(guardian_evidence or {})},
                                client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                                verified_context=verified_context, gpc_signal=gpc_signal)
    consent.history[-1].details["evidence_ref"] = evidence.evidence_ref
    consent.notice_version_id = evidence.notice_version_id
    from app.services.receipts import issue_receipt

    # R1-09/P-01: this is the fresh, affirmative act that a re-consent
    # campaign was waiting for, so the block on processing lifts here and
    # NOWHERE else. Re-pointing a consent at a new purpose version does not
    # clear it, and neither does a staff user editing the row - either would
    # be assuming the consent this machinery exists to obtain (BRD 4.1.3,
    # "consent cannot be assumed"). Best-effort against an already-valid
    # transition: a bookkeeping failure must not fail a grant.
    try:
        from app.services.material_change import clear_re_consent

        clear_re_consent(db, consent, actor_username=actor_username, request_id=request_id)
    except Exception:  # noqa: BLE001 - never let campaign bookkeeping break a renewal
        import logging

        logging.getLogger("app.reconsent").exception(
            "Failed to clear re_consent_required for consent_id=%s", consent.id
        )

    issue_receipt(db, consent, evidence, action="RENEWED")
    db.commit()
    db.refresh(consent)
    return consent


def expire_consents(db: Session, *, actor_username: str = "system", source_app: str = "SYSTEM") -> int:
    now = utcnow()
    expiring = (
        db.query(Consent)
        .filter(Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]))
        .all()
    )
    count = 0
    for consent in expiring:
        if consent.expires_at is not None and consent.expires_at <= now:
            from_status = consent.status
            consent.status = "EXPIRED"
            _record_transition(db, consent, action="CONSENT_EXPIRED", to_status="EXPIRED", from_status=from_status,
                               reason="Consent expiry date reached", actor_username=actor_username,
                               source_app=source_app,
                               policy_version_id=consent.policy_version_id,
                               policy_version_number=consent.policy_version.version_number if consent.policy_version else None)
            db.commit()
            count += 1
    return count


def update_consent_for_purpose_version(
    db: Session, consent: Consent, new_pv: PurposeVersion, *, reason: str = "",
    actor_username: str = "system", source_app: str = "", request_id: Optional[str] = None,
) -> Consent:
    """Re-associate consent with a new purpose version and mark UPDATED."""
    if consent.status not in ["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]:
        return consent
    from_status = consent.status
    consent.purpose_version_id = new_pv.id
    consent.consent_text = new_pv.consent_text or consent.consent_text
    consent.status = "UPDATED"
    _record_transition(db, consent, action="CONSENT_UPDATED", to_status="UPDATED", from_status=from_status,
                       reason=reason or f"Purpose version updated to v{new_pv.version_number}",
                       actor_username=actor_username, source_app=source_app, request_id=request_id,
                       policy_version_id=consent.policy_version_id,
                       policy_version_number=consent.policy_version.version_number if consent.policy_version else None,
                       metadata={"purpose_version": new_pv.version_number})
    db.commit()
    db.refresh(consent)
    try:
        from app.services.notifications import queue_notification

        queue_notification(
            db, customer=consent.customer, event_type="PURPOSE_CHANGE_RECONSENT", source_app=source_app,
            context={"purpose_name": consent.purpose.name, "purpose_code": consent.purpose.code,
                     "new_purpose_version": new_pv.version_number},
            request_id=request_id, actor_username=actor_username,
        )
    except Exception:  # noqa: BLE001 - never let a notification failure break the update
        import logging

        logging.getLogger("app.notifications").exception(
            "Failed to queue PURPOSE_CHANGE_RECONSENT notification for consent_id=%s", consent.id
        )
    return consent
