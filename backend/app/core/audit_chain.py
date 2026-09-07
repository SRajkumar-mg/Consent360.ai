"""Append-only hash chain over audit_logs, one chain per tenant.

Each row's entry_hash = sha256(prev_hash + canonical_json(row)). Rows written
before this feature shipped have entry_hash = NULL and are skipped by
verify_chain rather than treated as tamper evidence. (The dev database's 575
pre-existing rows are the exception: the migration that adds these columns
backfills them into a valid chain using this exact function, so in practice
only rows written before *that* migration would ever be NULL.)
"""
import hashlib
import json
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.entities import AuditLog

GENESIS_HASH = "0" * 64


def _canonical(entry: AuditLog) -> str:
    # Postgres TIMESTAMPTZ columns round-trip through psycopg2 converted to
    # the connection's session timezone, not necessarily UTC - a row loaded
    # fresh from the DB (as verify_chain always does) can report the same
    # instant with a different UTC offset than the tz-aware value log_audit
    # stamped just before insert. Normalising to UTC here makes the
    # canonical payload - and therefore the hash - stable regardless of
    # which representation happens to come back.
    created_at = entry.created_at.astimezone(timezone.utc) if entry.created_at else None
    payload = {
        "id": entry.id,
        "event": entry.event,
        "actor_username": entry.actor_username,
        "actor_type": entry.actor_type,
        "tenant_id": entry.tenant_id,
        "customer_id": entry.customer_id,
        "consent_id": entry.consent_id,
        "old_status": entry.old_status,
        "new_status": entry.new_status,
        "reason": entry.reason,
        "created_at": created_at.isoformat() if created_at else None,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def compute_entry_hash(prev_hash: str, entry: AuditLog) -> str:
    return hashlib.sha256((prev_hash + _canonical(entry)).encode("utf-8")).hexdigest()


def verify_chain(db: Session) -> dict:
    """Recompute each tenant's chain; report the first broken link per tenant."""
    tenant_ids = [row[0] for row in db.query(AuditLog.tenant_id).distinct().all()]
    broken = []
    checked = 0
    for tenant_id in tenant_ids:
        prev_hash = GENESIS_HASH
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.id.asc())
            .all()
        )
        for row in rows:
            if row.entry_hash is None:
                continue  # legacy row predating the hash chain - not verifiable
            expected = compute_entry_hash(prev_hash, row)
            checked += 1
            if row.entry_hash != expected:
                broken.append({"tenant_id": tenant_id, "audit_log_id": row.id})
                break
            prev_hash = row.entry_hash
    return {"checked": checked, "broken": broken, "tenants": len(tenant_ids)}
