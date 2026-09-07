"""R3-08 (I-01, I-02, I-03, I-04, H-12): breach register, notice/report
generators, the three statutory clocks, and K-39..K-41.

Everything statutory in this module is a citation, not a preference:

| Obligation                                   | Start        | Window        | Provision |
|----------------------------------------------|--------------|---------------|-----------|
| Intimate each affected Data Principal        | becoming aware | without delay | R.7(1)(a)-(e) |
| Intimate the Board (initial description)     | becoming aware | without delay | R.7(2)(a) |
| File the detailed report with the Board      | becoming aware | 72 hours*     | R.7(2)(b)(i)-(vi) |
| Report the incident to CERT-In               | noticing it  | 6 hours       | CERT-In Directions No. 20(3)/2022-CERT-In, 28 Apr 2022, under s.70B(6) IT Act |

\\* "or within such longer period as the Board may allow on a request made in
writing in this behalf" - R.7(2)(b). That written request and the Board's
answer are `breach_extension_requests`; only a GRANTED one moves the deadline.

Rule numbering is the notified Gazette text (DPDP Rules, 2025, G.S.R. 846(E),
13 Nov 2025), matching `DPDP_COMPLIANCE_GAP_ANALYSIS.md` - see its §1.2, which
warns that the study PDF and many web summaries number the rules differently.

**Every clock starts at `aware_at`, never `detected_at`.** Rule 7 says "on
becoming aware"; the CERT-In direction says "within 6 hours of noticing such
incidents or being brought to notice about such incidents". Detection is the
alert; awareness is the triage decision that the alert is a personal data
breach. `app/models/breach.py` keeps them as separate columns and a CHECK
constraint stops `aware_at` preceding `detected_at`; the gap between them is
itself reported (K-39's triage lag), because a fiduciary that sits on an
alert for a week cannot launder that delay by declaring awareness late.

**"Without delay" gets no invented deadline.** R.7(1) and R.7(2)(a) state a
standard, not a period. `deadline_at` on those filings is therefore NULL and
`target_at` carries a purely internal operational target (default 24 h,
overridable per tenant via `Organization.settings["breach_without_delay_target_hours"]`).
Every report says which of the two it measured against, so nothing here ever
presents an internal SLA as if it were the law.

Dispatch reuses `app/services/notifications.py` - `queue_notification` for
principals, `queue_operational_notification` for a regulator mailbox - so
retries, backoff and the K-44/K-45 delivery metrics are the same ones the rest
of the platform uses. There is no second sender in this module.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.models.breach import (
    BREACH_TRANSITIONS,
    Breach,
    BreachAffectedPrincipal,
    BreachExtensionRequest,
    BreachNotification,
)
from app.models.entities import Customer, Notification, Organization
from app.services.audit import log_audit
from app.services.notifications import queue_notification, queue_operational_notification
from app.services.tenancy import resolve_customer, resolve_tenant_id

logger = logging.getLogger("app.breach")


# ---------------------------------------------------------------------------
# Statutory constants. Change one of these only with a rule amendment in hand.
# ---------------------------------------------------------------------------

CERT_IN_DEADLINE_HOURS = 6
BOARD_DETAILED_DEADLINE_HOURS = 72
DEFAULT_WITHOUT_DELAY_TARGET_HOURS = 24  # internal target only - NOT a legal period

BASIS_PRINCIPAL = (
    "DPDP Rules 2025, R.7(1): intimate each affected Data Principal without delay "
    "(no fixed period prescribed)"
)
BASIS_BOARD_INITIAL = (
    "DPDP Rules 2025, R.7(2)(a): intimate the Board without delay "
    "(no fixed period prescribed)"
)
BASIS_BOARD_DETAILED = (
    "DPDP Rules 2025, R.7(2)(b): detailed report within 72 hours of becoming aware, "
    "or such longer period as the Board may allow on a written request"
)
BASIS_CERT_IN = (
    "CERT-In Directions No. 20(3)/2022-CERT-In of 28 April 2022 under s.70B(6) IT Act: "
    "report a listed cyber incident (Annexure I includes data breach and data leak) "
    "within 6 hours of noticing it"
)

# The Rule 7(1) content items, keyed by the payload field that carries each.
PRINCIPAL_NOTICE_CLAUSES: dict[str, str] = {
    "nature_extent_and_timing": "R.7(1)(a) nature, extent and timing of occurrence",
    "likely_consequences": "R.7(1)(b) consequences relevant to the Data Principal",
    "mitigation_measures": "R.7(1)(c) measures implemented and being implemented to mitigate risk",
    "safety_measures": "R.7(1)(d) safety measures the Data Principal may take",
    "contact": "R.7(1)(e) business contact information of a person able to respond",
}

# The Rule 7(2)(a) content items for the Board's initial intimation.
BOARD_INITIAL_CLAUSES: dict[str, str] = {
    "nature": "R.7(2)(a) nature of the breach",
    "extent": "R.7(2)(a) extent of the breach",
    "timing_of_occurrence": "R.7(2)(a) timing of occurrence",
    "location_of_occurrence": "R.7(2)(a) location of occurrence",
    "likely_impact": "R.7(2)(a) likely impact",
}

# The six mandated sections of the 72-hour report, in the Rules' own order.
BOARD_DETAILED_SECTIONS: dict[str, str] = {
    "updated_description": "R.7(2)(b)(i) updated and detailed information in respect of clause (a)",
    "events_and_reasons": "R.7(2)(b)(ii) broad facts related to the events, circumstances and reasons leading to the breach",
    "mitigation_measures": "R.7(2)(b)(iii) measures implemented and being implemented to mitigate risk",
    "findings_on_actor": "R.7(2)(b)(iv) findings regarding the person who caused the breach",
    "remedial_measures": "R.7(2)(b)(v) remedial measures taken to prevent recurrence",
    "principal_intimations_report": "R.7(2)(b)(vi) report regarding the intimations given to affected Data Principals",
}

# CERT-In's incident report has no clause-numbered statutory content list the
# way Rule 7 does - the direction requires reporting "cyber incidents as
# mentioned in Annexure I" and CERT-In's own form asks for these fields. They
# are therefore labelled as the form's fields, not as mandated clauses.
CERT_IN_SECTIONS: dict[str, str] = {
    "incident_category": "Incident category (Annexure I)",
    "noticed_at": "Time of noticing the incident (start of the 6-hour window)",
    "description": "Description of the incident",
    "location_of_occurrence": "Location of occurrence",
    "likely_impact": "Likely impact",
    "mitigation_measures": "Measures taken and being taken",
    "point_of_contact": "Point of contact",
}


class BreachError(ValueError):
    """A refusal the API surfaces as 422 - an invalid transition, a report
    missing a mandated section, a clock started twice."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """Postgres TIMESTAMPTZ round-trips in the session timezone, not
    necessarily UTC (the same reason `app/core/audit_chain.py::_canonical`
    normalises). Every arithmetic and hashing path here goes through this."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    normalised = _utc(value)
    return normalised.isoformat() if normalised else None


def _hours_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    start, end = _utc(start), _utc(end)
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 3600.0, 3)


# ---------------------------------------------------------------------------
# Content hashing - the same scheme as the audit ledger, deliberately
# ---------------------------------------------------------------------------

