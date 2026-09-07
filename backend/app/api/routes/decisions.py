"""R1-11 (D-09, S-03, K-12): the decision-engine outcome reporting endpoint.

`consent_decision_logs` (app/services/decision_engine.py::_finish) has been
written on every evaluate_decision call since R1's decision engine shipped,
but until this task nothing ever read it back out - D-09/S-03's gap was
"logged, not reported". This is that report: outcome counts
(ALLOW/DENY/REQUIRE_CONSENT/WITHDRAWN/EXPIRED) bucketed per period.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_VIEW
from app.models.entities import DECISION_OUTCOMES, ConsentDecisionLog, User
from app.schemas.schemas import DecisionPeriodBucket, DecisionReportOut

router = APIRouter(prefix="/decisions", tags=["decisions"])

_TRUNC_UNIT = {"day": "day", "week": "week", "month": "month"}


def _empty_bucket(period: str) -> DecisionPeriodBucket:
    return DecisionPeriodBucket(period=period)


@router.get("/report", response_model=DecisionReportOut)
def decision_report(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    group_by: str = Query(default="day", pattern="^(day|week|month|total)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    scope = get_org_scope(user)

    q = db.query(ConsentDecisionLog)
    if scope:
        q = q.filter(ConsentDecisionLog.source_app == scope)
    if date_from:
        q = q.filter(ConsentDecisionLog.evaluated_at >= date_from)
    if date_to:
        q = q.filter(ConsentDecisionLog.evaluated_at <= date_to)

    totals = _empty_bucket("total")
    buckets_by_period: dict[str, DecisionPeriodBucket] = {}

    if group_by == "total":
        rows = q.with_entities(ConsentDecisionLog.decision, func.count(ConsentDecisionLog.id)).group_by(
            ConsentDecisionLog.decision
        ).all()
        for decision, count in rows:
            if decision in DECISION_OUTCOMES:
                setattr(totals, decision, count)
            totals.total += count
        buckets = [totals]
    else:
        period_expr = func.date_trunc(_TRUNC_UNIT[group_by], ConsentDecisionLog.evaluated_at)
        rows = (
            q.with_entities(period_expr.label("period"), ConsentDecisionLog.decision, func.count(ConsentDecisionLog.id))
            .group_by("period", ConsentDecisionLog.decision)
            .order_by("period")
            .all()
        )
        for period_dt, decision, count in rows:
            key = period_dt.date().isoformat() if group_by != "week" else period_dt.date().isoformat()
            bucket = buckets_by_period.setdefault(key, _empty_bucket(key))
            if decision in DECISION_OUTCOMES:
                setattr(bucket, decision, getattr(bucket, decision) + count)
            bucket.total += count
            if decision in DECISION_OUTCOMES:
                setattr(totals, decision, getattr(totals, decision) + count)
            totals.total += count
        buckets = [buckets_by_period[k] for k in sorted(buckets_by_period)]

    return DecisionReportOut(group_by=group_by, date_from=date_from, date_to=date_to, buckets=buckets, totals=totals)
