"""Add all missing search columns to users and customers tables.

Revision ID: c2b3d4e5f6a7
Revises: b1a2c3d4e5f7
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "c2b3d4e5f6a7"
down_revision = "b1a2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("external_id_search", sa.String(64), nullable=False, server_default=""))
    op.add_column("customers", sa.Column("email_search", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("customers", "email_search")
    op.drop_column("customers", "external_id_search")
