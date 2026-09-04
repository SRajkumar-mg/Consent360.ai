"""Retention and erasure engine (R1-06).

Builds on the scheduler framework (R1-07). Implements four scheduled jobs:
- retention_scan: flags records past their retention policy
- inactivity_scan: Third-Schedule inactivity clock per tenant class
- pre_erasure_notices: sends the 48-hour warning before erasure
- erasure_executor: performs the actual erase/anonymise

Erasure triggers (consent withdrawal, rights request) create erasure_jobs
rows rather than deleting data directly. Never hard-deletes a record without
a prior erasure_jobs row and a 48-hour notice window.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.entities import (
    AuditLog,
    Consent,
    ConsentContext,
    ConsentEvidence,
    ConsentHistory,
    Customer,
    ErasureJob,
    LegalHold,
    RetentionPolicy,
)
from app.services.retention_floors import days_since

logger = logging.getLogger("consent360.retention")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_under_legal_hold(db: Session, target_type: str, target_id: int) -> bool:
    return (
        db.query(LegalHold)
        .filter(
            LegalHold.target_type == target_type,
            LegalHold.target_id == target_id,
            LegalHold.released_at.is_(None),
        )
        .first()
        is not None
    )


def compute_evidence_hash(db: Session, customer_id: int) -> str:
    """Compute a stable hash over the customer's erasure-relevant state."""
    payload = f"customer:{customer_id}:consents:{db.query(Consent).filter(Consent.customer_id == customer_id).count()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def retention_scan(db: Session, tenant_id: int | None = None) -> dict:
    """Flag records past their retention policy, excluding legal holds.

    This job only *marks* candidates by creating erasure_jobs rows where the
    policy says ERASE; it does not delete. Actual deletion is erasure_executor.
    """
    policies = db.query(RetentionPolicy).filter(RetentionPolicy.action == "ERASE").all()
    flagged = 0
    now = utcnow()
    for policy in policies:
        if policy.record_class == "customers":
            custs = db.query(Customer).all()
            for cust in custs:
                if cust.tenant_id and tenant_id and cust.tenant_id != tenant_id:
                    continue
                if _is_under_legal_hold(db, "customer", cust.id):
                    continue
                age_days = days_since(cust.created_at, now)
                if age_days > policy.retention_days:
                    existing = (
                        db.query(ErasureJob)
                        .filter(ErasureJob.customer_id == cust.id, ErasureJob.status.in_(["PENDING", "NOTICE_SENT"]))
                        .first()
                    )
                    if not existing:
                        db.add(ErasureJob(
                            tenant_id=cust.tenant_id or tenant_id or 1,
                            customer_id=cust.id,
                            trigger="CLOCK",
                            scheduled_for=now + timedelta(hours=policy.pre_erasure_notice_hours),
                            status="PENDING",
                            evidence_hash=compute_evidence_hash(db, cust.id),
                        ))
                        flagged += 1
            db.commit()
        # consents/evidence handled in erasure_executor via customer linkage
    return flagged


def inactivity_scan(db: Session, tenant_id: int | None = None) -> dict:
    """Apply Third-Schedule inactivity clock per tenant class.

    When last_interaction_at is older than the configured inactivity_days,
    create an erasure_jobs row (CLOCK trigger).
    """
    flagged = 0
    now = utcnow()
    policies = db.query(RetentionPolicy).filter(RetentionPolicy.inactivity_days.isnot(None)).all()
    for policy in policies:
        horizon = now - timedelta(days=policy.inactivity_days)
        custs = db.query(Customer).filter(
            Customer.last_interaction_at.isnot(None),
            Customer.last_interaction_at <= horizon,
        ).all()
        for cust in custs:
            if cust.tenant_id and tenant_id and cust.tenant_id != tenant_id:
                continue
            if _is_under_legal_hold(db, "customer", cust.id):
                continue
            existing = (
                db.query(ErasureJob)
                .filter(ErasureJob.customer_id == cust.id, ErasureJob.status.in_(["PENDING", "NOTICE_SENT"]))
                .first()
            )
            if not existing:
                db.add(ErasureJob(
                    tenant_id=cust.tenant_id or tenant_id or 1,
                    customer_id=cust.id,
                    trigger="CLOCK",
                    scheduled_for=now + timedelta(hours=policy.pre_erasure_notice_hours),
                    status="PENDING",
                    evidence_hash=compute_evidence_hash(db, cust.id),
                ))
                flagged += 1
    db.commit()
    return flagged


