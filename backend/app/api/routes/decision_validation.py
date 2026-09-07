"""R3-10 (CM-01, D-09): `POST /decisions/evaluate` - consent validation before
processing.

The decision engine itself (`app/services/decision_engine.py::evaluate_decision`)
has existed since R1 and is not touched here: its precedence order (explicit
policy rule DENY -> consent status -> rule `requires_active_consent=False` ->
`purpose.requires_consent=False` -> REQUIRE_CONSENT) and its
`consent_decision_logs` + audit writes are the behaviour this endpoint
publishes, unchanged. This module is the machine-to-machine door onto it, and
its whole job is the three things the engine deliberately does not do:

  **Authenticate.** A tenant-bound integration API key (`X-API-Key`), carrying
  the `decision.evaluate` scope. A new scope rather than reusing
  `integration.write`: asking "what is this named principal's consent state"
  is a different capability from "identify a customer for me", and every
  capability in this codebase is a deliberate per-tenant opt-in (see
  `app/core/api_keys.py`). The legacy unbound key implicitly carries only
  `integration.write`, so it can never reach this endpoint.

  **Scope.** The principal is resolved through
  `app/services/tenancy.py::resolve_customer` with the key's OWN tenant, never
  a caller-supplied one. A `source_app` in the body that differs from the
  key's tenant is a 403, and a principal belonging to another tenant is a 404 -
  indistinguishable from one that does not exist, which is the rule everywhere
  else in this codebase and the one that three rounds of cross-tenant fixes
  established.

  **Bound.** Per (tenant, IP) rate limiting, and a persisted latency/outcome
  sample per call for CM-06/K-42.

A separate router file from the existing `decisions.py` (which serves the
staff-facing `GET /decisions/report`) purely so the two could be built in
parallel; they share the `/decisions` prefix and appear as one group in the
OpenAPI document.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey, require_scope, verify_integration_key
from app.core.database import get_db
from app.core.utils import get_request_id
from app.models.entities import ConsentDecisionLog, DataCategory, ProcessingActivity, Purpose
from app.schemas.artefacts import DecisionEvaluateIn, DecisionEvaluateOut
from app.services.consent_manager import (
    SCOPE_DECISION_EVALUATE,
    decision_limiter,
    measure,
)
from app.services.decision_engine import evaluate_decision
from app.services.tenancy import resolve_customer

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.post("/evaluate", response_model=DecisionEvaluateOut)
def evaluate(
    payload: DecisionEvaluateIn,
    request: Request,
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Validate consent before processing.

    Returns the engine's verdict verbatim - `ALLOW`, `DENY`, `REQUIRE_CONSENT`,
    `WITHDRAWN` or `EXPIRED` - together with `allowed`, a plain boolean the
    calling system can gate on without having to know the taxonomy. Every call
    writes a `consent_decision_logs` row and an audit entry, so "we checked
    before processing" is evidenced on our side rather than asserted by the
    caller.
    """
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with measure(db, endpoint="POST /decisions/evaluate", method="POST", request_id=request_id) as sample:
        if resolved_key.tenant_code is None or resolved_key.tenant_id is None:
            # The legacy unbound key names whatever source_app it likes and
            # has proven no tenant identity. require_scope already refuses it
            # this scope; refused explicitly here too, because "which tenant's
            # principal is this" is the entire security question of this route.
            raise HTTPException(
                status_code=403,
                detail=(
                    "This endpoint requires a tenant-bound API key; the shared legacy "
                    "integration key cannot identify a Data Fiduciary."
                ),
            )
        sample["tenant_id"] = resolved_key.tenant_id

        if payload.source_app and payload.source_app != resolved_key.tenant_code:
            raise HTTPException(
                status_code=403,
                detail="source_app does not match the tenant bound to this API key",
            )
        require_scope(resolved_key, SCOPE_DECISION_EVALUATE)

        client_ip = request.client.host if request.client else "unknown"
        if not decision_limiter.allow(f"decision:{resolved_key.tenant_code}:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many decision requests")

        source_app = resolved_key.tenant_code
        customer = resolve_customer(db, source_app=source_app, external_id=payload.customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="No such principal at this tenant")

        purpose = db.query(Purpose).filter(Purpose.code == payload.purpose_code).first()
        if not purpose:
            raise HTTPException(status_code=404, detail=f"Unknown purpose '{payload.purpose_code}'")
        data_category = (
            db.query(DataCategory).filter(DataCategory.code == payload.data_category_code).first()
        )
        if not data_category:
            raise HTTPException(
                status_code=404, detail=f"Unknown data category '{payload.data_category_code}'"
            )
        activity = (
            db.query(ProcessingActivity)
            .filter(ProcessingActivity.code == payload.processing_activity_code)
            .first()
        )
        if not activity:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown processing activity '{payload.processing_activity_code}'",
            )

        decision = evaluate_decision(
            db,
            customer,
            purpose,
            data_category,
            activity,
            requested_by=payload.requested_by or "integration",
            source_app=source_app,
            persist=True,
            request_id=request_id,
        )

        # The engine returns its verdict, not the row it wrote. Re-read the
        # row it just committed by this request's own correlation id plus the
        # full decision identity, so the id reported back is unambiguously
        # this call's - not a concurrent one for the same principal.
        log = (
            db.query(ConsentDecisionLog)
            .filter(
                ConsentDecisionLog.request_id == request_id,
                ConsentDecisionLog.customer_id == customer.id,
                ConsentDecisionLog.purpose_id == purpose.id,
                ConsentDecisionLog.data_category_id == data_category.id,
                ConsentDecisionLog.processing_activity_id == activity.id,
                ConsentDecisionLog.source_app == source_app,
            )
            .order_by(ConsentDecisionLog.id.desc())
            .first()
        )

        return DecisionEvaluateOut(
            decision=decision.decision,
            allowed=decision.allowed,
            reason=decision.reason,
            customer_id=payload.customer_id,
            purpose_code=purpose.code,
            data_category_code=data_category.code,
            processing_activity_code=activity.code,
            source_app=source_app,
            consent_status=decision.consent.status if decision.consent else None,
            consent_version=decision.consent.consent_version if decision.consent else None,
            consent_expires_at=decision.consent.expires_at if decision.consent else None,
            policy_code=decision.policy.code if decision.policy else None,
            policy_version=decision.policy_version_number,
            decision_log_id=log.id if log else None,
            evaluated_at=log.evaluated_at if log else datetime.now(timezone.utc),
            duration_ms=int((time.perf_counter() - sample["started"]) * 1000),
            request_id=request_id,
        )
