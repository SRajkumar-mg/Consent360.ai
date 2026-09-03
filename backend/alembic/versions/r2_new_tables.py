"""add tenant_settings, notices, rights_requests, grievances, banner_events, kpi_snapshots tables

Revision ID: r2_new_tables
Revises: d2e3f4a5b6c7
Create Date: 2026-09-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'r2_new_tables'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_settings (
            id SERIAL PRIMARY KEY,
            tenant_code VARCHAR(64) NOT NULL UNIQUE,
            dpo_name VARCHAR(128) DEFAULT '',
            dpo_contact VARCHAR(256) DEFAULT '',
            withdraw_url VARCHAR(512) DEFAULT '',
            rights_url VARCHAR(512) DEFAULT '',
            grievance_url VARCHAR(512) DEFAULT '',
            board_complaint_url VARCHAR(512) DEFAULT '',
            grievance_response_days INTEGER DEFAULT 30,
            default_language VARCHAR(10) DEFAULT 'en',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tenant_settings_tenant_code ON tenant_settings(tenant_code)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS notices (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER REFERENCES tenant_settings(id),
            purpose_id INTEGER REFERENCES purposes(id),
            title TEXT DEFAULT '',
            body TEXT DEFAULT '',
            language VARCHAR(10) DEFAULT 'en',
            status VARCHAR(32) DEFAULT 'DRAFT',
            version INTEGER DEFAULT 1,
            data_items JSONB DEFAULT '[]'::jsonb,
            services_enabled JSONB DEFAULT '[]'::jsonb,
            retention_text VARCHAR(512) DEFAULT '',
            checklist_passed BOOLEAN DEFAULT FALSE,
            checklist_reviewer VARCHAR(64) DEFAULT '',
            checklist_at TIMESTAMP WITH TIME ZONE,
            published_at TIMESTAMP WITH TIME ZONE,
            created_by VARCHAR(64) DEFAULT 'system',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS rights_requests (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER DEFAULT 1,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            type VARCHAR(32) NOT NULL,
            status VARCHAR(32) DEFAULT 'RECEIVED',
            received_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            acknowledged_at TIMESTAMP WITH TIME ZONE,
            due_at TIMESTAMP WITH TIME ZONE,
            closed_at TIMESTAMP WITH TIME ZONE,
            identity_verified_at TIMESTAMP WITH TIME ZONE,
            assignee_user_id INTEGER,
            resolution TEXT DEFAULT '',
            evidence_ref VARCHAR(128) DEFAULT '',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rights_requests_customer_id ON rights_requests(customer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rights_requests_status ON rights_requests(status)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS grievances (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER DEFAULT 1,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            reference_no VARCHAR(32) NOT NULL UNIQUE,
            category VARCHAR(64) DEFAULT 'general',
            description TEXT DEFAULT '',
            status VARCHAR(32) DEFAULT 'RECEIVED',
            received_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            acknowledged_at TIMESTAMP WITH TIME ZONE,
            due_at TIMESTAMP WITH TIME ZONE,
            escalated_at TIMESTAMP WITH TIME ZONE,
            resolved_at TIMESTAMP WITH TIME ZONE,
            resolution_summary TEXT DEFAULT '',
            feedback TEXT DEFAULT '',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_grievances_customer_id ON grievances(customer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_grievances_reference_no ON grievances(reference_no)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_grievances_status ON grievances(status)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS banner_events (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER DEFAULT 1,
            customer_id INTEGER,
            session_id VARCHAR(64) DEFAULT '',
            purpose_id INTEGER,
            event_type VARCHAR(32) NOT NULL,
            language VARCHAR(10) DEFAULT 'en',
            banner_version VARCHAR(32) DEFAULT '1.0',
            control_id VARCHAR(64) DEFAULT '',
            notice_version VARCHAR(64) DEFAULT '',
            occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS kpi_snapshots (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER DEFAULT 1,
            kpi_id VARCHAR(32) NOT NULL,
            kpi_name VARCHAR(128) NOT NULL,
            period VARCHAR(32) NOT NULL,
            period_start TIMESTAMP WITH TIME ZONE NOT NULL,
            period_end TIMESTAMP WITH TIME ZONE NOT NULL,
            value INTEGER DEFAULT 0,
            details JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kpi_snapshots")
    op.execute("DROP TABLE IF EXISTS banner_events")
    op.execute("DROP TABLE IF EXISTS grievances")
    op.execute("DROP TABLE IF EXISTS rights_requests")
    op.execute("DROP TABLE IF EXISTS notices")
    op.execute("DROP TABLE IF EXISTS tenant_settings")
