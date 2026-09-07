"""widen encrypted columns to model lengths and add missing _search columns

The initial schema (96c45ff69f72) sized customers/crm_customers/users columns
for plaintext (VARCHAR(32)-VARCHAR(255)). The models declare EncryptedString
at 256-512 chars because AES-256-GCM ciphertext, base64-encoded with its
nonce, is longer than the plaintext. With FIELD_ENCRYPTION_KEY set this
mismatch raises StringDataRightTruncation on insert. Separately,
scripts/add_search_columns.py added the `*_search` HMAC digest columns
out-of-band; this migration folds that into Alembic so `alembic check` does
not see the columns the models declare as missing from a fresh database.

Revision ID: e1a2b3c4d5e6
Revises: d2e3f4a5b6c7
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("customers", "external_id", type_=sa.String(256), existing_type=sa.String(64))
    op.alter_column("customers", "name", type_=sa.String(512), existing_type=sa.String(255))
    op.alter_column("customers", "email", type_=sa.String(512), existing_type=sa.String(255))
    op.alter_column("customers", "phone", type_=sa.String(256), existing_type=sa.String(32))

    op.alter_column("crm_customers", "name", type_=sa.String(512), existing_type=sa.String(255))
    op.alter_column("crm_customers", "email", type_=sa.String(512), existing_type=sa.String(255))
    op.alter_column("crm_customers", "aadhar_number", type_=sa.String(256), existing_type=sa.String(32))
    op.alter_column("crm_customers", "phone", type_=sa.String(256), existing_type=sa.String(32))

    op.alter_column("users", "email", type_=sa.String(512), existing_type=sa.String(255))

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    search_columns = {
        "customers": [("external_id_search", sa.String(64)), ("email_search", sa.String(64))],
        "crm_customers": [("email_search", sa.String(64))],
        "users": [("email_search", sa.String(64))],
    }
    for table, cols in search_columns.items():
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col_name, col_type in cols:
            if col_name not in existing:
                op.add_column(table, sa.Column(col_name, col_type, nullable=True))

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_external_id_search ON customers (external_id_search)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_email_search ON customers (email_search)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_crm_customers_email_search ON crm_customers (email_search)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_search ON users (email_search)")


def downgrade() -> None:
    op.drop_index("ix_users_email_search", table_name="users")
    op.drop_index("ix_crm_customers_email_search", table_name="crm_customers")
    op.drop_index("ix_customers_email_search", table_name="customers")
    op.drop_index("ix_customers_external_id_search", table_name="customers")

    op.drop_column("users", "email_search")
    op.drop_column("crm_customers", "email_search")
    op.drop_column("customers", "email_search")
    op.drop_column("customers", "external_id_search")

    op.alter_column("users", "email", type_=sa.String(255), existing_type=sa.String(512))
    op.alter_column("crm_customers", "phone", type_=sa.String(32), existing_type=sa.String(256))
    op.alter_column("crm_customers", "aadhar_number", type_=sa.String(32), existing_type=sa.String(256))
    op.alter_column("crm_customers", "email", type_=sa.String(255), existing_type=sa.String(512))
    op.alter_column("crm_customers", "name", type_=sa.String(255), existing_type=sa.String(512))
    op.alter_column("customers", "phone", type_=sa.String(32), existing_type=sa.String(256))
    op.alter_column("customers", "email", type_=sa.String(255), existing_type=sa.String(512))
    op.alter_column("customers", "name", type_=sa.String(255), existing_type=sa.String(512))
    op.alter_column("customers", "external_id", type_=sa.String(64), existing_type=sa.String(256))