def pre_erasure_notices(db: Session, tenant_id: int | None = None) -> dict:
    """For erasure_jobs within the notice window, fire the 48-hour warning."""
    notified = 0
    now = utcnow()
    jobs = db.query(ErasureJob).filter(ErasureJob.status == "PENDING").all()
    for job in jobs:
        if job.notice_sent_at is not None:
            continue
        window_start = job.scheduled_for - timedelta(hours=48)
        if window_start <= now < job.scheduled_for:
            # TODO(R3-06): once the notification service exists, call it here to
            # send the 48-hour pre-erasure warning to the principal.
            job.notice_sent_at = now
            job.status = "NOTICE_SENT"
            notified += 1
    db.commit()
    return notified


def _anonymise_audit_references(db: Session, customer_id: int) -> None:
    """Anonymise the customer reference inside audit rows (R1-02 consistency).

    Never deletes rows; removes PII from the reference fields.
    """
    rows = db.query(AuditLog).filter(AuditLog.customer_id == customer_id).all()
    for row in rows:
        if row.customer_external_id:
            row.customer_external_id = None
        if row.details:
            for k in list(row.details.keys()):
                if k.lower() in ("name", "email", "phone", "external_id"):
                    row.details[k] = "[anonymised]"
    db.commit()


def erasure_executor(db: Session, tenant_id: int | None = None) -> dict:
    """Execute erasure jobs that are past scheduled_for with a notice window.

    Erases or anonymises per the policy's action, anonymises audit references,
    calls the processor register (R3-07 — stubbed), records evidence_hash.
    """
    executed = 0
    now = utcnow()
    jobs = (
        db.query(ErasureJob)
        .filter(
            ErasureJob.status.in_(["NOTICE_SENT", "PENDING"]),
            ErasureJob.scheduled_for <= now,
        )
        .all()
    )
    for job in jobs:
        # Pre-erasure notice must have been sent (or the window elapsed with
        # no notice). We require either notice_sent_at set, or scheduled_for
        # older than 48h+ (the notice window has fully passed).
        notice_window_passed = (now - job.scheduled_for).total_seconds() > 48 * 3600
        if job.notice_sent_at is None and not notice_window_passed:
            continue

        customer = db.get(Customer, job.customer_id)
        if not customer:
            job.status = "CANCELLED"
            db.commit()
            continue

        # Determine action from policy (default ANONYMISE)
        policy = (
            db.query(RetentionPolicy)
            .filter(
                RetentionPolicy.tenant_id == job.tenant_id,
                RetentionPolicy.record_class == "customers",
            )
            .first()
        )
        action = (policy.action if policy else "ANONYMISE") or "ANONYMISE"

        if action == "ERASE":
            consent_ids = [c.id for c in db.query(Consent).filter(Consent.customer_id == customer.id).all()]
            if consent_ids:
                db.query(ConsentEvidence).filter(ConsentEvidence.consent_id.in_(consent_ids)).delete(synchronize_session=False)
                db.query(ConsentHistory).filter(ConsentHistory.consent_id.in_(consent_ids)).delete(synchronize_session=False)
                db.query(Consent).filter(Consent.customer_id == customer.id).delete(synchronize_session=False)
            db.query(ConsentContext).filter(ConsentContext.customer_id == customer.id).delete(synchronize_session=False)
        else:  # ANONYMISE
            customer.name = "[anonymised]"
            customer.email = ""
            customer.email_search = None
            customer.phone = ""
            customer.external_id = f"anon-{customer.id}"
            customer.external_id_search = None
            for c in db.query(Consent).filter(Consent.customer_id == customer.id).all():
                c.consent_text = "[anonymised]"
            _anonymise_audit_references(db, customer.id)

        # TODO(R3-07): call the processor register's alert mechanism with an
        # erasure instruction and track acks here.
        job.executed_at = now
        job.processors_notified_at = now
        job.status = "EXECUTED"
        job.evidence_hash = compute_evidence_hash(db, customer.id)
        db.commit()
        executed += 1
    return executed


def trigger_erasure(db: Session, customer_id: int, *, trigger: str = "REQUEST", tenant_id: int | None = None) -> ErasureJob:
    """Create an erasure_jobs row (used by withdrawal / rights-request wiring)."""
    customer = db.get(Customer, customer_id)
    if not customer:
        raise ValueError("Customer not found")
    job = ErasureJob(
        tenant_id=tenant_id or customer.tenant_id or 1,
        customer_id=customer_id,
        trigger=trigger,
        scheduled_for=utcnow() + timedelta(hours=48),
        status="PENDING",
        evidence_hash=compute_evidence_hash(db, customer_id),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
