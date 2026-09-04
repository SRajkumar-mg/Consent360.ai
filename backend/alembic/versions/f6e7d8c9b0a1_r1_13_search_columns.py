"""R1-13 migrations hygiene - bring _search HMAC columns into the alembic chain.

The customers.external_id_search / customers.email_search / crm_customers.email_search
and users.email_search columns were previously added only via the unmanaged raw
script backend/scripts/add_search_columns.py, so a fresh build from migrations
alone would be missing them and ORM lookups (Customer.external_id_search == ...)
would fail. This migration adds them idempotently so the migration chain is the
single source of truth (R1-13, gap T-01/T-05).

Revision ID: f6e7d8c9b0a1
Revises: e1f2a3b4c5d6
Create Date: 2026-09-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6e7d8c9b0a1'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    # customers.external_id_search (unique, not null)
    if not _has_column("customers", "external_id_search"):
        op.add_column("customers", sa.Column("external_id_search", sa.String(length=64), nullable=True))
        op.create_index(op.f("ix_customers_external_id_search"), "customers", ["external_id_search"], unique=True)
    # customers.email_search (unique, not null)
    if not _has_column("customers", "email_search"):
        op.add_column("customers", sa.Column("email_search", sa.String(length=64), nullable=True))
        op.create_index(op.f("ix_customers_email_search"), "customers", ["email_search"], unique=True)
    # crm_customers.email_search
    if not _has_column("crm_customers", "email_search"):
        op.add_column("crm_customers", sa.Column("email_search", sa.String(length=64), nullable=True))
        op.create_index(op.f("ix_crm_customers_email_search"), "crm_customers", ["email_search"], unique=True)
    # users.email_search
    if not _has_column("users", "email_search"):
        op.add_column("users", sa.Column("email_search", sa.String(length=64), nullable=True))
        op.create_index(op.f("ix_users_email_search"), "users", ["email_search"], unique=True)


def downgrade() -> None:
    for table, column, ix in (
        ("customers", "external_id_search", "ix_customers_external_id_search"),
        ("customers", "email_search", "ix_customers_email_search"),
        ("crm_customers", "email_search", "ix_crm_customers_email_search"),
        ("users", "email_search", "ix_users_email_search"),
    ):
        if _has_column(table, column):
            op.drop_index(ix, table_name=table)
            op.drop_column(table, column)