def _canonical(payload: Any) -> Any:
    """Recursively normalise a payload so the same notice always canonicalises
    to the same bytes: datetimes to UTC ISO-8601, mappings to plain dicts
    (json.dumps sorts the keys), sequences preserved in order."""
    if isinstance(payload, datetime):
        return _iso(payload)
    if isinstance(payload, dict):
        return {str(k): _canonical(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_canonical(v) for v in payload]
    return payload


def canonical_json(payload: Any) -> str:
    """Identical canonicalisation to `app/core/audit_chain.py::_canonical`:
    `json.dumps(..., sort_keys=True, default=str)` over a UTC-normalised
    structure. One hashing scheme for the whole platform, not two."""
    return json.dumps(_canonical(payload), sort_keys=True, default=str)


def content_hash(payload: Any) -> str:
    """SHA-256 over the canonical JSON of a filing. This is what makes a sent
    notice provable later: re-canonicalise the stored payload, re-hash, and
    compare with the `content_hash` recorded at generation time."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def verify_notification_hash(notification: BreachNotification) -> dict:
    recomputed = content_hash(notification.payload or {})
    return {
        "breach_notification_id": notification.id,
        "recorded_hash": notification.content_hash,
        "recomputed_hash": recomputed,
        "matches": bool(notification.content_hash) and recomputed == notification.content_hash,
        "algorithm": "sha256(canonical_json(payload)) - same canonicalisation as app/core/audit_chain.py",
    }


# ---------------------------------------------------------------------------
# Tenant configuration
# ---------------------------------------------------------------------------

def _organization(db: Session, tenant_id: Optional[int]) -> Optional[Organization]:
    if tenant_id is None:
        return None
    return db.query(Organization).filter(Organization.id == tenant_id).first()


def _tenant_setting(org: Optional[Organization], key: str, default: Any = "") -> Any:
    if org is None:
        return default
    value = (org.settings or {}).get(key)
    return default if value in (None, "") else value


def without_delay_target_hours(db: Session, breach: Breach) -> int:
    """The internal operational target applied to "without delay". Never a
    legal deadline - see the module docstring."""
    org = _organization(db, breach.tenant_id)
    try:
        return int(_tenant_setting(org, "breach_without_delay_target_hours",
                                   DEFAULT_WITHOUT_DELAY_TARGET_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_WITHOUT_DELAY_TARGET_HOURS


def board_recipient(db: Session, breach: Breach) -> str:
    """Address the Board's intimations are emailed to, if the tenant has
    configured one. The Board has a portal, not an API: with no address
    configured the filing is generated, hashed and held, and the operator
    records the real filing reference through `record_filing`."""
    return str(_tenant_setting(_organization(db, breach.tenant_id), "board_notification_email", ""))


def cert_in_recipient(db: Session, breach: Breach) -> str:
    return str(_tenant_setting(_organization(db, breach.tenant_id), "cert_in_notification_email", ""))


# ---------------------------------------------------------------------------
# Register lifecycle
# ---------------------------------------------------------------------------

def _next_breach_ref(db: Session) -> str:
    """BR-<year>-<seq>, sequential across the whole platform rather than per
    tenant: `breach_ref` is globally unique (it is the URL key), so two
    tenants must not both mint BR-2026-0001."""
    year = utcnow().year
    prefix = f"BR-{year}-"
    count = db.query(Breach).filter(Breach.breach_ref.like(f"{prefix}%")).count()
    return f"{prefix}{count + 1:04d}"


def register_breach(
    db: Session,
    *,
    title: str,
    source_app: str,
    detected_at: Optional[datetime] = None,
    occurred_at: Optional[datetime] = None,
    aware_at: Optional[datetime] = None,
    severity: str = "MEDIUM",
    nature: str = "",
    extent: str = "",
    location: str = "",
    likely_impact: str = "",
    likely_consequences: str = "",
    mitigation_measures: str = "",
    safety_measures: str = "",
    cause: str = "",
    cert_in_reportable: bool = True,
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> Breach:
    tenant_id = resolve_tenant_id(db, source_app)
    org = _organization(db, tenant_id)
    detected = _utc(detected_at) or utcnow()
    aware = _utc(aware_at)
    if aware and aware < detected:
        raise BreachError(
            "aware_at cannot precede detected_at: awareness is the triage decision that "
            "follows detection, and every statutory clock starts from it."
        )

    breach = Breach(
        tenant_id=tenant_id,
        breach_ref=_next_breach_ref(db),
        source_app=source_app,
        title=title,
        status="DETECTED",
        severity=severity,
        occurred_at=_utc(occurred_at),
        detected_at=detected,
        aware_at=aware,
        nature=nature,
        extent=extent,
        location=location,
        likely_impact=likely_impact,
        likely_consequences=likely_consequences,
        mitigation_measures=mitigation_measures,
        safety_measures=safety_measures,
        cause=cause,
        # R.7(1)(e) defaults to the tenant's published DPO contact, so a
        # notice can never go out with nobody able to respond to it.
        contact_name=(org.dpo_name if org else "") or "",
        contact_email=(org.dpo_email if org else "") or "",
        contact_phone=(org.dpo_phone if org else "") or "",
        cert_in_reportable=cert_in_reportable,
        created_by=actor_username,
    )
    db.add(breach)
    db.flush()
    log_audit(
        db, "BREACH_REGISTERED", actor_username=actor_username, actor_type="USER",
        source_app=source_app, tenant_id=tenant_id,
        reason=f"Personal data breach {breach.breach_ref} registered: {title}",
        request_id=request_id,
        metadata={
            "breach_id": breach.id, "breach_ref": breach.breach_ref, "severity": severity,
            "detected_at": _iso(detected), "aware_at": _iso(aware),
        },
        commit=False,
    )
    db.commit()
    db.refresh(breach)
    return breach


_UPDATABLE_FIELDS = (
    "title", "severity", "nature", "extent", "location", "likely_impact",
    "likely_consequences", "mitigation_measures", "safety_measures", "cause",
    "findings_on_actor", "remedial_measures", "contact_name", "contact_email",
    "contact_phone", "cert_in_reportable", "cert_in_not_reportable_reason",
    "occurred_at",
)


def update_breach(
    db: Session, breach: Breach, changes: dict, *, actor_username: str = "system",
    request_id: Optional[str] = None,
) -> Breach:
    applied = {}
    for field, value in changes.items():
        if field not in _UPDATABLE_FIELDS or value is None:
            continue
        if field == "occurred_at":
            value = _utc(value)
        setattr(breach, field, value)
        applied[field] = _iso(value) if isinstance(value, datetime) else value
    if not applied:
        return breach
    log_audit(
        db, "BREACH_UPDATED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=f"Breach {breach.breach_ref} updated: {', '.join(sorted(applied))}",
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref, "fields": sorted(applied)},
        commit=False,
    )
    db.commit()
    db.refresh(breach)
    return breach


def mark_aware(
    db: Session, breach: Breach, *, aware_at: Optional[datetime] = None,
    actor_username: str = "system", request_id: Optional[str] = None,
) -> Breach:
    """Record the moment the fiduciary became aware this is a personal data
    breach. This is the single event that starts all three clocks, so it is a
    deliberate, audited act and not a side effect of editing a field."""
    if breach.aware_at is not None:
        raise BreachError(
            f"Breach {breach.breach_ref} is already marked aware at {_iso(breach.aware_at)}. "
            "Awareness starts the statutory clocks and cannot be restated - a correction "
            "must be made as a recorded amendment, not by moving the clock."
        )
    aware = _utc(aware_at) or utcnow()
    detected = _utc(breach.detected_at)
    if detected and aware < detected:
        raise BreachError("aware_at cannot precede detected_at")
    breach.aware_at = aware
    if breach.status == "DETECTED":
        breach.status = "CLASSIFIED"
    log_audit(
        db, "BREACH_AWARENESS_RECORDED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: awareness recorded at {_iso(aware)}; "
            f"CERT-In 6h due {_iso(aware + timedelta(hours=CERT_IN_DEADLINE_HOURS))}, "
            f"Board detailed report due {_iso(aware + timedelta(hours=BOARD_DETAILED_DEADLINE_HOURS))}"
        ),
        request_id=request_id,
        metadata={
            "breach_id": breach.id, "breach_ref": breach.breach_ref,
            "aware_at": _iso(aware),
            "triage_lag_hours": _hours_between(detected, aware),
        },
        commit=False,
    )
    db.commit()
    db.refresh(breach)
    return breach


def transition_breach(
    db: Session, breach: Breach, new_status: str, *, note: str = "",
    actor_username: str = "system", request_id: Optional[str] = None,
) -> Breach:
    old_status = breach.status
    allowed = BREACH_TRANSITIONS.get(old_status, [])
    if new_status not in allowed:
        raise BreachError(
            f"Invalid breach transition {old_status} -> {new_status}. "
            f"Allowed from {old_status}: {allowed or 'none (terminal)'}"
        )
    if new_status == "CLOSED":
        outstanding = outstanding_obligations(db, breach)
        if outstanding:
            raise BreachError(
                "Cannot close a breach with outstanding statutory obligations: "
                + "; ".join(outstanding)
            )
        breach.closed_at = utcnow()
        breach.closure_note = note
    breach.status = new_status
    log_audit(
        db, "BREACH_STATUS_CHANGED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        old_status=old_status, new_status=new_status,
        reason=f"Breach {breach.breach_ref}: {old_status} -> {new_status}. {note}".strip(),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref},
        commit=False,
    )
    db.commit()
    db.refresh(breach)
    return breach


def outstanding_obligations(db: Session, breach: Breach) -> list[str]:
    """What still has to happen before this breach can be closed. Closure is
    the assertion that every Rule 7 duty was discharged, so it is refused
    while any of them is open."""
    problems: list[str] = []
    if breach.aware_at is None:
        problems.append("awareness has not been recorded (R.7: every clock starts on becoming aware)")
        return problems
    if breach.scope_finalised_at is None:
        problems.append("the affected-principal list has not been finalised (R.7(1))")
    filings = {(n.recipient_type, n.stage): n for n in breach.notifications
               if n.recipient_type != "PRINCIPAL"}
    board_initial = filings.get(("BOARD", "INITIAL"))
    if not board_initial or board_initial.status not in ("SENT", "DELIVERED"):
        problems.append("the Board initial intimation has not been sent (R.7(2)(a))")
    board_detailed = filings.get(("BOARD", "DETAILED"))
    if not board_detailed or board_detailed.status not in ("SENT", "DELIVERED"):
        problems.append("the Board 72-hour detailed report has not been filed (R.7(2)(b))")
    if breach.cert_in_reportable:
        cert = filings.get(("CERT_IN", "INITIAL"))
        if not cert or cert.status not in ("SENT", "DELIVERED"):
            problems.append("the CERT-In 6-hour report has not been filed (CERT-In Directions 2022)")
    pending = [
        n for n in breach.notifications
        if n.recipient_type == "PRINCIPAL" and n.status not in ("SENT", "DELIVERED")
    ]
    notified_ids = {n.customer_id for n in breach.notifications if n.recipient_type == "PRINCIPAL"}
    un_notified = [a for a in breach.affected if a.customer_id not in notified_ids]
    if un_notified:
        problems.append(f"{len(un_notified)} affected principal(s) have no notice (R.7(1))")
    if pending:
        problems.append(f"{len(pending)} principal notice(s) not yet sent (R.7(1))")
    return problems


# ---------------------------------------------------------------------------
# Affected-principal scoping (R.7(1))
# ---------------------------------------------------------------------------

def add_affected_principals(
    db: Session, breach: Breach, *, external_ids: Iterable[str], data_involved: str = "",
    actor_username: str = "system", request_id: Optional[str] = None,
) -> dict:
    """Add principals to the breach's scope by external id, resolved through
    `app/services/tenancy.py::resolve_customer` so the lookup is tenant-scoped
    (an external id belonging to a different tenant is simply not found, never
    silently pulled into this tenant's breach)."""
    added, already, unknown = [], [], []
    for external_id in external_ids:
        customer = resolve_customer(db, source_app=breach.source_app, external_id=external_id)
        if customer is None:
            unknown.append(external_id)
            continue
        existing = (
            db.query(BreachAffectedPrincipal)
            .filter(
                BreachAffectedPrincipal.breach_id == breach.id,
                BreachAffectedPrincipal.customer_id == customer.id,
            )
            .first()
        )
        if existing:
            already.append(external_id)
            continue
        db.add(BreachAffectedPrincipal(
            breach_id=breach.id,
            tenant_id=breach.tenant_id,
            customer_id=customer.id,
            data_involved=data_involved,
            added_by=actor_username,
        ))
        added.append(external_id)
    db.flush()
    breach.affected_count = (
        db.query(BreachAffectedPrincipal)
        .filter(BreachAffectedPrincipal.breach_id == breach.id)
        .count()
    )
    log_audit(
        db, "BREACH_SCOPE_UPDATED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: {len(added)} principal(s) added to scope "
            f"(total {breach.affected_count})"
        ),
        request_id=request_id,
        metadata={
            "breach_id": breach.id, "breach_ref": breach.breach_ref,
            "added": len(added), "already_present": len(already), "unresolved": len(unknown),
            # External ids are the principals' own identifiers: counted, never
            # written into the ledger's metadata.
        },
        commit=False,
    )
    db.commit()
    db.refresh(breach)
    return {
        "added": len(added), "already_present": len(already),
        "unresolved": unknown, "affected_count": breach.affected_count,
    }


def finalise_scope(
    db: Session, breach: Breach, *, actor_username: str = "system",
    request_id: Optional[str] = None,
) -> Breach:
    """Assert the affected-principal list is complete. Until this happens,
    "every affected principal has been notified" is not a claim this platform
    can make, and the principal clock reports SCOPING rather than a false
    ON_TIME."""
    if breach.scope_finalised_at is not None:
        return breach
    breach.scope_finalised_at = utcnow()
    log_audit(
        db, "BREACH_SCOPE_FINALISED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: affected-principal scope finalised at "
            f"{breach.affected_count} principal(s)"
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref,
                  "affected_count": breach.affected_count},
        commit=False,
    )
    db.commit()
    db.refresh(breach)
    return breach


