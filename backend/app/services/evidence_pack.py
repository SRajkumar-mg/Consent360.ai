"""R1-10 (D-08): the regulator evidence pack.

One export, for one period, containing what a Data Protection Board request
under s.28 would actually ask for: the audit ledger extract, the notice
versions that were in force, the data-principal request log, the breach
register, the retention actions taken, and integrity proofs over all of it.

**Sections degrade honestly, in both directions.** A feature this platform has
not built is emitted as a section with ``"status": "UNAVAILABLE"`` and
``"records": null``, never as an empty list - an empty ``records: []`` in a
pack handed to a regulator reads as a positive assertion that there was
nothing to report, which is a claim the platform can only make about a
register it actually keeps. ``unavailable_sections`` at the top level names
every such section so a reader cannot miss it even if they skim.

The converse is just as important and is why `_breach_register` below now
queries `app/services/breach.py`. That section was UNAVAILABLE while R3-08 did
not exist; leaving it that way now that a real register does exist would be
the same misrepresentation pointing the other way, and would hide from a
regulator the very filings they came to read. The UNAVAILABLE path is kept as
a genuine fallback for the case where the register cannot be reached at all,
so an import failure still cannot masquerade as "no breaches".

**Integrity proofs reuse the existing chain.** `app/core/audit_chain.py`'s
`verify_chain` is the mechanism - the same one the scheduled
`audit_chain_verify` job runs - not a second, pack-specific scheme that could
disagree with it. The pack adds only its own manifest: a SHA-256 over the
canonical JSON of every other section, signed with the platform HMAC key
(`app/core/encryption.py::hmac_signature`, as used for consent receipts and
evidence), so the pack itself is tamper-evident in transit.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.audit_chain import verify_chain
from app.core.encryption import hmac_signature
from app.models.entities import (
    AuditLog,
    Notice,
    NoticeVersion,
    Objection,
    Organization,
    PolicyVersion,
    PurposeVersion,
    RetentionAction,
)
from app.services.audit import log_audit
from app.services.retention import retention_scan

logger = logging.getLogger("app.evidence_pack")

# Cap on how many ledger rows are inlined. The pack always reports the true
# total alongside, so a truncated extract announces itself rather than looking
# like a complete one.
LEDGER_ROW_LIMIT = 5000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    v = _as_utc(value)
    return v.isoformat() if v else None


def _unavailable(name: str, reason: str, unblocked_by: str) -> dict:
    return {
        "section": name,
        "status": "UNAVAILABLE",
        "records": None,
        "reason": reason,
        "unblocked_by": unblocked_by,
    }


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _ledger_extract(db: Session, start: datetime, end: datetime, tenant_id: Optional[int]) -> dict:
    q = db.query(AuditLog).filter(AuditLog.created_at >= start, AuditLog.created_at <= end)
    if tenant_id is not None:
        q = q.filter(AuditLog.tenant_id == tenant_id)
    total = q.count()
    rows = q.order_by(AuditLog.id.asc()).limit(LEDGER_ROW_LIMIT).all()
    return {
        "section": "ledger_extract",
        "status": "OK",
        "total_in_period": total,
        "rows_included": len(rows),
        "truncated": total > len(rows),
        "records": [
            {
                "id": r.id,
                "tenant_id": r.tenant_id,
                "event": r.event,
                "actor_username": r.actor_username,
                "actor_type": r.actor_type,
                "actor_role": r.actor_role,
                "source_app": r.source_app,
                # The principal is identified by external id only; `reason` is
                # an EncryptedText column that can carry free text, so it is
                # deliberately not inlined into an exported artefact.
                "customer_external_id": r.customer_external_id,
                "consent_id": r.consent_id,
                "purpose_code": r.purpose_code,
                "old_status": r.old_status,
                "new_status": r.new_status,
                "decision": r.decision,
                "request_id": r.request_id,
                "prev_hash": r.prev_hash,
                "entry_hash": r.entry_hash,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ],
    }


def _notice_versions(db: Session, start: datetime, end: datetime, tenant_id: Optional[int]) -> dict:
    """Every notice/purpose/policy version that was in force at any point in
    the period - published before the end and either still current or retired
    after the start."""
    nq = db.query(NoticeVersion).join(Notice, Notice.id == NoticeVersion.notice_id)
    if tenant_id is not None:
        nq = nq.filter(Notice.tenant_id == tenant_id)
    notice_versions = [
        v for v in nq.order_by(NoticeVersion.id.asc()).all()
        if _overlaps(_as_utc(v.effective_from), _as_utc(v.effective_to), start, end)
    ]
    purpose_versions = [
        v for v in db.query(PurposeVersion).order_by(PurposeVersion.id.asc()).all()
        if _overlaps(_as_utc(v.effective_from), _as_utc(v.effective_to), start, end)
    ]
    policy_versions = [
        v for v in db.query(PolicyVersion).order_by(PolicyVersion.id.asc()).all()
        if _overlaps(_as_utc(v.effective_from), _as_utc(v.effective_to), start, end)
    ]
    return {
        "section": "notice_versions",
        "status": "OK",
        "records": {
            "notice_versions": [
                {
                    "id": v.id, "notice_id": v.notice_id, "version_number": v.version_number,
                    "title": v.title, "language_default": v.language_default,
                    "languages": sorted([v.language_default, *(v.translations or {}).keys()]),
                    "content_hash": v.content_hash, "is_current": v.is_current,
                    "child_restricted": v.child_restricted,
                    "published_at": _iso(v.published_at),
                    "effective_from": _iso(v.effective_from), "effective_to": _iso(v.effective_to),
                }
                for v in notice_versions
            ],
            "purpose_versions": [
                {
                    "id": v.id, "purpose_id": v.purpose_id, "version_number": v.version_number,
                    "is_current": v.is_current,
                    "effective_from": _iso(v.effective_from), "effective_to": _iso(v.effective_to),
                }
                for v in purpose_versions
            ],
            "policy_versions": [
                {
                    "id": v.id, "policy_id": v.policy_id, "version_number": v.version_number,
                    "default_decision": v.default_decision, "is_current": v.is_current,
                    "effective_from": _iso(v.effective_from), "effective_to": _iso(v.effective_to),
                }
                for v in policy_versions
            ],
        },
    }


def _overlaps(effective_from: Optional[datetime], effective_to: Optional[datetime],
              start: datetime, end: datetime) -> bool:
    if effective_from is not None and effective_from > end:
        return False
    if effective_to is not None and effective_to < start:
        return False
    return True


# Audit events that record a data principal exercising a right. Kept here
# rather than inline so the set is one obvious thing to extend when a
# dedicated rights-request table lands.
_REQUEST_EVENTS = (
    "PRINCIPAL_RECORD_VIEWED",
    "PRINCIPAL_RECORD_EXPORTED",
    "CUSTOMER_PURGED",
    "OBJECTION_RECORDED",
    "OBJECTION_RESOLVED",
    "CONSENT_WITHDRAWN",
)


def _grievance_records(db: Session, start: datetime, end: datetime,
                       tenant_id: Optional[int]) -> Optional[list[dict]]:
    """R2-06's grievance register for the period, or None if it is not in this
    build / cannot be read.

    Returns the structured register only - reference, category, status and the
    SLA clock - and never `subject`, `description`, `resolution_summary` or
    any handler note. Those are AES-GCM encrypted free text written by a data
    principal about their own personal data, and this pack is exported
    wholesale; the same discipline `services/grievance.py` applies to the
    audit ledger applies here. A regulator who needs the narrative of one
    complaint asks for it by the reference this section gives them.
    """
    try:
        from app.models.grievance import Grievance
    except Exception:  # noqa: BLE001 - absent register must degrade, not raise
        logger.exception("Grievance register unavailable while building an evidence pack")
        return None

    q = db.query(Grievance).filter(
        Grievance.received_at >= start, Grievance.received_at <= end
    )
    if tenant_id is not None:
        q = q.filter(Grievance.tenant_id == tenant_id)
    return [
        {
            "reference_no": g.reference_no, "category": g.category, "status": g.status,
            "channel": g.channel, "source_app": g.source_app,
            "customer_id": g.customer_id,
            "received_at": _iso(g.received_at), "acknowledged_at": _iso(g.acknowledged_at),
            "response_days": g.response_days, "due_at": _iso(g.due_at),
            "escalated_at": _iso(g.escalated_at), "escalation_reason": g.escalation_reason,
            "resolved_at": _iso(g.resolved_at), "closed_at": _iso(g.closed_at),
            "resolved_within_period": (
                _as_utc(g.resolved_at) <= _as_utc(g.due_at) if g.resolved_at else None
            ),
        }
        for g in q.order_by(Grievance.id.asc()).all()
    ]


def _rights_request_records(db: Session, start: datetime, end: datetime,
                            tenant_id: Optional[int]) -> Optional[list[dict]]:
    """R2-05's ss.11-14 rights-request register for the period, or None if it
    is not in this build / cannot be read.

    Structured register only, for the same reason `_grievance_records` gives.
    `closure_hash` IS included: it is a hash, not content, and it is the value
    that lets a regulator check the recorded outcome against the immutable
    audit row for RIGHTS_REQUEST_CLOSED.
    """
    try:
        from app.models.rights import RightsRequest
    except Exception:  # noqa: BLE001 - absent register must degrade, not raise
        logger.exception("Rights-request register unavailable while building an evidence pack")
        return None

    q = db.query(RightsRequest).filter(
        RightsRequest.received_at >= start, RightsRequest.received_at <= end
    )
    if tenant_id is not None:
        q = q.filter(RightsRequest.tenant_id == tenant_id)
    return [
        {
            "reference_no": r.reference_no, "request_type": r.request_type, "status": r.status,
            "channel": r.channel, "source_app": r.source_app, "customer_id": r.customer_id,
            "received_at": _iso(r.received_at), "acknowledged_at": _iso(r.acknowledged_at),
            "acknowledgement_hours": r.acknowledgement_hours,
            "response_days": r.response_days, "due_at": _iso(r.due_at),
            "identity_verified": bool(r.identity_verified),
            "verification_method": r.verification_method,
            "verified_at": _iso(r.verified_at),
            "fulfilled_at": _iso(r.fulfilled_at),
            "rejection_reason": r.rejection_reason,
            "closed_at": _iso(r.closed_at), "closure_hash": r.closure_hash,
            "erasure_job_id": r.erasure_job_id,
            "closed_within_period": (
                _as_utc(r.closed_at) <= _as_utc(r.due_at) if r.closed_at else None
            ),
        }
        for r in q.order_by(RightsRequest.id.asc()).all()
    ]


def _request_logs(db: Session, start: datetime, end: datetime, tenant_id: Optional[int]) -> dict:
    """The data-principal request log: every record this platform keeps of a
    principal exercising a right in the period.

    Four sources, and the section's own status and `coverage` are DERIVED from
    which of them actually answered rather than asserted by a sentence:

      * `rights_requests` (R2-05) - ss.11-14 access, correction, erasure and
        nomination requests, each with its reference, its R.14(2) identity
        check, its acknowledgement and response clocks and its closure hash;
      * `grievances` (R2-06) - s.13 complaints, with reference, acknowledgement,
        the tenant's published response period, escalation and resolution;
      * the objection register (s.7(a)); and
      * the audit events that mark a right being exercised outside those
        registers (record access/export, purge, withdrawal).

    **This docstring is load-bearing history, not decoration.** It previously
    read "there is no dedicated rights-request table ... in this build", and
    that sentence stayed in an exported regulator pack after BOTH registers
    above had been built - so the pack announced an absence that had been
    filled and silently omitted the entire grievance queue from the one
    document whose purpose is completeness. The status/coverage below are now
    computed from `missing`, so the prose cannot outlive the fact again, and
    `tests/test_retention_and_evidence_pack.py::test_a_section_stops_claiming_
    an_absence_the_moment_its_register_lands` is the tripwire that fails the
    moment a watched register appears while this function still ignores it.

    What is deliberately NOT here: the narrative fields of either register.
    See `_grievance_records`.
    """
    q = db.query(AuditLog).filter(
        AuditLog.created_at >= start, AuditLog.created_at <= end,
        AuditLog.event.in_(_REQUEST_EVENTS),
    )
    if tenant_id is not None:
        q = q.filter(AuditLog.tenant_id == tenant_id)
    events = q.order_by(AuditLog.id.asc()).all()

    oq = db.query(Objection).filter(Objection.objected_at >= start, Objection.objected_at <= end)
    if tenant_id is not None:
        oq = oq.filter(Objection.tenant_id == tenant_id)
    objections = oq.order_by(Objection.id.asc()).all()

    grievances = _grievance_records(db, start, end, tenant_id)
    rights_requests = _rights_request_records(db, start, end, tenant_id)

    records: dict = {
        "rights_events": [
            {
                "audit_log_id": e.id, "event": e.event, "actor_username": e.actor_username,
                "actor_type": e.actor_type, "source_app": e.source_app,
                "customer_external_id": e.customer_external_id,
                "purpose_code": e.purpose_code, "created_at": _iso(e.created_at),
            }
            for e in events
        ],
        "objections": [
            {
                "id": o.id, "customer_id": o.customer_id, "purpose_id": o.purpose_id,
                "status": o.status, "source_app": o.source_app,
                "objected_at": _iso(o.objected_at), "resolved_at": _iso(o.resolved_at),
            }
            for o in objections
        ],
    }
    counts = {"rights_events": len(events), "objections": len(objections)}

    # A source that could not be read is named in `missing` and is NOT given an
    # empty list: an empty list in a regulator pack asserts "there were none",
    # which is a claim this platform can only make about a register it actually
    # reached. Same rule the module docstring states for whole sections.
    missing: list[str] = []
    if rights_requests is None:
        missing.append("the ss.11-14 rights-request register (R2-05)")
    else:
        records["rights_requests"] = rights_requests
        counts["rights_requests"] = len(rights_requests)
    if grievances is None:
        missing.append("the s.13 grievance register (R2-06)")
    else:
        records["grievances"] = grievances
        counts["grievances"] = len(grievances)

    covered = ["the objection register", "audit events marking a right being exercised "
               "(record access/export, purge, withdrawal)"]
    if rights_requests is not None:
        covered.insert(0, "the ss.11-14 rights-request register (access, correction, erasure "
                          "and nomination, each with its identity check, its acknowledgement "
                          "and response clocks and its closure hash)")
    if grievances is not None:
        covered.insert(
            1 if rights_requests is not None else 0,
            "the s.13 grievance register (reference, acknowledgement, the tenant's published "
            "response period, escalation and resolution)",
        )

    coverage = (
        "Assembled from " + "; ".join(covered) + ". "
        "The narrative fields of the two registers (complaint and request text, resolution "
        "summaries, handler notes) are encrypted personal data and are deliberately not "
        "reproduced here - each record carries the reference number to request them by."
    )
    if missing:
        coverage += (
            " NOT covered in this export, because it could not be read: "
            + "; ".join(missing)
            + ". Those omissions are not a finding that no such request exists."
        )

    return {
        "section": "request_logs",
        # Derived, never asserted. PARTIAL means a source is genuinely absent
        # from this export and `coverage` names which.
        "status": "PARTIAL" if missing else "OK",
        "coverage": coverage,
        "missing_sources": missing,
        "records": records,
        "counts": counts,
        "nil_return_note": (
            "An empty list under a source named in `coverage` is a positive statement that "
            "that register holds no such record for this period. A source that could not be "
            "read is listed in `missing_sources` and carries no list at all."
        ),
    }


def _breach_register(db: Session, start: datetime, end: datetime,
                     organization: Optional[Organization]) -> dict:
    """The s.8(6) personal data breach register for the period, read from
    R3-08's own service so the pack and the register can never disagree.

    Scoped by tenant CODE rather than id because that is the key
    `services/breach.py::breach_register` filters on (`Breach.source_app`); a
    pack for all tenants passes None and gets every breach.

    An empty `records: []` here is now a real finding - "this register records
    no breach detected in this period" - which is exactly what it could NOT
    mean before R3-08 existed. The UNAVAILABLE fallback remains for the case
    where the register genuinely cannot be reached, so a broken import can
    never be mistaken for a clean period.
    """
    try:
        from app.services.breach import breach_register
    except Exception as exc:  # noqa: BLE001 - must degrade, never fabricate a nil finding
        logger.exception("Breach register unavailable while building an evidence pack")
        return _unavailable(
            "breach_register",
            reason=(
                "The breach register could not be read while building this pack "
                f"({type(exc).__name__}). This section therefore reports nothing about "
                "breaches in the period, and specifically MUST NOT be read as an assertion "
                "that there were none - re-export the pack once the register is reachable."
            ),
            unblocked_by="a working app/services/breach.py",
        )

    records = breach_register(
        db,
        source_app=organization.code if organization else None,
        period_start=start,
        period_end=end,
    )
    return {
        "section": "breach_register",
        "status": "OK",
        "scope": organization.code if organization else "ALL",
        "basis": (
            "DPDP Act s.8(6) and DPDP Rules 2025 Rule 7: every personal data breach the "
            "fiduciary became aware of, with its awareness/notification clocks, the filings "
            "made to the Board, CERT-In and affected principals, and each filing's content "
            "hash. Filtered on detected_at within the period."
        ),
        "count": len(records),
        "records": records,
        "nil_return_note": (
            "An empty record list is a positive statement that this register holds no breach "
            "detected in the period - not an absence of capability."
        ),
    }


def _retention_section(db: Session, start: datetime, end: datetime, tenant_id: Optional[int]) -> dict:
    q = db.query(RetentionAction).filter(
        RetentionAction.executed_at >= start, RetentionAction.executed_at <= end
    )
    if tenant_id is not None:
        q = q.filter(RetentionAction.tenant_id == tenant_id)
    actions = q.order_by(RetentionAction.id.asc()).all()
    # audit=False: building a pack is a read, and writing a RETENTION_SCAN_RUN
    # audit row here would mutate the very ledger the pack is extracting.
    scan = retention_scan(db, audit=False)
    return {
        "section": "retention_actions",
        "status": "OK",
        "records": [
            {
                "id": a.id, "record_class": a.record_class, "action": a.action,
                "cutoff": _iso(a.cutoff), "floor_days_at_execution": a.floor_days_at_execution,
                "retention_days_at_execution": a.retention_days_at_execution,
                "rows_affected": a.rows_affected, "dry_run": a.dry_run,
                "actor_username": a.actor_username, "executed_at": _iso(a.executed_at),
            }
            for a in actions
        ],
        "scan": {
            "generated_at": _iso(scan["generated_at"]),
            "deleted_before_floor": scan["deleted_before_floor"],
            "violations": scan["violations"],
            "compliant": scan["compliant"],
            "classes": [
                {
                    "record_class": c["record_class"],
                    "effective_floor_days": c["effective_floor_days"],
                    "configured_retention_days": c["configured_retention_days"],
                    "schedule_meets_floor": c["schedule_meets_floor"],
                    "rows_total": c["rows_total"],
                    "rows_within_floor": c["rows_within_floor"],
                    "rows_deleted_before_floor": c["rows_deleted_before_floor"],
                    "enforcement": c["enforcement"],
                }
                for c in scan["classes"]
            ],
        },
    }


def _integrity_proofs(db: Session, sections: list[dict]) -> dict:
    """verify_chain over the whole ledger, the per-tenant chain heads, and a
    signed manifest hash over every other section of this pack."""
    chain = verify_chain(db)
    heads = []
    for tenant_id in [row[0] for row in db.query(AuditLog.tenant_id).distinct().all()]:
        tail = (
            db.query(AuditLog)
            .filter(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.id.desc())
            .first()
        )
        if tail is not None:
            heads.append({
                "tenant_id": tenant_id, "head_audit_log_id": tail.id,
                "head_entry_hash": tail.entry_hash, "head_created_at": _iso(tail.created_at),
            })
    manifest = json.dumps(sections, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    manifest_hash = hashlib.sha256(manifest).hexdigest()
    return {
        "section": "integrity_proofs",
        "status": "OK",
        "audit_chain": {
            "mechanism": (
                "app/core/audit_chain.py::verify_chain - entry_hash = sha256(prev_hash + "
                "canonical(row)), chained per tenant, with a database trigger blocking UPDATE "
                "and DELETE on audit_logs for every role."
            ),
            "entries_checked": chain["checked"],
            "tenants": chain["tenants"],
            "broken_links": chain["broken"],
            "intact": not chain["broken"],
        },
        "chain_heads": heads,
        "pack_manifest_hash": manifest_hash,
        "pack_manifest_signature": hmac_signature(manifest_hash),
        "manifest_note": (
            "pack_manifest_hash is a SHA-256 over the canonical JSON of every other section of "
            "this pack, signed with the platform HMAC signing key. Recompute it over the "
            "sections as delivered to prove the pack was not altered after export."
        ),
    }


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------
def build_evidence_pack(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
    tenant_id: Optional[int] = None,
    generated_by: str = "system",
    request_id: Optional[str] = None,
    audit: bool = True,
) -> dict:
    start = _as_utc(period_start)
    end = _as_utc(period_end)
    if start > end:
        raise ValueError("period_start must be on or before period_end")

    organization = db.get(Organization, tenant_id) if tenant_id is not None else None

    sections = [
        _ledger_extract(db, start, end, tenant_id),
        _notice_versions(db, start, end, tenant_id),
        _request_logs(db, start, end, tenant_id),
        _breach_register(db, start, end, organization),
        _retention_section(db, start, end, tenant_id),
    ]
    integrity = _integrity_proofs(db, sections)

    unavailable = [s["section"] for s in sections if s["status"] == "UNAVAILABLE"]
    partial = [s["section"] for s in sections if s["status"] == "PARTIAL"]

    pack = {
        "pack_type": "DPDP_REGULATOR_EVIDENCE_PACK",
        "pack_version": "1",
        "generated_at": utcnow(),
        "generated_by": generated_by,
        "period_start": start,
        "period_end": end,
        "tenant": (
            {"id": organization.id, "code": organization.code, "name": organization.name}
            if organization else {"id": None, "code": "ALL", "name": "All tenants"}
        ),
        "unavailable_sections": unavailable,
        "partial_sections": partial,
        "completeness_note": (
            "Sections listed in unavailable_sections could not be produced because the "
            "underlying feature does not exist in this build; their absence is NOT a finding of "
            "nil. Sections in partial_sections carry their own `coverage` statement."
        ),
        "sections": {s["section"]: s for s in sections},
        "integrity_proofs": integrity,
    }
    pack["sections"]["integrity_proofs"] = integrity

    if audit:
        log_audit(
            db, "EVIDENCE_PACK_EXPORTED", actor_username=generated_by,
            tenant_id=tenant_id,
            reason=(
                f"Regulator evidence pack exported for "
                f"{start.date().isoformat()}..{end.date().isoformat()}"
            ),
            request_id=request_id,
            metadata={
                "period_start": start.isoformat(), "period_end": end.isoformat(),
                "tenant_id": tenant_id,
                "ledger_rows": sections[0]["rows_included"],
                "ledger_total_in_period": sections[0]["total_in_period"],
                "unavailable_sections": unavailable,
                "partial_sections": partial,
                "pack_manifest_hash": integrity["pack_manifest_hash"],
                "audit_chain_intact": integrity["audit_chain"]["intact"],
            },
        )
    return pack
