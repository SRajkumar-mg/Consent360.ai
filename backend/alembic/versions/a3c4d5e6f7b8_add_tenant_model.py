"""extend organizations into the tenant entity; add tenant_id everywhere

Revision ID: a3c4d5e6f7b8
Revises: f2b3c4d5e6a7
Create Date: 2026-09-03 00:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3c4d5e6f7b8"
down_revision: Union[str, None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE_APP_TABLES = ("customers", "consents", "audit_logs", "consent_evidence")
_TENANT_ID_TABLES = ("customers", "consents", "purposes", "policies", "audit_logs",
                     "consent_evidence", "scheduler_runs", "kpi_snapshots")


def upgrade() -> None:
    op.add_column("organizations", sa.Column("dpo_name", sa.String(256), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("dpo_email", sa.String(512), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("dpo_phone", sa.String(256), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("withdraw_url", sa.String(512), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("rights_url", sa.String(512), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("grievance_url", sa.String(512), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("board_complaint_url", sa.String(512), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("grievance_response_days", sa.Integer(), nullable=False, server_default="90"))
    op.add_column("organizations", sa.Column("default_language", sa.String(8), nullable=False, server_default="en"))
    op.add_column("organizations", sa.Column("environment", sa.String(16), nullable=False, server_default="DEV"))
    op.add_column("organizations", sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"))
    op.create_check_constraint(
        "ck_organizations_grievance_response_days", "organizations", "grievance_response_days <= 90"
    )

    for table in _TENANT_ID_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    # Ensure a PLATFORM tenant exists to absorb placeholder source_app values.
    op.execute(
        """
        INSERT INTO organizations
            (name, code, domain, description, logo_url, is_active, created_at, updated_at,
             dpo_name, dpo_email, dpo_phone, withdraw_url, rights_url, grievance_url,
             board_complaint_url, grievance_response_days, default_language, environment, settings)
        SELECT 'Platform', 'PLATFORM', '', 'Internal placeholder tenant for system-originated records', '',
               TRUE, NOW(), NOW(), '', '', '', '', '', '', '', 90, 'en', 'DEV', '{}'::json
        WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE code = 'PLATFORM')
        """
    )

    # Create an Organization for every distinct non-placeholder source_app seen
    # anywhere, that is not already a known org code.
    union_parts = " UNION ".join(
        f"SELECT source_app FROM {t} WHERE source_app IS NOT NULL AND source_app NOT IN ('', 'SYSTEM', 'UI')"
        for t in _SOURCE_APP_TABLES
    )
    op.execute(
        f"""
        INSERT INTO organizations
            (name, code, domain, description, logo_url, is_active, created_at, updated_at,
             dpo_name, dpo_email, dpo_phone, withdraw_url, rights_url, grievance_url,
             board_complaint_url, grievance_response_days, default_language, environment, settings)
        SELECT s.source_app, s.source_app, '', 'Auto-created from legacy source_app during tenant migration',
               '', TRUE, NOW(), NOW(), '', '', '', '', '', '', '', 90, 'en', 'DEV', '{{}}'::json
        FROM ({union_parts}) s
        WHERE NOT EXISTS (SELECT 1 FROM organizations o WHERE o.code = s.source_app)
        """
    )

    # Backfill tenant_id from source_app on the four tables that carry it.
    for table in _SOURCE_APP_TABLES:
        op.execute(
            f"""
            UPDATE {table} t SET tenant_id = o.id
            FROM organizations o
            WHERE o.code = CASE WHEN t.source_app IN ('', 'SYSTEM', 'UI') OR t.source_app IS NULL
                                 THEN 'PLATFORM' ELSE t.source_app END
            """
        )

    # Purposes and policies are global (no source_app column) -> PLATFORM tenant.
    op.execute("UPDATE purposes SET tenant_id = (SELECT id FROM organizations WHERE code = 'PLATFORM')")
    op.execute("UPDATE policies SET tenant_id = (SELECT id FROM organizations WHERE code = 'PLATFORM')")
    op.execute("UPDATE scheduler_runs SET tenant_id = (SELECT id FROM organizations WHERE code = 'PLATFORM')")
    op.execute("UPDATE kpi_snapshots SET tenant_id = (SELECT id FROM organizations WHERE code = 'PLATFORM')")


def downgrade() -> None:
    # Deliberately does NOT delete the organization rows this migration
    # created (the PLATFORM tenant, and any tenant auto-created from a
    # previously-unseen source_app): by the time anyone downgrades, other
    # tables' `tenant_id` values already reference them, other code may have
    # started keying off `Organization.code`, and a real organization row
    # could coincidentally share a code with one of these. Deleting them here
    # risks orphaning or corrupting data that has nothing to do with this
    # migration; dropping only the columns/constraint this migration added is
    # the safe, reversible half of the change. This is intentional, not an
    # oversight.
    for table in _TENANT_ID_TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
    op.drop_constraint("ck_organizations_grievance_response_days", "organizations", type_="check")
    for col in (
        "settings", "environment", "default_language", "grievance_response_days",
        "board_complaint_url", "grievance_url", "rights_url", "withdraw_url",
        "dpo_phone", "dpo_email", "dpo_name",
    ):
        op.drop_column("organizations", col)