# ---------------------------------------------------------------------------
# Notice and report generators
# ---------------------------------------------------------------------------

def _sentence(text: str) -> str:
    """Join narrative fields into prose without producing "exported.. Extent:"
    - operators type their own full stops and the template adds one too."""
    text = (text or "").strip()
    if not text:
        return ""
    return text if text[-1] in ".!?" else text + "."


def _timing_statement(breach: Breach) -> str:
    occurred = _iso(breach.occurred_at)
    return (
        f"Occurred (best known): {occurred}. " if occurred
        else "Time of occurrence not yet established. "
    ) + f"Detected: {_iso(breach.detected_at)}. Fiduciary became aware: {_iso(breach.aware_at)}."


def principal_notice_payload(db: Session, breach: Breach, customer: Customer,
                             affected: Optional[BreachAffectedPrincipal] = None) -> dict:
    """The five mandated contents of R.7(1), as structured data. The prose the
    principal reads is rendered from this; the hash is taken over this."""
    contact_bits = [b for b in (breach.contact_name, breach.contact_email, breach.contact_phone) if b]
    data_involved = (affected.data_involved if affected else "") or breach.extent
    return {
        "notice_type": "PRINCIPAL_BREACH_NOTICE",
        "provision": "DPDP Rules 2025, Rule 7(1)(a)-(e)",
        "breach_ref": breach.breach_ref,
        "issued_at": _iso(utcnow()),
        "data_principal": {"external_id": customer.external_id, "name": customer.name},
        # (a) description of the breach including nature, extent and timing
        "nature_extent_and_timing": (
            f"Nature: {_sentence(breach.nature)} Extent: {_sentence(breach.extent)} "
            f"Personal data of yours involved: {_sentence(data_involved)} "
            f"{_timing_statement(breach)}"
        ).strip(),
        # (b) consequences relevant to her, likely to arise from the breach
        "likely_consequences": breach.likely_consequences,
        # (c) measures implemented and being implemented to mitigate risk
        "mitigation_measures": breach.mitigation_measures,
        # (d) safety measures she may take to protect her interests
        "safety_measures": breach.safety_measures,
        # (e) business contact information of a person able to respond
        "contact": ", ".join(contact_bits),
    }


def render_principal_notice(payload: dict) -> str:
    return (
        "We are writing to inform you of a personal data breach affecting your personal data. "
        "This notice is given under Rule 7(1) of the Digital Personal Data Protection Rules, 2025.\n\n"
        f"1. What happened (nature, extent and timing): {payload['nature_extent_and_timing']}\n\n"
        f"2. Likely consequences for you: {payload['likely_consequences']}\n\n"
        f"3. What we have done and are doing to mitigate the risk: {payload['mitigation_measures']}\n\n"
        f"4. Steps you can take to protect your interests: {payload['safety_measures']}\n\n"
        f"5. Who to contact for any question about this breach: {payload['contact']}\n\n"
        f"Our reference for this incident is {payload['breach_ref']}."
    )


def board_initial_payload(db: Session, breach: Breach) -> dict:
    """R.7(2)(a): description of the breach - nature, extent, timing AND
    location of occurrence - and the likely impact."""
    return {
        "report_type": "BOARD_INITIAL_INTIMATION",
        "provision": "DPDP Rules 2025, Rule 7(2)(a)",
        "breach_ref": breach.breach_ref,
        "issued_at": _iso(utcnow()),
        "data_fiduciary": {
            "tenant_code": breach.source_app,
            "contact_name": breach.contact_name,
            "contact_email": breach.contact_email,
            "contact_phone": breach.contact_phone,
        },
        "became_aware_at": _iso(breach.aware_at),
        "nature": breach.nature,
        "extent": breach.extent,
        "timing_of_occurrence": _timing_statement(breach),
        "location_of_occurrence": breach.location,
        "likely_impact": breach.likely_impact,
        "affected_principals_known_so_far": breach.affected_count,
    }


