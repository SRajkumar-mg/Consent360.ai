"""widen encrypted columns to match model

Revision ID: d3e4f5a6b7c8
Revises: c2b3d4e5f6a7
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2b3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("customers", "phone", type_=sa.String(256))
    op.alter_column("crm_customers", "aadhar_number", type_=sa.String(256))
    op.alter_column("crm_customers", "phone", type_=sa.String(256))


def downgrade() -> None:
    op.alter_column("crm_customers", "phone", type_=sa.String(32))
    op.alter_column("crm_customers", "aadhar_number", type_=sa.String(32))
    op.alter_column("customers", "phone", type_=sa.String(32))
