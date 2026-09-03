"""add email_search to crm_customers

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_customers", sa.Column("email_search", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("crm_customers", "email_search")