def principal_intimations_report(db: Session, breach: Breach) -> dict:
    """R.7(2)(b)(vi): the report on intimations given to affected principals.
    Computed from the notification rows, never typed in - the whole point of
    section (vi) is that it is an account of what actually happened."""
    rows = [n for n in breach.notifications if n.recipient_type == "PRINCIPAL"]
    sent = [n for n in rows if n.sent_at]
    delivered = [n for n in rows if n.delivered_at]
    first_sent = min((_utc(n.sent_at) for n in sent), default=None)
    last_sent = max((_utc(n.sent_at) for n in sent), default=None)
    channels: dict[str, int] = {}
    for n in rows:
        channels[n.channel] = channels.get(n.channel, 0) + 1
    return {
        "affected_principals": breach.affected_count,
        "scope_finalised_at": _iso(breach.scope_finalised_at),
        "notices_generated": len(rows),
        "notices_sent": len(sent),
        "notices_delivered": len(delivered),
        "notices_failed": len([n for n in rows if n.status == "FAILED"]),
        "channels": channels,
        "first_notice_sent_at": _iso(first_sent),
        "last_notice_sent_at": _iso(last_sent),
        "hours_from_awareness_to_first_notice": _hours_between(breach.aware_at, first_sent),
        "hours_from_awareness_to_last_notice": _hours_between(breach.aware_at, last_sent),
        "notice_content_hashes": sorted(n.content_hash for n in rows if n.content_hash),
    }


def board_detailed_payload(db: Session, breach: Breach) -> dict:
    """R.7(2)(b)(i)-(vi): the 72-hour detailed report, six sections, in the
    Rules' own order."""
    return {
        "report_type": "BOARD_DETAILED_REPORT",
        "provision": "DPDP Rules 2025, Rule 7(2)(b)(i)-(vi)",
        "breach_ref": breach.breach_ref,
        "issued_at": _iso(utcnow()),
        "became_aware_at": _iso(breach.aware_at),
        "due_at": _iso(effective_board_deadline(db, breach)),
        "data_fiduciary": {
            "tenant_code": breach.source_app,
            "contact_name": breach.contact_name,
            "contact_email": breach.contact_email,
            "contact_phone": breach.contact_phone,
        },
        # (i) updated and detailed information in respect of clause (a)
        "updated_description": (
            f"Nature: {_sentence(breach.nature)} Extent: {_sentence(breach.extent)} "
            f"Location of occurrence: {_sentence(breach.location)} "
            f"Likely impact: {_sentence(breach.likely_impact)} "
            f"{_timing_statement(breach)} "
            f"Affected Data Principals identified: {breach.affected_count}."
        ).strip(),
        # (ii) broad facts related to the events, circumstances and reasons
        "events_and_reasons": breach.cause,
        # (iii) measures implemented and being implemented to mitigate risk
        "mitigation_measures": breach.mitigation_measures,
        # (iv) findings regarding the person who caused the breach
        "findings_on_actor": breach.findings_on_actor,
        # (v) remedial measures taken to prevent recurrence
        "remedial_measures": breach.remedial_measures,
        # (vi) report regarding the intimations given to affected principals
        "principal_intimations_report": principal_intimations_report(db, breach),
        "extension": _extension_summary(db, breach),
    }


def cert_in_payload(db: Session, breach: Breach) -> dict:
    aware = _utc(breach.aware_at)
    return {
        "report_type": "CERT_IN_INCIDENT_REPORT",
        "provision": (
            "CERT-In Directions No. 20(3)/2022-CERT-In of 28 April 2022 under s.70B(6) IT Act "
            "- Annexure I listed incident, 6-hour reporting window"
        ),
        "breach_ref": breach.breach_ref,
        "issued_at": _iso(utcnow()),
        "incident_category": "Data breach / data leak (Annexure I)",
        "noticed_at": _iso(aware),
        "report_due_by": _iso(aware + timedelta(hours=CERT_IN_DEADLINE_HOURS)) if aware else None,
        "time_of_occurrence": _iso(breach.occurred_at),
        "detected_at": _iso(breach.detected_at),
        "description": f"{_sentence(breach.nature)} {_sentence(breach.extent)}".strip(),
        "location_of_occurrence": breach.location,
        "likely_impact": breach.likely_impact,
        "affected_records_or_principals": breach.affected_count,
        "mitigation_measures": breach.mitigation_measures,
        "remedial_measures": breach.remedial_measures,
        "point_of_contact": {
            "name": breach.contact_name,
            "email": breach.contact_email,
            "phone": breach.contact_phone,
        },
    }


def render_sections(title: str, payload: dict, sections: dict[str, str]) -> str:
    lines = [title, f"Reference: {payload['breach_ref']}", f"Provision: {payload['provision']}", ""]
    for index, (field, description) in enumerate(sections.items(), start=1):
        value = payload.get(field)
        if isinstance(value, dict):
            value = json.dumps(value, indent=2, sort_keys=True, default=str)
        lines.append(f"({index}) {description}\n{value}\n")
    return "\n".join(lines)


def missing_clauses(payload: dict, clauses: dict[str, str]) -> list[str]:
    """Which mandated content items this payload does not actually carry.
    A dict section (R.7(2)(b)(vi)) is generated, never typed, so it is present
    by construction; every other section must be non-empty text."""
    missing = []
    for field, description in clauses.items():
        value = payload.get(field)
        if isinstance(value, dict):
            continue
        if not str(value or "").strip():
            missing.append(description)
    return missing


# ---------------------------------------------------------------------------
# Deadlines and filings
# ---------------------------------------------------------------------------

def granted_extension(db: Session, breach: Breach) -> Optional[BreachExtensionRequest]:
    """The furthest-out GRANTED extension, if any. Only a granted request
    moves the R.7(2)(b) deadline."""
    granted = [
        e for e in breach.extension_requests
        if e.status == "GRANTED" and e.granted_until is not None
    ]
    if not granted:
        return None
    return max(granted, key=lambda e: _utc(e.granted_until))


def _extension_summary(db: Session, breach: Breach) -> dict:
    rows = sorted(breach.extension_requests, key=lambda e: _utc(e.requested_at) or utcnow())
    active = granted_extension(db, breach)
    return {
        "requests": [
            {
                "id": e.id,
                "requested_at": _iso(e.requested_at),
                "requested_by": e.requested_by,
                "requested_until": _iso(e.requested_until),
                "reason": e.reason,
                "written_request_ref": e.written_request_ref,
                "status": e.status,
                "decided_at": _iso(e.decided_at),
                "granted_until": _iso(e.granted_until),
                "board_reference": e.board_reference,
                "decision_note": e.decision_note,
            }
            for e in rows
        ],
        "effective_deadline_at": _iso(effective_board_deadline(db, breach)),
        "extended": active is not None,
    }


def statutory_board_deadline(breach: Breach) -> Optional[datetime]:
    aware = _utc(breach.aware_at)
    return aware + timedelta(hours=BOARD_DETAILED_DEADLINE_HOURS) if aware else None


def effective_board_deadline(db: Session, breach: Breach) -> Optional[datetime]:
    base = statutory_board_deadline(breach)
    if base is None:
        return None
    extension = granted_extension(db, breach)
    if extension and _utc(extension.granted_until) > base:
        return _utc(extension.granted_until)
    return base


def cert_in_deadline(breach: Breach) -> Optional[datetime]:
    aware = _utc(breach.aware_at)
    return aware + timedelta(hours=CERT_IN_DEADLINE_HOURS) if aware else None


def _require_aware(breach: Breach) -> datetime:
    aware = _utc(breach.aware_at)
    if aware is None:
        raise BreachError(
            f"Breach {breach.breach_ref} has no aware_at. Rule 7 obligations run from "
            "'becoming aware'; record awareness before generating any filing."
        )
    return aware


def _upsert_filing(
    db: Session, breach: Breach, *, recipient_type: str, stage: str,
    customer_id: Optional[int], payload: dict, subject: str, content: str,
    recipient: str, channel: str, deadline_at: Optional[datetime],
    target_at: Optional[datetime], deadline_basis: str, actor_username: str,
) -> BreachNotification:
    row = (
        db.query(BreachNotification)
        .filter(
            BreachNotification.breach_id == breach.id,
            BreachNotification.recipient_type == recipient_type,
            BreachNotification.stage == stage,
            BreachNotification.customer_id == customer_id,
        )
        .first()
    )
    if row and row.status in ("SENT", "DELIVERED"):
        raise BreachError(
            f"A {recipient_type}/{stage} filing for {breach.breach_ref} has already been "
            f"{row.status.lower()} (hash {row.content_hash[:12]}...). Regenerating it would "
            "destroy the record of what was actually filed; log a corrective filing instead."
        )
    if row is None:
        row = BreachNotification(
            breach_id=breach.id, tenant_id=breach.tenant_id,
            recipient_type=recipient_type, stage=stage, customer_id=customer_id,
        )
        db.add(row)
    row.recipient = recipient
    row.channel = channel
    row.subject = subject
    row.content = content
    row.payload = payload
    row.content_hash = content_hash(payload)
    row.clock_start_at = _utc(breach.aware_at)
    row.deadline_at = deadline_at
    row.target_at = target_at
    row.deadline_basis = deadline_basis
    row.status = "PENDING"
    row.generated_by = actor_username
    db.flush()
    return row


