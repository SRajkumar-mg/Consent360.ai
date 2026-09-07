from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.utils import get_request_id
from app.models.entities import AuditLog


def log_audit(
    db: Session,
    event: str,
    *,
    actor_username: str = "system",
    actor_id: Optional[str] = None,
    actor_type: str = "SYSTEM",
    actor_role: str = "",
    source_app: str = "",
    tenant_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    customer_id: Optional[int] = None,
    customer_external_id: Optional[str] = None,
    consent_id: Optional[int] = None,
    purpose_id: Optional[int] = None,
    purpose_code: Optional[str] = None,
    policy_id: Optional[int] = None,
    policy_code: Optional[str] = None,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    consent_version: Optional[int] = None,
    policy_version: Optional[int] = None,
    decision: Optional[str] = None,
    reason: str = "",
    request_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> AuditLog:
    from app.core.audit_chain import GENESIS_HASH, compute_entry_hash
    from app.services.tenancy import resolve_tenant_id

    resolved_tenant_id = tenant_id if tenant_id is not None else resolve_tenant_id(db, source_app)

    # Serialise the "read this tenant's chain tail -> compute hash -> insert"
    # sequence per tenant. Without this, two concurrent transactions writing
    # audit rows for the SAME tenant (e.g. two simultaneous CRM banner
    # submissions, which share the CRM_APP tenant) can both read the same
    # committed prev_hash before either commits, so both new rows claim the
    # same predecessor - forking the chain. verify_chain walks each tenant's
    # rows in id order and would then flag whichever row committed second as
    # broken, even though nothing was tampered with.
    #
    # A transaction-scoped Postgres advisory lock, keyed per tenant, closes
    # this without a table lock: it only blocks other writers to the SAME
    # tenant (a different tenant's audit writes are unaffected), it is
    # released automatically at COMMIT/ROLLBACK (so it composes correctly
    # with commit=False call sites that keep writing inside the same
    # transaction before their own final commit), and re-acquiring it from
    # the same session/transaction is a no-op rather than a deadlock ("if a
    # session already holds a given advisory lock, additional requests by it
    # will always succeed" - Postgres docs). SELECT ... FOR UPDATE on the
    # tail row was the other option considered, but it requires a real row
    # to lock (the very first write for a brand-new tenant has none yet, so
    # it would need a separate bootstrap path) and ties the lock's lifetime
    # to a row that this append-only table's own semantics say should never
    # be touched again once written; the advisory lock has neither problem.
    #
    # The lock key hashes a namespaced string ("auditlog:<tenant_id>") rather
    # than the raw tenant_id, so this lock's keyspace cannot collide with an
    # unrelated advisory lock elsewhere in the app that happens to reuse the
    # same small integer for a different purpose.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"auditlog:{resolved_tenant_id}"},
    )

    # audit_logs is append-only (a Postgres trigger blocks UPDATE and
    # DELETE), so entry_hash must be part of the row's single INSERT rather
    # than a follow-up UPDATE after a flush assigns the id. Pre-allocate the
    # id from the table's own sequence and stamp created_at ourselves so the
    # hash can be computed - and the whole row built - before anything is
    # written.
    next_id = db.execute(
        text("SELECT nextval(pg_get_serial_sequence('audit_logs', 'id'))")
    ).scalar()
    created_at = datetime.now(timezone.utc)

    prev = (
        db.query(AuditLog.entry_hash)
        .filter(AuditLog.tenant_id == resolved_tenant_id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    prev_hash = prev[0] if prev and prev[0] else GENESIS_HASH

    entry = AuditLog(
        id=next_id,
        event=event,
        actor_username=actor_username,
        actor_id=actor_id,
        actor_type=actor_type,
        actor_role=actor_role,
        source_app=source_app,
        tenant_id=resolved_tenant_id,
        ip_address=ip_address,
        user_agent=user_agent,
        customer_id=customer_id,
        customer_external_id=customer_external_id,
        consent_id=consent_id,
        purpose_id=purpose_id,
        purpose_code=purpose_code,
        policy_id=policy_id,
        policy_code=policy_code,
        old_status=old_status,
        new_status=new_status,
        consent_version=consent_version,
        policy_version=policy_version,
        decision=decision,
        reason=reason,
        request_id=request_id or get_request_id(),
        details=metadata or {},
        created_at=created_at,
        prev_hash=prev_hash,
    )
    entry.entry_hash = compute_entry_hash(prev_hash, entry)
    db.add(entry)
    db.flush()

    if commit:
        db.commit()
        db.refresh(entry)
    return entry
