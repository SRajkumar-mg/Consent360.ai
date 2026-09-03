"""Audit ledger hash-chain utilities (R1-02).

Computes SHA-256 hash-chaining over audit_logs entries to make the ledger
tamper-evident. Each entry stores ``entry_hash`` (hash of its own canonical
business fields + the previous entry's hash) and ``prev_hash`` (the previous
entry's ``entry_hash``), forming a chain per tenant partition.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.entities import AuditLog


def canonical_fields(entry: AuditLog) -> dict:
    """Canonical serialization of a row's business fields (PII-free, stable)."""
    def iso(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    return {
        "event": entry.event,
        "actor_id": entry.actor_id or "",
        "actor_type": entry.actor_type or "USER",
        "source_app": entry.source_app or "",
        "customer_id": entry.customer_id,
        "consent_id": entry.consent_id,
        "purpose_id": entry.purpose_id,
        "purpose_code": entry.purpose_code,
        "policy_id": entry.policy_id,
        "policy_code": entry.policy_code,
        "old_status": entry.old_status,
        "new_status": entry.new_status,
        "consent_version": entry.consent_version,
        "policy_version": entry.policy_version,
        "decision": entry.decision,
        "reason": entry.reason if not _looks_encrypted(entry.reason) else "",
        "request_id": entry.request_id,
        "tenant_id": entry.tenant_id,
        "created_at": iso(entry.created_at),
    }


def _looks_encrypted(value: str) -> bool:
    # EncryptedText fields may be read back decrypted by the ORM; only treat
    # obviously-encrypted blobs as opaque to keep the canonical form PII-free.
    return isinstance(value, str) and "." in value and len(value) > 40


def compute_entry_hash(entry: AuditLog, prev_hash: str | None = None) -> str:
    payload = json.dumps(canonical_fields(entry), sort_keys=True, default=str)
    payload += f"|prev:{prev_hash or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_last_entry_hash(db: Session, tenant_id: int | None = None) -> str | None:
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if tenant_id is not None:
        q = q.filter(AuditLog.tenant_id == tenant_id)
    row = q.first()
    if row is None:
        return None
    return row.entry_hash or compute_entry_hash(row, None)


def chain_evidence(entry: AuditLog, prev_hash: str | None = None) -> None:
    """Stamp entry_hash and prev_hash onto an in-memory AuditLog."""
    entry.prev_hash = prev_hash
    entry.entry_hash = compute_entry_hash(entry, prev_hash)


def verify_chain(db: Session, tenant_id: int | None = None) -> dict:
    """Walk a tenant's (or all) audit chain and verify hash continuity.

    Returns {"verified": bool, "checked": int, "first_broken": row_id or None}.
    """
    q = db.query(AuditLog).order_by(AuditLog.id.asc())
    if tenant_id is not None:
        q = q.filter(AuditLog.tenant_id == tenant_id)
    rows = q.all()

    prev_hash = None
    for i, row in enumerate(rows):
        expected_prev = prev_hash
        if row.prev_hash is not None and row.prev_hash != expected_prev:
            return {"verified": False, "checked": i, "first_broken": row.id}
        recomputed = compute_entry_hash(row, row.prev_hash)
        if recomputed != row.entry_hash:
            return {"verified": False, "checked": i + 1, "first_broken": row.id}
        prev_hash = row.entry_hash
    return {"verified": True, "checked": len(rows), "first_broken": None}