def notify_principals(
    db: Session, breach: Breach, *, language: Optional[str] = None,
    actor_username: str = "system", request_id: Optional[str] = None,
) -> dict:
    """R.7(1): generate and queue one notice per affected principal that does
    not already have one. Dispatch goes through
    `app/services/notifications.py::queue_notification` - the same queue,
    retries and delivery metrics as every other principal communication."""
    aware = _require_aware(breach)
    target_hours = without_delay_target_hours(db, breach)
    generated, skipped = [], []
    for affected in list(breach.affected):
        existing = (
            db.query(BreachNotification)
            .filter(
                BreachNotification.breach_id == breach.id,
                BreachNotification.recipient_type == "PRINCIPAL",
                BreachNotification.customer_id == affected.customer_id,
            )
            .first()
        )
        if existing and existing.status in ("QUEUED", "SENT", "DELIVERED"):
            skipped.append(existing.id)
            continue
        customer = resolve_customer(
            db, source_app=breach.source_app, customer_pk=affected.customer_id
        )
        if customer is None:  # pragma: no cover - FK guarantees the row exists in-tenant
            continue
        payload = principal_notice_payload(db, breach, customer, affected)
        missing = missing_clauses(payload, PRINCIPAL_NOTICE_CLAUSES)
        if missing:
            raise BreachError(
                "Cannot issue a principal notice missing mandated content: " + "; ".join(missing)
            )
        body = render_principal_notice(payload)
        row = _upsert_filing(
            db, breach, recipient_type="PRINCIPAL", stage="NOTICE",
            customer_id=affected.customer_id, payload=payload,
            subject=f"Important: personal data breach notice ({breach.breach_ref})",
            content=body, recipient=customer.email or customer.external_id,
            channel="EMAIL",
            deadline_at=None,  # R.7(1) prescribes no fixed period - see module docstring
            target_at=aware + timedelta(hours=target_hours),
            deadline_basis=BASIS_PRINCIPAL,
            actor_username=actor_username,
        )
        queued = queue_notification(
            db, customer=customer, event_type="BREACH_NOTICE",
            source_app=breach.source_app, language=language,
            context={"details": body, "breach_ref": breach.breach_ref},
            request_id=request_id, actor_username=actor_username,
        )
        # queue_notification fans out across channels; the EMAIL row (or the
        # first queued row when the principal has no email on file) is the one
        # this filing tracks for sent/delivered.
        primary = next((n for n in queued if n.channel == "EMAIL"), queued[0] if queued else None)
        if primary is not None:
            row.notification_id = primary.id
            row.channel = primary.channel
            row.status = "QUEUED"
        db.flush()
        generated.append(row.id)

    log_audit(
        db, "BREACH_PRINCIPAL_NOTICES_ISSUED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: {len(generated)} principal notice(s) issued under R.7(1) "
            f"({len(skipped)} already issued)"
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref,
                  "generated": len(generated), "already_issued": len(skipped)},
        commit=False,
    )
    db.commit()
    return {"generated": len(generated), "already_issued": len(skipped),
            "breach_notification_ids": generated}


def _dispatch_regulator_filing(
    db: Session, breach: Breach, row: BreachNotification, *, recipient: str,
    regulator_name: str, actor_username: str, request_id: Optional[str],
) -> None:
    """Hand a Board/CERT-In filing to the shared notification dispatcher when
    the tenant has configured an address for it. When it has not, the filing
    stays PENDING with its content and hash on record and the operator records
    the out-of-band filing reference through `record_filing` - the honest
    outcome, since neither regulator exposes an ingestion API."""
    if not recipient:
        row.last_error = (
            f"No {regulator_name} address configured for this tenant "
            f"(Organization.settings). Filing generated and hashed; record the "
            f"out-of-band filing reference to mark it sent."
        )
        logger.warning(
            "Breach %s: %s/%s filing generated but HELD - no %s address configured for "
            "tenant %s. The statutory deadline is still running; file it out of band and "
            "record the reference via record_filing().",
            breach.breach_ref, row.recipient_type, row.stage, regulator_name, breach.source_app,
        )
        return
    notification = queue_operational_notification(
        db, recipient=recipient, event_type="BREACH_NOTICE",
        tenant_id=breach.tenant_id, source_app=breach.source_app,
        context={"customer_name": regulator_name, "details": row.content,
                 "breach_ref": breach.breach_ref},
        request_id=request_id, actor_username=actor_username,
    )
    if notification is not None:
        row.notification_id = notification.id
        row.status = "QUEUED"
        row.last_error = ""


def file_board_initial(
    db: Session, breach: Breach, *, actor_username: str = "system",
    request_id: Optional[str] = None,
) -> BreachNotification:
    aware = _require_aware(breach)
    payload = board_initial_payload(db, breach)
    missing = missing_clauses(payload, BOARD_INITIAL_CLAUSES)
    if missing:
        raise BreachError(
            "Cannot file the Board initial intimation missing mandated content: " + "; ".join(missing)
        )
    content = render_sections(
        "INTIMATION OF PERSONAL DATA BREACH TO THE DATA PROTECTION BOARD OF INDIA",
        payload, BOARD_INITIAL_CLAUSES,
    )
    recipient = board_recipient(db, breach)
    row = _upsert_filing(
        db, breach, recipient_type="BOARD", stage="INITIAL", customer_id=None,
        payload=payload, subject=f"Personal data breach intimation - {breach.breach_ref}",
        content=content, recipient=recipient, channel="EMAIL",
        deadline_at=None,  # R.7(2)(a): "without delay", no fixed period
        target_at=aware + timedelta(hours=without_delay_target_hours(db, breach)),
        deadline_basis=BASIS_BOARD_INITIAL, actor_username=actor_username,
    )
    _dispatch_regulator_filing(
        db, breach, row, recipient=recipient, regulator_name="Data Protection Board of India",
        actor_username=actor_username, request_id=request_id,
    )
    log_audit(
        db, "BREACH_BOARD_NOTIFIED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=f"Breach {breach.breach_ref}: Board initial intimation generated under R.7(2)(a)",
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref, "stage": "INITIAL",
                  "content_hash": row.content_hash, "status": row.status},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


def file_board_detailed(
    db: Session, breach: Breach, *, actor_username: str = "system",
    request_id: Optional[str] = None,
) -> BreachNotification:
    aware = _require_aware(breach)
    payload = board_detailed_payload(db, breach)
    missing = missing_clauses(payload, BOARD_DETAILED_SECTIONS)
    if missing:
        raise BreachError(
            "The R.7(2)(b) report must carry all six mandated sections; missing: "
            + "; ".join(missing)
        )
    content = render_sections(
        "DETAILED REPORT OF PERSONAL DATA BREACH TO THE DATA PROTECTION BOARD OF INDIA",
        payload, BOARD_DETAILED_SECTIONS,
    )
    recipient = board_recipient(db, breach)
    deadline = effective_board_deadline(db, breach)
    row = _upsert_filing(
        db, breach, recipient_type="BOARD", stage="DETAILED", customer_id=None,
        payload=payload, subject=f"Personal data breach detailed report - {breach.breach_ref}",
        content=content, recipient=recipient, channel="EMAIL",
        deadline_at=deadline,
        target_at=None,
        deadline_basis=BASIS_BOARD_DETAILED, actor_username=actor_username,
    )
    _dispatch_regulator_filing(
        db, breach, row, recipient=recipient, regulator_name="Data Protection Board of India",
        actor_username=actor_username, request_id=request_id,
    )
    log_audit(
        db, "BREACH_BOARD_NOTIFIED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: 72-hour detailed report generated under R.7(2)(b); "
            f"due {_iso(deadline)}"
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref, "stage": "DETAILED",
                  "content_hash": row.content_hash, "due_at": _iso(deadline),
                  "status": row.status},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


