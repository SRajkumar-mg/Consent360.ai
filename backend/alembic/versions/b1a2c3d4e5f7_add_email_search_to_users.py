"""add email_search to users

Revision ID: b1a2c3d4e5f7
Revises: r2_new_tables
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "b1a2c3d4e5f7"
down_revision = "r2_new_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_search", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("users", "email_search")
