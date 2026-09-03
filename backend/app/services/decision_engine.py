from datetime import datetime, timezone
from time import perf_counter
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import (
    Consent,
    ConsentDecisionLog,
    Customer,
    DataCategory,
    Policy,
    ProcessingActivity,
    Purpose,
)
from app.services.audit import log_audit


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _active_consent_statuses() -> tuple[str, ...]:
    return ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")


class Decision:
    def __init__(
        self,
        decision: str,
        reason: str,
        allowed: bool,
        *,
        consent: Optional[Consent] = None,
        policy: Optional[Policy] = None,
        policy_version_number: Optional[int] = None,
        policy_version_id: Optional[int] = None,
    ):
        self.decision = decision
        self.reason = reason
        self.allowed = allowed
        self.consent = consent
        self.policy = policy
        self.policy_version_number = policy_version_number
        self.policy_version_id = policy_version_id


def get_active_policy(db: Session) -> Optional[Policy]:
    policies = (
        db.query(Policy)
        .filter(Policy.is_active.is_(True), Policy.status == "ACTIVE")
        .order_by(Policy.id.asc())
        .all()
    )
    for policy in policies:
        for version in sorted(policy.versions, key=lambda v: v.version_number, reverse=True):
            if version.is_current:
                return policy
    return None


def find_applicable_rule(policy: Policy, purpose_code: str, dc_code: str, pa_code: str):
    version = None
    for v in sorted(policy.versions, key=lambda x: x.version_number, reverse=True):
        if v.is_current:
            version = v
            break
    if not version:
        return None, None, version
    for rule in version.rules or []:
        if (
            rule.get("purpose_code") == purpose_code
            and rule.get("data_category_code") == dc_code
            and rule.get("processing_activity_code") == pa_code
        ):
            return rule, version, version
    return None, version, version