def file_cert_in(
    db: Session, breach: Breach, *, actor_username: str = "system",
    request_id: Optional[str] = None,
) -> BreachNotification:
    aware = _require_aware(breach)
    if not breach.cert_in_reportable:
        raise BreachError(
            f"Breach {breach.breach_ref} is marked not CERT-In reportable "
            f"({breach.cert_in_not_reportable_reason or 'no reason recorded'}). "
            "Clear that flag before filing."
        )
    payload = cert_in_payload(db, breach)
    content = render_sections("CYBER INCIDENT REPORT TO CERT-In", payload, CERT_IN_SECTIONS)
    recipient = cert_in_recipient(db, breach)
    deadline = cert_in_deadline(breach)
    row = _upsert_filing(
        db, breach, recipient_type="CERT_IN", stage="INITIAL", customer_id=None,
        payload=payload, subject=f"Cyber incident report - {breach.breach_ref}",
        content=content, recipient=recipient, channel="EMAIL",
        deadline_at=deadline, target_at=None,
        deadline_basis=BASIS_CERT_IN, actor_username=actor_username,
    )
    _dispatch_regulator_filing(
        db, breach, row, recipient=recipient, regulator_name="CERT-In",
        actor_username=actor_username, request_id=request_id,
    )
    log_audit(
        db, "BREACH_CERT_IN_NOTIFIED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: CERT-In incident report generated; "
            f"6-hour window from {_iso(aware)} closes {_iso(deadline)}"
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref,
                  "content_hash": row.content_hash, "due_at": _iso(deadline),
                  "status": row.status},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


def record_filing(
    db: Session, breach: Breach, row: BreachNotification, *, filing_reference: str,
    filed_at: Optional[datetime] = None, delivered: bool = True,
    actor_username: str = "system", request_id: Optional[str] = None,
) -> BreachNotification:
    """Record an out-of-band filing (the Board's portal, CERT-In's incident
    form) against a generated notification. This is what turns a PENDING
    regulator filing into a SENT one when there is no email transport."""
    if row.recipient_type == "PRINCIPAL":
        raise BreachError(
            "Principal notices are delivered through the notification service and their "
            "sent/delivered timestamps come from it; they cannot be marked filed by hand."
        )
    if not filing_reference.strip():
        raise BreachError("A filing reference is required: it is the evidence the filing happened.")
    when = _utc(filed_at) or utcnow()
    row.sent_at = when
    row.status = "DELIVERED" if delivered else "SENT"
    if delivered:
        row.delivered_at = when
    row.filing_reference = filing_reference.strip()
    row.last_error = ""
    log_audit(
        db, "BREACH_FILING_RECORDED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: {row.recipient_type}/{row.stage} filing recorded as "
            f"{row.status} at {_iso(when)} (ref {row.filing_reference})"
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref,
                  "breach_notification_id": row.id, "content_hash": row.content_hash,
                  "filed_at": _iso(when)},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Extension requests (R.7(2)(b) proviso)
# ---------------------------------------------------------------------------

def request_extension(
    db: Session, breach: Breach, *, requested_until: datetime, reason: str,
    written_request_ref: str = "", actor_username: str = "system",
    request_id: Optional[str] = None,
) -> BreachExtensionRequest:
    base = statutory_board_deadline(breach)
    if base is None:
        raise BreachError("Record awareness before requesting an extension: there is no clock yet.")
    requested = _utc(requested_until)
    if requested <= base:
        raise BreachError(
            f"An extension must ask for a period longer than the statutory deadline "
            f"({_iso(base)}); {_iso(requested)} is not."
        )
    row = BreachExtensionRequest(
        breach_id=breach.id, tenant_id=breach.tenant_id,
        requested_until=requested, reason=reason,
        written_request_ref=written_request_ref, requested_by=actor_username,
        status="REQUESTED",
    )
    db.add(row)
    db.flush()
    log_audit(
        db, "BREACH_EXTENSION_REQUESTED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: written request to the Board under the R.7(2)(b) proviso "
            f"for an extension to {_iso(requested)}"
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref,
                  "extension_request_id": row.id, "requested_until": _iso(requested),
                  "statutory_deadline": _iso(base)},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


