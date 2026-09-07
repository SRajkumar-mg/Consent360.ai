"""harden the audit ledger: actor/ip/ua/tenant/hash-chain columns, append-only trigger

Revision ID: c5e6f7a8b9d0
Revises: b4d5e6f7a8c9
Create Date: 2026-09-03 00:20:00.000000

Unlike a from-scratch design, the dev database already holds pre-existing
audit_logs rows (every environment this has run in does, by the time this
migration lands) with no entry_hash/prev_hash yet. Rather than leave those
rows permanently unverifiable (entry_hash = NULL forever), this migration
backfills a valid hash chain over them, per tenant, in id order - using the
exact same canonical-payload/sha256 algorithm as
``app.core.audit_chain.compute_entry_hash`` so that a later
``verify_chain(db)`` call succeeds over the full history, not just rows
written after this migration. The backfill deliberately does not import
``app.core.audit_chain`` itself (a migration should not depend on
application code that might change independently of this file); the
canonical-payload construction is duplicated here instead, byte-for-byte.

The backfill runs BEFORE the append-only trigger is created below, since the
trigger would otherwise block the backfill's own UPDATEs.
"""
import hashlib
import json
from datetime import timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e6f7a8b9d0"
down_revision: Union[str, None] = "b4d5e6f7a8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GENESIS_HASH = "0" * 64


def _canonical(row: dict) -> str:
    """Mirrors app.core.audit_chain._canonical exactly, including
    normalising created_at to UTC before formatting it: TIMESTAMPTZ values
    round-trip through psycopg2 converted to the connection's session
    timezone (not necessarily UTC), and verify_chain() will later see this
    same row loaded fresh through the ORM - the hash must be stable
    regardless of which UTC-offset representation either side happens to
    get back for the same instant.
    """
    created_at = row["created_at"].astimezone(timezone.utc) if row["created_at"] else None
    payload = {
        "id": row["id"],
        "event": row["event"],
        "actor_username": row["actor_username"],
        "actor_type": row["actor_type"],
        "tenant_id": row["tenant_id"],
        "customer_id": row["customer_id"],
        "consent_id": row["consent_id"],
        "old_status": row["old_status"],
        "new_status": row["new_status"],
        "reason": row["reason"],
        "created_at": created_at.isoformat() if created_at else None,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _compute_entry_hash(prev_hash: str, row: dict) -> str:
    """Mirrors app.core.audit_chain.compute_entry_hash exactly."""
    return hashlib.sha256((prev_hash + _canonical(row)).encode("utf-8")).hexdigest()


def _backfill_hash_chain(bind) -> None:
    """Assign prev_hash/entry_hash to every pre-existing audit_logs row, per
    tenant, in id order. `reason` is EncryptedText: the raw column holds
    ciphertext, so it is decrypted here the same way
    EncryptedText.process_result_value does, to match the plaintext that
    verify_chain() will see later through the ORM. A fresh database has no
    rows at this point, so this is a no-op there.
    """
    from app.core.encryption import decrypt

    rows = bind.execute(
        sa.text(
            """
            SELECT id, event, actor_username, actor_type, tenant_id, customer_id,
                   consent_id, old_status, new_status, reason, created_at
            FROM audit_logs
            ORDER BY id ASC
            """
        )
    ).mappings().all()

    prev_hash_by_tenant: dict = {}
    for row in rows:
        row = dict(row)
        row["reason"] = decrypt(row["reason"])
        tenant_id = row["tenant_id"]
        prev_hash = prev_hash_by_tenant.get(tenant_id, GENESIS_HASH)
        entry_hash = _compute_entry_hash(prev_hash, row)
        bind.execute(
            sa.text("UPDATE audit_logs SET prev_hash = :p, entry_hash = :e WHERE id = :id"),
            {"p": prev_hash, "e": entry_hash, "id": row["id"]},
        )
        prev_hash_by_tenant[tenant_id] = entry_hash


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("audit_logs", sa.Column("actor_id", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("actor_type", sa.String(16), nullable=False, server_default="SYSTEM"))
    op.add_column("audit_logs", sa.Column("ip_address", sa.String(256), nullable=True))
    op.add_column("audit_logs", sa.Column("user_agent", sa.String(512), nullable=True))
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("entry_hash", sa.String(64), nullable=True))
    op.create_index("ix_audit_logs_entry_hash", "audit_logs", ["entry_hash"])

    op.add_column("customers", sa.Column("anonymised_ref", sa.String(64), nullable=True))

    # Backfill legacy rows into a valid, verifiable chain before the table
    # becomes append-only below.
    _backfill_hash_chain(bind)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_logs_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_block_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_block_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_block_delete ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_block_update ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_block_mutation()")
    op.drop_column("customers", "anonymised_ref")
    op.drop_index("ix_audit_logs_entry_hash", table_name="audit_logs")
    op.drop_column("audit_logs", "entry_hash")
    op.drop_column("audit_logs", "prev_hash")
    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "ip_address")
    op.drop_column("audit_logs", "actor_type")
    op.drop_column("audit_logs", "actor_id")