def evaluate_decision(
    db: Session,
    customer: Customer,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    *,
    requested_by: str = "system",
    source_app: str = "",
    persist: bool = True,
    request_id: Optional[str] = None,
    tenant_id: Optional[int] = None,
) -> Decision:
    """Deterministic, rule-based consent decision evaluation."""
    start = perf_counter()
    now = utcnow()

    # R1-05: lawful-basis gateway. If the purpose is not consent-based, the
    # decision is governed by the lawful basis (S7), not by a consent record.
    if purpose.lawful_basis != "CONSENT":
        d = Decision(
            "ALLOW",
            f"Purpose {purpose.name} relies on lawful basis {purpose.lawful_basis} "
            f"(not consent), so consent is not required for this processing.",
            True,
            policy=None,
        )
        return _finish(d, db, customer, purpose, data_category, processing_activity,
                       requested_by, source_app, persist, request_id, tenant_id, start)

    policy = get_active_policy(db)
    rule = None
    policy_version = None
    policy_version_id = None
    policy_code = None
    policy_version_number = None

    if policy:
        rule, policy_version, _ = find_applicable_rule(policy, purpose.code, data_category.code, processing_activity.code)
        if policy_version:
            policy_code = policy.code
            policy_version_number = policy_version.version_number
            policy_version_id = policy_version.id

    if policy and rule:
        rule_decision = rule.get("decision", "ALLOW")
        if rule_decision == "DENY":
            d = Decision(
                "DENY",
                f"Policy {policy.code} v{policy_version_number} explicitly denies processing "
                f"{processing_activity.name} on {data_category.name} for {purpose.name}.",
                False,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

    # Find the most relevant consent record
    consent = (
        db.query(Consent)
        .filter(
            Consent.customer_id == customer.id,
            Consent.purpose_id == purpose.id,
            Consent.data_category_id == data_category.id,
            Consent.processing_activity_id == processing_activity.id,
        )
        .order_by(Consent.consent_version.desc(), Consent.created_at.desc())
        .first()
    )

    if consent:
        if consent.status == "WITHDRAWN":
            d = Decision(
                "WITHDRAWN",
                "Consent for this purpose has been withdrawn by the data principal.",
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

        if consent.status == "DENIED":
            d = Decision(
                "DENY",
                "Consent for this purpose has been explicitly denied.",
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

        expired = consent.expires_at is not None and consent.expires_at <= now
        if consent.status == "EXPIRED" or expired:
            d = Decision(
                "EXPIRED",
                "Consent for this purpose has expired and may no longer be relied upon.",
                False,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

        if consent.status in _active_consent_statuses() and not expired:
            # R1-09: a material change flagged re_consent_required gates processing
            # until the principal has explicitly re-consented (status UPDATED reset).
            if consent.re_consent_required:
                d = Decision(
                    "RE_CONSENT_REQUIRED",
                    "A material change to the consent requires the data principal "
                    "to re-consent before processing may continue.",
                    False,
                    consent=consent,
                    policy=policy,
                    policy_version_number=policy_version_number,
                    policy_version_id=policy_version_id,
                )
                return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)
            reason = (
                f"Valid active consent exists for {purpose.name} "
                f"(status {consent.status}, version {consent.consent_version}) "
                f"and the consent has not expired or been withdrawn."
            )
            d = Decision(
                "ALLOW",
                reason,
                True,
                consent=consent,
                policy=policy,
                policy_version_number=policy_version_number,
                policy_version_id=policy_version_id,
            )
            return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

    # No valid consent
    if rule and rule.get("requires_active_consent", True) is False:
        reason = (
            f"No active consent required by policy {policy_code} for {purpose.name}, "
            f"processing {data_category.name} for {processing_activity.name}."
        )
        d = Decision("ALLOW", reason, True, policy=policy, policy_version_number=policy_version_number, policy_version_id=policy_version_id)
        return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

    if purpose.requires_consent is False:
        reason = (
            f"Purpose {purpose.name} does not require consent (legal basis {purpose.legal_basis}); "
            f"processing {processing_activity.name} on {data_category.name} is permitted."
        )
        d = Decision("ALLOW", reason, True, policy=policy, policy_version_number=policy_version_number, policy_version_id=policy_version_id)
        return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)

    d = Decision(
        "REQUIRE_CONSENT",
        f"No valid consent exists for {purpose.name} processing {data_category.name} "
        f"for {processing_activity.name}. Consent must be obtained before processing.",
        False,
        policy=policy,
        policy_version_number=policy_version_number,
        policy_version_id=policy_version_id,
    )
    return _finish(d, db, customer, purpose, data_category, processing_activity, requested_by, source_app, persist, request_id, tenant_id, start)


def _finish(
    d: Decision,
    db: Session,
    customer: Customer,
    purpose: Purpose,
    data_category: DataCategory,
    processing_activity: ProcessingActivity,
    requested_by: str,
    source_app: str,
    persist: bool,
    request_id: Optional[str],
    tenant_id: Optional[int] = None,
    start_ms: Optional[float] = None,
) -> Decision:
    latency_ms = int((perf_counter() - start_ms) * 1000) if start_ms else 0
    if not persist:
        return d
    log = ConsentDecisionLog(
        customer_id=customer.id,
        purpose_id=purpose.id,
        data_category_id=data_category.id,
        processing_activity_id=processing_activity.id,
        consent_id=d.consent.id if d.consent else None,
        decision=d.decision,
        reason=d.reason,
        consent_status=d.consent.status if d.consent else None,
        consent_version=d.consent.consent_version if d.consent else None,
        policy_id=d.policy.id if d.policy else None,
        policy_version_id=d.policy_version_id,
        policy_version_number=d.policy_version_number,
        requested_by=requested_by,
        source_app=source_app,
        request_id=request_id,
        tenant_id=tenant_id if tenant_id is not None else (customer.tenant_id if customer.tenant_id else 1),
        latency_ms=latency_ms,
        details={"purpose_code": purpose.code, "data_category_code": data_category.code,
                 "processing_activity_code": processing_activity.code},
        evaluated_at=utcnow(),
    )
    db.add(log)
    log_audit(
        db,
        "DECISION_EVALUATED",
        actor_username=requested_by,
        source_app=source_app,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        consent_id=d.consent.id if d.consent else None,
        purpose_id=purpose.id,
        purpose_code=purpose.code,
        policy_id=d.policy.id if d.policy else None,
        policy_code=d.policy.code if d.policy else None,
        consent_version=d.consent.consent_version if d.consent else None,
        policy_version=d.policy_version_number,
        decision=d.decision,
        reason=d.reason,
        request_id=request_id,
        tenant_id=tenant_id if tenant_id is not None else (customer.tenant_id if customer.tenant_id else 1),
        commit=False,
    )
    db.commit()
    db.refresh(log)
    return d