def decide_extension(
    db: Session, breach: Breach, row: BreachExtensionRequest, *, status: str,
    granted_until: Optional[datetime] = None, board_reference: str = "",
    decision_note: str = "", actor_username: str = "system",
    request_id: Optional[str] = None,
) -> BreachExtensionRequest:
    if row.status != "REQUESTED":
        raise BreachError(f"Extension request {row.id} is already {row.status}.")
    if status not in ("GRANTED", "REFUSED", "WITHDRAWN"):
        raise BreachError(f"Unknown extension decision '{status}'")
    if status == "GRANTED":
        granted = _utc(granted_until) or _utc(row.requested_until)
        base = statutory_board_deadline(breach)
        if base and granted <= base:
            raise BreachError(
                f"A granted extension must fall after the statutory deadline ({_iso(base)})."
            )
        row.granted_until = granted
    row.status = status
    row.decided_at = utcnow()
    row.decided_by = actor_username
    row.board_reference = board_reference
    row.decision_note = decision_note
    db.flush()

    # A granted extension moves the R.7(2)(b) deadline; any detailed report
    # already generated but not yet filed must carry the new one.
    if status == "GRANTED":
        new_deadline = effective_board_deadline(db, breach)
        for filing in breach.notifications:
            if filing.recipient_type == "BOARD" and filing.stage == "DETAILED" \
                    and filing.status in ("PENDING", "QUEUED"):
                filing.deadline_at = new_deadline

    log_audit(
        db, "BREACH_EXTENSION_DECIDED", actor_username=actor_username, actor_type="USER",
        source_app=breach.source_app, tenant_id=breach.tenant_id,
        reason=(
            f"Breach {breach.breach_ref}: Board extension request {row.id} {status.lower()}"
            + (f" until {_iso(row.granted_until)}" if row.granted_until else "")
        ),
        request_id=request_id,
        metadata={"breach_id": breach.id, "breach_ref": breach.breach_ref,
                  "extension_request_id": row.id, "status": status,
                  "granted_until": _iso(row.granted_until),
                  "board_reference": board_reference},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Delivery sync and the clocks
# ---------------------------------------------------------------------------

def sync_delivery(db: Session, breach: Breach) -> int:
    """Copy sent/delivered/failed state from the notification rows the shared
    dispatcher owns onto the breach filings that reference them. Called on
    every read path, so a clock is never computed from stale state."""
    linked = [n for n in breach.notifications if n.notification_id]
    if not linked:
        return 0
    rows = {
        n.id: n for n in db.query(Notification)
        .filter(Notification.id.in_([f.notification_id for f in linked]))
        .all()
    }
    changed = 0
    for filing in linked:
        source = rows.get(filing.notification_id)
        if source is None:
            continue
        before = (filing.status, filing.sent_at, filing.delivered_at)
        if source.status == "FAILED":
            filing.status = "FAILED"
            filing.failed_at = filing.failed_at or utcnow()
            filing.last_error = (source.last_error or "")[:2000]
        elif source.delivered_at or source.status in ("DELIVERED", "ACKNOWLEDGED"):
            filing.status = "DELIVERED"
            filing.sent_at = _utc(source.sent_at) or _utc(source.delivered_at)
            filing.delivered_at = _utc(source.delivered_at) or _utc(source.sent_at)
        elif source.sent_at or source.status == "SENT":
            filing.status = "SENT"
            filing.sent_at = _utc(source.sent_at)
        if before != (filing.status, filing.sent_at, filing.delivered_at):
            changed += 1
    if changed:
        db.commit()
    return changed


def _clock(
    *, key: str, obligation: str, basis: str, applicable: bool,
    started_at: Optional[datetime], deadline_at: Optional[datetime],
    target_at: Optional[datetime], satisfied_at: Optional[datetime],
    now: datetime, extra: Optional[dict] = None,
) -> dict:
    started_at, deadline_at = _utc(started_at), _utc(deadline_at)
    target_at, satisfied_at = _utc(target_at), _utc(satisfied_at)
    reference = deadline_at or target_at
    measured_against = "STATUTORY_DEADLINE" if deadline_at else (
        "INTERNAL_TARGET" if target_at else "NONE"
    )
    if not applicable:
        status = "NOT_APPLICABLE"
    elif started_at is None:
        status = "NOT_STARTED"
    elif satisfied_at is not None:
        status = "MET_LATE" if reference and satisfied_at > reference else "MET"
    elif reference and now > reference:
        status = "OVERDUE"
    else:
        status = "OPEN"
    clock = {
        "clock": key,
        "obligation": obligation,
        "basis": basis,
        "starts_from": "aware_at (becoming aware / noticing) - not detected_at",
        "started_at": _iso(started_at),
        "deadline_at": _iso(deadline_at),
        "deadline_kind": "STATUTORY" if deadline_at else "WITHOUT_DELAY_NO_FIXED_PERIOD",
        "internal_target_at": _iso(target_at),
        "measured_against": measured_against,
        "satisfied_at": _iso(satisfied_at),
        "elapsed_hours": _hours_between(started_at, satisfied_at or now),
        "remaining_hours": _hours_between(now, reference) if (reference and satisfied_at is None) else None,
        "status": status,
    }
    if extra:
        clock.update(extra)
    return clock


def breach_clocks(db: Session, breach: Breach, *, now: Optional[datetime] = None) -> list[dict]:
    """The four tracked clocks. `now` is injectable so a test - or a
    what-if in the runbook - can ask "where would this stand at T+7h?"."""
    sync_delivery(db, breach)
    now = _utc(now) or utcnow()
    aware = _utc(breach.aware_at)
    target_hours = without_delay_target_hours(db, breach)
    target = aware + timedelta(hours=target_hours) if aware else None

    filings = {(n.recipient_type, n.stage): n for n in breach.notifications
               if n.recipient_type != "PRINCIPAL"}
    principal_rows = [n for n in breach.notifications if n.recipient_type == "PRINCIPAL"]
    sent_principal = [n for n in principal_rows if n.sent_at]
    notified_ids = {n.customer_id for n in sent_principal}
    all_notified = (
        breach.scope_finalised_at is not None
        and breach.affected_count > 0
        and all(a.customer_id in notified_ids for a in breach.affected)
    )
    last_principal_sent = max((_utc(n.sent_at) for n in sent_principal), default=None)

    def _sent(key: tuple[str, str]) -> Optional[datetime]:
        row = filings.get(key)
        return _utc(row.sent_at) if row and row.sent_at else None

    cert_row = filings.get(("CERT_IN", "INITIAL"))
    board_detailed_row = filings.get(("BOARD", "DETAILED"))

    return [
        _clock(
            key="CERT_IN_6H",
            obligation="Report the cyber incident to CERT-In",
            basis=BASIS_CERT_IN, applicable=bool(breach.cert_in_reportable),
            started_at=aware, deadline_at=cert_in_deadline(breach), target_at=None,
            satisfied_at=_sent(("CERT_IN", "INITIAL")), now=now,
            extra={
                "window_hours": CERT_IN_DEADLINE_HOURS,
                "filing_status": cert_row.status if cert_row else "NOT_GENERATED",
                "not_applicable_reason": (
                    breach.cert_in_not_reportable_reason if not breach.cert_in_reportable else None
                ),
            },
        ),
        _clock(
            key="PRINCIPAL_WITHOUT_DELAY",
            obligation="Intimate each affected Data Principal",
            basis=BASIS_PRINCIPAL, applicable=True,
            started_at=aware, deadline_at=None, target_at=target,
            satisfied_at=last_principal_sent if all_notified else None, now=now,
            extra={
                "affected_principals": breach.affected_count,
                "notices_generated": len(principal_rows),
                "notices_sent": len(sent_principal),
                "notices_delivered": len([n for n in principal_rows if n.delivered_at]),
                "scope_finalised": breach.scope_finalised_at is not None,
                # Until the scope is finalised, "all notified" is unknowable;
                # the clock says so instead of reporting a false MET.
                "scope_note": (
                    None if breach.scope_finalised_at is not None
                    else "Scope not finalised - completion of R.7(1) cannot yet be asserted"
                ),
                "internal_target_hours": target_hours,
            },
        ),
        _clock(
            key="BOARD_INITIAL_WITHOUT_DELAY",
            obligation="Intimate the Board with a description of the breach",
            basis=BASIS_BOARD_INITIAL, applicable=True,
            started_at=aware, deadline_at=None, target_at=target,
            satisfied_at=_sent(("BOARD", "INITIAL")), now=now,
            extra={
                "filing_status": (
                    filings[("BOARD", "INITIAL")].status if ("BOARD", "INITIAL") in filings
                    else "NOT_GENERATED"
                ),
                "internal_target_hours": target_hours,
            },
        ),
        _clock(
            key="BOARD_DETAILED_72H",
            obligation="File the detailed six-section report with the Board",
            basis=BASIS_BOARD_DETAILED, applicable=True,
            started_at=aware, deadline_at=effective_board_deadline(db, breach), target_at=None,
            satisfied_at=_sent(("BOARD", "DETAILED")), now=now,
            extra={
                "window_hours": BOARD_DETAILED_DEADLINE_HOURS,
                "statutory_deadline_at": _iso(statutory_board_deadline(breach)),
                "extension_granted": granted_extension(db, breach) is not None,
                "filing_status": board_detailed_row.status if board_detailed_row else "NOT_GENERATED",
            },
        ),
    ]


def breach_timeline(db: Session, breach: Breach) -> list[dict]:
    """Every clock-relevant event in one ordered list - the thing an
    investigator, or the Board, actually reads."""
    sync_delivery(db, breach)
    events: list[dict] = []
    if breach.occurred_at:
        events.append({"at": _iso(breach.occurred_at), "event": "OCCURRED",
                       "detail": "Breach occurred (best known)"})
    events.append({"at": _iso(breach.detected_at), "event": "DETECTED",
                   "detail": "Breach detected"})
    if breach.aware_at:
        events.append({
            "at": _iso(breach.aware_at), "event": "AWARE",
            "detail": "Fiduciary became aware - R.7 and CERT-In clocks start here",
        })
        events.append({"at": _iso(cert_in_deadline(breach)), "event": "CERT_IN_DEADLINE",
                       "detail": f"CERT-In 6-hour window closes ({BASIS_CERT_IN})"})
        events.append({"at": _iso(effective_board_deadline(db, breach)),
                       "event": "BOARD_DETAILED_DEADLINE",
                       "detail": f"R.7(2)(b) report due ({BASIS_BOARD_DETAILED})"})
    for e in breach.extension_requests:
        events.append({"at": _iso(e.requested_at), "event": "EXTENSION_REQUESTED",
                       "detail": f"Written request to the Board until {_iso(e.requested_until)}"})
        if e.decided_at:
            events.append({"at": _iso(e.decided_at), "event": f"EXTENSION_{e.status}",
                           "detail": f"Board reference {e.board_reference or '-'}"})
    for n in breach.notifications:
        if n.sent_at:
            events.append({
                "at": _iso(n.sent_at),
                "event": f"{n.recipient_type}_{n.stage}_SENT",
                "detail": f"content_hash={n.content_hash}",
            })
    if breach.scope_finalised_at:
        events.append({"at": _iso(breach.scope_finalised_at), "event": "SCOPE_FINALISED",
                       "detail": f"{breach.affected_count} affected principal(s)"})
    if breach.closed_at:
        events.append({"at": _iso(breach.closed_at), "event": "CLOSED",
                       "detail": breach.closure_note})
    return sorted(events, key=lambda e: (e["at"] or "", e["event"]))


# ---------------------------------------------------------------------------
# K-39, K-40, K-41
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 3) if values else None


