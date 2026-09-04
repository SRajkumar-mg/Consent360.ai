"""add organizations and organization_users tables

Adds multi-tenancy support: organizations table plus organization_users
for org-scoped portal users (org_admin / org_viewer roles).

Revision ID: d2e3f4a5b6c7
Revises: cee9a14a2b1d
Create Date: 2026-08-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'cee9a14a2b1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id SERIAL PRIMARY KEY,
            name VARCHAR(256) NOT NULL,
            code VARCHAR(64) NOT NULL UNIQUE,
            domain VARCHAR(256) DEFAULT '',
            description TEXT DEFAULT '',
            logo_url VARCHAR(512) DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_organizations_code ON organizations(code)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS organization_users (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            username VARCHAR(64) NOT NULL UNIQUE,
            full_name VARCHAR(128) NOT NULL,
            email VARCHAR(512) DEFAULT '',
            email_search VARCHAR(128) DEFAULT '',
            password_hash VARCHAR(256) NOT NULL,
            role VARCHAR(32) DEFAULT 'org_viewer',
            is_active BOOLEAN DEFAULT TRUE,
            last_login_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_organization_users_organization_id ON organization_users(organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_organization_users_username ON organization_users(username)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS organization_users")
    op.execute("DROP TABLE IF EXISTS organizations")
