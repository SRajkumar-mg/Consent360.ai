"""R1-04 / R1-05 reporting and lawful-basis gateway.

Groups several R1 responsibilities under `/reports` and `/decisions`:
- /decisions/evaluate  — REST decision endpoint backed by decision_engine
  (`evaluate_decision`), honouring lawful-basis gating (R1-05), re-consent
  gating (R1-09), latency + tenant capture (R1-04).
- /reports/decisions  — p95 latency and decision breakdown (R1-04).
- /reports/lawful-gateway — purposes grouped by lawful basis (R1-05).
- /reports/compliance-evidence-pack — R1-11 evidence pack (stubbed here,
  extended by the audit/JIT route).
"""

import time
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_REPORTS_VIEW, PERM_INTEGRATION
from app.api.deps import verify_integration_key
from app.models.entities import (
    ConsentDecisionLog,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
    User,
)
from app.services import decision_engine
from app.services.audit import log_audit

router = APIRouter(tags=["reports"])

# Decisions router (used by both integration and internal UI)
decisions_router = APIRouter(prefix="/decisions", tags=["decisions"])


@decisions_router.post("/evaluate")
def evaluate(
    body: dict,
    db: Session = Depends(get_db),
    _: None = Depends(verify_integration_key),
):
    """Evaluate a decision for a principal + purpose + category + activity.

    Body: {customer_external_id, purpose_code, data_category_code,
           processing_activity_code, requested_by?, source_app?, request_id?}
    """
    customer = db.query(Customer).filter(
        Customer.external_id == body.get("customer_external_id")
    ).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    purpose = db.query(Purpose).filter(Purpose.code == body.get("purpose_code")).first()
    dc = db.query(DataCategory).filter(DataCategory.code == body.get("data_category_code")).first()
    pa = db.query(ProcessingActivity).filter(
        ProcessingActivity.code == body.get("processing_activity_code")
    ).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    if not dc:
        raise HTTPException(status_code=404, detail="Data category not found")
    if not pa:
        raise HTTPException(status_code=404, detail="Processing activity not found")

    d = decision_engine.evaluate_decision(
        db,
        customer,
        purpose,
        dc,
        pa,
        requested_by=body.get("requested_by", "integration"),
        source_app=body.get("source_app", ""),
        request_id=body.get("request_id"),
        tenant_id=customer.tenant_id,
    )
    return {
        "decision": d.decision,
        "allowed": d.allowed,
        "reason": d.reason,
        "consent_id": d.consent.id if d.consent else None,
        "consent_status": d.consent.status if d.consent else None,
        "purpose_code": purpose.code,
        "data_category_code": dc.code,
        "processing_activity_code": pa.code,
    }


@router.get("/decisions", dependencies=[Depends(require_permission(PERM_REPORTS_VIEW))])
def decision_report(
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_REPORTS_VIEW)),
):
    q = db.query(ConsentDecisionLog)
    scope = get_org_scope(user)
    if scope:
        q = q.filter(
            (ConsentDecisionLog.tenant_id == getattr(user, "tenant_id", None))
            | (ConsentDecisionLog.source_app == scope)
        )
    if date_from:
        q = q.filter(ConsentDecisionLog.evaluated_at >= date_from)
    if date_to:
        q = q.filter(ConsentDecisionLog.evaluated_at <= date_to)

    breakdown = dict(
        db.query(ConsentDecisionLog.decision, func.count(ConsentDecisionLog.id))
        .filter(ConsentDecisionLog.evaluated_at >= (date_from or "1970-01-01"))
        .group_by(ConsentDecisionLog.decision)
        .all()
    )

    latencies = [
        r[0] for r in db.query(ConsentDecisionLog.latency_ms)
        .filter(ConsentDecisionLog.latency_ms.isnot(None))
        .all()
    ]
    p95 = _percentile(latencies, 95) if latencies else None
    return {
        "total_decisions": q.count(),
        "decision_breakdown": breakdown,
        "p95_latency_ms": p95,
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
        "sample_count": len(latencies),
    }


@router.get("/lawful-gateway", dependencies=[Depends(require_permission(PERM_REPORTS_VIEW))])
def lawful_gateway_report(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_REPORTS_VIEW))):
    """R1-05: list purposes grouped by their lawful basis (which non-consent
    bases permit processing without a consent record)."""
    rows = db.query(Purpose).order_by(Purpose.lawful_basis, Purpose.code).all()
    grouped: dict[str, list[dict]] = {}
    for p in rows:
        grouped.setdefault(p.lawful_basis or "CONSENT", []).append({
            "code": p.code,
            "name": p.name,
            "requires_consent": p.requires_consent,
        })
    return {"gateway": grouped}


@router.get("/compliance-evidence-pack", dependencies=[Depends(require_permission(PERM_REPORTS_VIEW))])
def compliance_evidence_pack(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_REPORTS_VIEW))):
    """R1-11: aggregate the evidence needed for a regulator submission."""
    purposes = db.query(Purpose).count()
    from app.models.entities import Consent, ConsentEvidence, AuditLog, Policy
    return {
        "purposes": purposes,
        "policies_active": db.query(Policy).filter(Policy.is_active.is_(True), Policy.status == "ACTIVE").count(),
        "active_consents": db.query(Consent).filter(Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"])).count(),
        "evidence_rows": db.query(ConsentEvidence).count(),
        "audit_rows": db.query(AuditLog).count(),
        "ledger_chain": None,  # populated by /audit/verify-chain
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac