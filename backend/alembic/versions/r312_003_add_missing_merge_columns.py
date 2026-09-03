"""Add columns that existed on SQLAlchemy models after the R1/R2/R3 merge
but were never added to any migration (found via a live-DB vs. model diff):
users auth-hardening fields, tenants domain/is_active, customers identity
verification fields, crm_customers aadhar_number.

Revision ID: r312_003
Revises: r312_002
Create Date: 2026-09-03 21:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r312_003'
down_revision: Union[str, None] = 'r312_002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # R3-02: staff auth hardening fields on users
    op.add_column('users', sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('token_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('users', sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))

    # R1-01: tenant model fields that predate the tenants migration's column set
    op.add_column('tenants', sa.Column('domain', sa.String(length=256), nullable=False, server_default=''))
    op.add_column('tenants', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))

    # R3-05: principal identity verification fields on customers
    op.add_column('customers', sa.Column('identity_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('customers', sa.Column('verification_method', sa.String(length=32), nullable=True))

    # crm_customers gains an encrypted aadhar_number field
    op.add_column('crm_customers', sa.Column('aadhar_number', sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column('crm_customers', 'aadhar_number')
    op.drop_column('customers', 'verification_method')
    op.drop_column('customers', 'identity_verified_at')
    op.drop_column('tenants', 'is_active')
    op.drop_column('tenants', 'domain')
    op.drop_column('users', 'mfa_enabled')
    op.drop_column('users', 'token_version')
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_login_count')
