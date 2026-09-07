"""align schema with models so alembic check reports no drift

Closes out the schema drift that predates the S2 phase-0 migration-hygiene
task (e1a2b3c4d5e6) and was left for a follow-up: it was never introduced by
that migration, but it kept `alembic check` red, which disables the pytest
gate every later S2 task relies on. Three groups of changes:

1. ``consent_decision_logs.details`` was created as plain ``JSON`` in the
   initial schema (96c45ff69f72); the model declares ``EncryptedJSON``, whose
   ``impl`` is ``Text`` (see app/core/encryption.py). Cast the column to
   ``TEXT``. Existing rows keep their plaintext JSON text representation and
   stay readable: ``EncryptedJSON.process_result_value``/``decrypt()`` fall
   back to returning the raw value when it is not recognised as ciphertext
   (no "." separator), so no data is lost. Encrypting those historical rows
   going forward is a separate, deliberate step via
   ``python -m scripts.encrypt_existing_data`` (not run here).

2. A handful of columns on ``organizations``, ``organization_users`` and
   ``crm_customers`` were created without ``NOT NULL`` even though their
   models declare non-Optional fields (the ``d2e3f4a5b6c7`` migration used
   raw ``CREATE TABLE`` SQL and only marked required-at-creation columns
   ``NOT NULL``, not columns with a Python/SQL default). Same story for the
   three ``*_search`` columns ``e1a2b3c4d5e6`` deliberately left nullable.
   Each column is backfilled before the constraint is tightened so this
   applies cleanly to a database that already has rows; on a fresh database
   every backfill statement matches zero rows.

3. ``organizations.code`` and ``organization_users.username`` were declared
   ``UNIQUE`` inline in that same raw-SQL ``CREATE TABLE``, which Postgres
   satisfies with a separate unique *constraint* plus the migration's own
   plain (non-unique) ``CREATE INDEX``. The models declare
   ``unique=True, index=True``, which SQLAlchemy represents as a single
   unique *index* — so autogenerate keeps proposing to remove the constraint
   and the plain index and add a unique index instead. Do exactly that.

Revision ID: e1a2b3c4d5e7
Revises: e1a2b3c4d5e6
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5e7"
down_revision: Union[str, None] = "e1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_search_column(bind, table: str, source_col: str, search_col: str) -> None:
    """Fill a NULL *_search digest from its encrypted source column.

    Mirrors scripts/add_search_columns.py: decrypt the source column's raw
    value in Python (it may be ciphertext or legacy plaintext — decrypt()
    handles both) and store the deterministic HMAC digest. Rows whose source
    column is itself NULL are left alone (nothing to derive a digest from).
    A fresh database has no rows, so this is a no-op there.
    """
    from app.core.encryption import decrypt, hmac_digest

    rows = bind.execute(
        sa.text(
            f"SELECT id, {source_col} FROM {table} "
            f"WHERE {search_col} IS NULL AND {source_col} IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        pk, raw = row[0], row[1]
        digest = hmac_digest(decrypt(raw))
        bind.execute(
            sa.text(f"UPDATE {table} SET {search_col} = :digest WHERE id = :pk"),
            {"digest": digest, "pk": pk},
        )


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. consent_decision_logs.details: JSON -> TEXT (EncryptedJSON.impl) ---
    op.alter_column(
        "consent_decision_logs",
        "details",
        type_=sa.Text(),
        existing_type=sa.JSON(),
        existing_nullable=False,
        postgresql_using="details::text",
    )

    # --- 2. backfill + tighten to NOT NULL ---
    _backfill_search_column(bind, "crm_customers", "email", "email_search")
    _backfill_search_column(bind, "customers", "external_id", "external_id_search")
    _backfill_search_column(bind, "users", "email", "email_search")

    op.execute("UPDATE crm_customers SET consent_preferences = '{}'::json WHERE consent_preferences IS NULL")
    op.execute("UPDATE organization_users SET email = '' WHERE email IS NULL")
    op.execute("UPDATE organization_users SET email_search = '' WHERE email_search IS NULL")
    op.execute("UPDATE organization_users SET role = 'org_viewer' WHERE role IS NULL")
    op.execute("UPDATE organization_users SET is_active = TRUE WHERE is_active IS NULL")
    op.execute("UPDATE organization_users SET created_at = NOW() WHERE created_at IS NULL")
    op.execute("UPDATE organizations SET domain = '' WHERE domain IS NULL")
    op.execute("UPDATE organizations SET description = '' WHERE description IS NULL")
    op.execute("UPDATE organizations SET logo_url = '' WHERE logo_url IS NULL")
    op.execute("UPDATE organizations SET is_active = TRUE WHERE is_active IS NULL")
    op.execute("UPDATE organizations SET created_at = NOW() WHERE created_at IS NULL")
    op.execute("UPDATE organizations SET updated_at = NOW() WHERE updated_at IS NULL")

    op.alter_column("crm_customers", "email_search", nullable=False, existing_type=sa.String(64))
    op.alter_column("crm_customers", "consent_preferences", nullable=False, existing_type=sa.JSON())
    op.alter_column("customers", "external_id_search", nullable=False, existing_type=sa.String(64))
    op.alter_column("users", "email_search", nullable=False, existing_type=sa.String(64))
    op.alter_column("organization_users", "email", nullable=False, existing_type=sa.String(512))
    op.alter_column("organization_users", "email_search", nullable=False, existing_type=sa.String(128))
    op.alter_column("organization_users", "role", nullable=False, existing_type=sa.String(32))
    op.alter_column("organization_users", "is_active", nullable=False, existing_type=sa.Boolean())
    op.alter_column("organization_users", "created_at", nullable=False, existing_type=sa.DateTime(timezone=True))
    op.alter_column("organizations", "domain", nullable=False, existing_type=sa.String(256))
    op.alter_column("organizations", "description", nullable=False, existing_type=sa.Text())
    op.alter_column("organizations", "logo_url", nullable=False, existing_type=sa.String(512))
    op.alter_column("organizations", "is_active", nullable=False, existing_type=sa.Boolean())
    op.alter_column("organizations", "created_at", nullable=False, existing_type=sa.DateTime(timezone=True))
    op.alter_column("organizations", "updated_at", nullable=False, existing_type=sa.DateTime(timezone=True))

    # --- 3. code/username: constraint+plain-index -> single unique index ---
    op.execute("ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_code_key")
    op.execute("DROP INDEX IF EXISTS ix_organizations_code")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_organizations_code ON organizations (code)")

    op.execute("ALTER TABLE organization_users DROP CONSTRAINT IF EXISTS organization_users_username_key")
    op.execute("DROP INDEX IF EXISTS ix_organization_users_username")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_organization_users_username ON organization_users (username)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_organization_users_username")
    op.execute("CREATE INDEX IF NOT EXISTS ix_organization_users_username ON organization_users (username)")
    op.execute(
        "ALTER TABLE organization_users ADD CONSTRAINT organization_users_username_key UNIQUE (username)"
    )

    op.execute("DROP INDEX IF EXISTS ix_organizations_code")
    op.execute("CREATE INDEX IF NOT EXISTS ix_organizations_code ON organizations (code)")
    op.execute("ALTER TABLE organizations ADD CONSTRAINT organizations_code_key UNIQUE (code)")

    op.alter_column("organizations", "updated_at", nullable=True, existing_type=sa.DateTime(timezone=True))
    op.alter_column("organizations", "created_at", nullable=True, existing_type=sa.DateTime(timezone=True))
    op.alter_column("organizations", "is_active", nullable=True, existing_type=sa.Boolean())
    op.alter_column("organizations", "logo_url", nullable=True, existing_type=sa.String(512))
    op.alter_column("organizations", "description", nullable=True, existing_type=sa.Text())
    op.alter_column("organizations", "domain", nullable=True, existing_type=sa.String(256))
    op.alter_column("organization_users", "created_at", nullable=True, existing_type=sa.DateTime(timezone=True))
    op.alter_column("organization_users", "is_active", nullable=True, existing_type=sa.Boolean())
    op.alter_column("organization_users", "role", nullable=True, existing_type=sa.String(32))
    op.alter_column("organization_users", "email_search", nullable=True, existing_type=sa.String(128))
    op.alter_column("organization_users", "email", nullable=True, existing_type=sa.String(512))
    op.alter_column("users", "email_search", nullable=True, existing_type=sa.String(64))
    op.alter_column("customers", "external_id_search", nullable=True, existing_type=sa.String(64))
    op.alter_column("crm_customers", "consent_preferences", nullable=True, existing_type=sa.JSON())
    op.alter_column("crm_customers", "email_search", nullable=True, existing_type=sa.String(64))

    op.alter_column(
        "consent_decision_logs",
        "details",
        type_=sa.JSON(),
        existing_type=sa.Text(),
        existing_nullable=False,
        postgresql_using="details::json",
    )