def breach_metrics(db: Session, *, source_app: Optional[str] = None) -> dict:
    """K-39 (time-to-detect / time-to-notify principals), K-40 (Board
    notification timeliness), K-41 (affected principals per breach and notice
    delivery rate), plus the CERT-In 6-hour figure H-12 needs."""
    query = db.query(Breach)
    if source_app:
        query = query.filter(Breach.source_app == source_app)
    breaches = query.order_by(Breach.id.asc()).all()

    detect_hours, triage_hours = [], []
    first_notice_hours, last_notice_hours = [], []
    board_initial_hours, board_detailed_hours = [], []
    cert_in_hours = []
    detailed_within_window, detailed_filed = 0, 0
    cert_in_within_window, cert_in_filed, cert_in_applicable = 0, 0, 0
    affected_total, notices_generated, notices_sent, notices_delivered = 0, 0, 0, 0
    per_breach = []

    for breach in breaches:
        sync_delivery(db, breach)
        aware = _utc(breach.aware_at)
        d_hours = _hours_between(breach.occurred_at, breach.detected_at)
        if d_hours is not None:
            detect_hours.append(d_hours)
        t_hours = _hours_between(breach.detected_at, breach.aware_at)
        if t_hours is not None:
            triage_hours.append(t_hours)

        principal_rows = [n for n in breach.notifications if n.recipient_type == "PRINCIPAL"]
        sent_rows = [n for n in principal_rows if n.sent_at]
        first_sent = min((_utc(n.sent_at) for n in sent_rows), default=None)
        last_sent = max((_utc(n.sent_at) for n in sent_rows), default=None)
        f_hours = _hours_between(aware, first_sent)
        l_hours = _hours_between(aware, last_sent)
        if f_hours is not None:
            first_notice_hours.append(f_hours)
        if l_hours is not None:
            last_notice_hours.append(l_hours)

        filings = {(n.recipient_type, n.stage): n for n in breach.notifications
                   if n.recipient_type != "PRINCIPAL"}
        bi = filings.get(("BOARD", "INITIAL"))
        bi_hours = _hours_between(aware, bi.sent_at) if bi and bi.sent_at else None
        if bi_hours is not None:
            board_initial_hours.append(bi_hours)
        bd = filings.get(("BOARD", "DETAILED"))
        bd_hours = _hours_between(aware, bd.sent_at) if bd and bd.sent_at else None
        bd_on_time = None
        if bd_hours is not None:
            board_detailed_hours.append(bd_hours)
            detailed_filed += 1
            deadline = effective_board_deadline(db, breach)
            bd_on_time = bool(deadline and _utc(bd.sent_at) <= deadline)
            if bd_on_time:
                detailed_within_window += 1
        ci = filings.get(("CERT_IN", "INITIAL"))
        ci_hours = _hours_between(aware, ci.sent_at) if ci and ci.sent_at else None
        ci_on_time = None
        if breach.cert_in_reportable:
            cert_in_applicable += 1
            if ci_hours is not None:
                cert_in_hours.append(ci_hours)
                cert_in_filed += 1
                ci_on_time = ci_hours <= CERT_IN_DEADLINE_HOURS
                if ci_on_time:
                    cert_in_within_window += 1

        affected_total += breach.affected_count
        notices_generated += len(principal_rows)
        notices_sent += len(sent_rows)
        delivered_rows = [n for n in principal_rows if n.delivered_at]
        notices_delivered += len(delivered_rows)

        per_breach.append({
            "breach_ref": breach.breach_ref,
            "status": breach.status,
            "severity": breach.severity,
            "source_app": breach.source_app,
            "occurred_at": _iso(breach.occurred_at),
            "detected_at": _iso(breach.detected_at),
            "aware_at": _iso(aware),
            # K-39
            "time_to_detect_hours": d_hours,
            "detect_to_aware_hours": t_hours,
            "hours_to_first_principal_notice": f_hours,
            "hours_to_last_principal_notice": l_hours,
            # K-40
            "hours_to_board_initial": bi_hours,
            "hours_to_board_detailed": bd_hours,
            "board_detailed_within_deadline": bd_on_time,
            "board_deadline_at": _iso(effective_board_deadline(db, breach)),
            "board_deadline_extended": granted_extension(db, breach) is not None,
            # CERT-In (H-12)
            "cert_in_reportable": breach.cert_in_reportable,
            "hours_to_cert_in": ci_hours,
            "cert_in_within_6h": ci_on_time,
            # K-41
            "affected_principals": breach.affected_count,
            "principal_notices_generated": len(principal_rows),
            "principal_notices_sent": len(sent_rows),
            "principal_notices_delivered": len(delivered_rows),
            "principal_notice_delivery_rate_pct": (
                round(len(delivered_rows) / len(principal_rows) * 100, 2) if principal_rows else None
            ),
        })

    return {
        "generated_at": _iso(utcnow()),
        "scope": source_app or "ALL_TENANTS",
        "breaches": len(breaches),
        "K-39": {
            "definition": "Breach time-to-detect / time-to-notify principals (hours)",
            "provision": "DPDP Rules 2025, R.7(1)",
            "mean_time_to_detect_hours": _mean(detect_hours),
            "max_time_to_detect_hours": max(detect_hours) if detect_hours else None,
            "mean_detect_to_aware_hours": _mean(triage_hours),
            "mean_hours_to_first_principal_notice": _mean(first_notice_hours),
            "max_hours_to_last_principal_notice": max(last_notice_hours) if last_notice_hours else None,
            "note": (
                "Notification hours run from aware_at, per R.7(1) 'on becoming aware'. "
                "detect_to_aware is reported separately so a late triage cannot hide inside a "
                "flattering notification figure."
            ),
        },
        "K-40": {
            "definition": "Breach Board-notification timeliness (initial: hours; detailed: <= 72h)",
            "provision": "DPDP Rules 2025, R.7(2)",
            "mean_hours_to_board_initial": _mean(board_initial_hours),
            "max_hours_to_board_initial": max(board_initial_hours) if board_initial_hours else None,
            "mean_hours_to_board_detailed": _mean(board_detailed_hours),
            "detailed_reports_filed": detailed_filed,
            "detailed_reports_within_deadline": detailed_within_window,
            "detailed_within_deadline_pct": (
                round(detailed_within_window / detailed_filed * 100, 2) if detailed_filed else None
            ),
            "target": "100% within 72 hours (R.7(2)(b)), or within a Board-granted extension",
        },
        "K-41": {
            "definition": "Affected principals per breach and notice delivery rate",
            "provision": "DPDP Rules 2025, R.7(1)",
            "affected_principals_total": affected_total,
            "mean_affected_per_breach": (
                round(affected_total / len(breaches), 3) if breaches else None
            ),
            "principal_notices_generated": notices_generated,
            "principal_notices_sent": notices_sent,
            "principal_notices_delivered": notices_delivered,
            "notice_delivery_rate_pct": (
                round(notices_delivered / notices_generated * 100, 2) if notices_generated else None
            ),
            "target": "100% delivered",
        },
        "cert_in": {
            "definition": "CERT-In 6-hour reporting (H-12)",
            "provision": BASIS_CERT_IN,
            "reportable_breaches": cert_in_applicable,
            "reports_filed": cert_in_filed,
            "reports_within_6h": cert_in_within_window,
            "within_6h_pct": (
                round(cert_in_within_window / cert_in_filed * 100, 2) if cert_in_filed else None
            ),
            "mean_hours_to_report": _mean(cert_in_hours),
        },
        "per_breach": per_breach,
    }


# The two clock fields that are a live countdown rather than a recorded fact.
# `breach_clocks` reports them because an incident responder needs them; the
# register must not, see `_register_clocks`.
_LIVE_CLOCK_FIELDS = ("elapsed_hours", "remaining_hours")


def _register_clocks(db: Session, breach: Breach) -> list[dict]:
    """The clocks as an *evidence* artefact rather than an operational view.

    `breach_clocks` reports `remaining_hours`, and `elapsed_hours` measured to
    "now" while an obligation is still open. Both are correct for an incident
    responder watching a deadline approach, and both are wrong here: the
    register is a record, and `app/services/evidence_pack.py` takes a SHA-256
    over the canonical JSON of every section it emits. A countdown that ticks
    would give the same pack, for the same period, a different manifest hash on
    every generation - so a regulator could not re-derive the hash they were
    handed, which is the whole point of the manifest.

    So this strips the live fields, keeping `elapsed_hours` only where the
    obligation has already been discharged (there it is a fixed
    satisfied_at - started_at, not a countdown). Everything left is a recorded
    fact: when the clock started, when it was due, when it was met, and the
    resulting status. `status` can still change - OPEN becomes OVERDUE when a
    deadline passes - but that is a real change in the record, not a clock
    tick, and it is exactly what a later reader should see.
    """
    rows = []
    for clock in breach_clocks(db, breach):
        row = {k: v for k, v in clock.items() if k not in _LIVE_CLOCK_FIELDS}
        if clock.get("satisfied_at"):
            row["elapsed_hours"] = clock["elapsed_hours"]
        rows.append(row)
    return rows


def breach_register(db: Session, *, source_app: Optional[str] = None,
                    period_start: Optional[datetime] = None,
                    period_end: Optional[datetime] = None) -> list[dict]:
    """The register extract a s.28 request (or `services/evidence_pack.py`)
    asks for: one row per breach with its clocks and its filings' hashes.

    Deterministic for a given database state - see `_register_clocks` for why
    that matters and what it costs."""
    query = db.query(Breach)
    if source_app:
        query = query.filter(Breach.source_app == source_app)
    if period_start:
        query = query.filter(Breach.detected_at >= _utc(period_start))
    if period_end:
        query = query.filter(Breach.detected_at <= _utc(period_end))
    rows = []
    for breach in query.order_by(Breach.id.asc()).all():
        rows.append({
            "breach_ref": breach.breach_ref,
            "tenant_code": breach.source_app,
            "title": breach.title,
            "status": breach.status,
            "severity": breach.severity,
            "occurred_at": _iso(breach.occurred_at),
            "detected_at": _iso(breach.detected_at),
            "aware_at": _iso(breach.aware_at),
            "affected_principals": breach.affected_count,
            "clocks": _register_clocks(db, breach),
            "filings": [
                {
                    "recipient_type": n.recipient_type,
                    "stage": n.stage,
                    "status": n.status,
                    "sent_at": _iso(n.sent_at),
                    "delivered_at": _iso(n.delivered_at),
                    "deadline_at": _iso(n.deadline_at),
                    "deadline_basis": n.deadline_basis,
                    "content_hash": n.content_hash,
                    "filing_reference": n.filing_reference,
                }
                for n in sorted(breach.notifications, key=lambda x: x.id)
            ],
            "extension_requests": _extension_summary(db, breach)["requests"],
            "closed_at": _iso(breach.closed_at),
        })
    return rows
