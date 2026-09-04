"""R1 Consent360 implementation - tenants, audit hardening, evidence, scheduler,
   notices, lawful basis, receipts, retention/erasure, re-consent, retention floors.

Revision ID: e1f2a3b4c5d6
Revises: d2e3f4a5b6c7
Create Date: 2026-09-03 00:00:00.000000
"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Concrete timestamp for seeded rows (bulk_insert cannot bind _NOW).
_NOW = datetime.now().astimezone()


def upgrade() -> None:
    # ---------------------------------------------------------------- tenants
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('dpo_name', sa.String(length=256), nullable=False),
        sa.Column('dpo_contact', sa.Text(), nullable=False),
        sa.Column('withdraw_url', sa.String(length=512), nullable=False),
        sa.Column('rights_url', sa.String(length=512), nullable=False),
        sa.Column('grievance_url', sa.String(length=512), nullable=False),
        sa.Column('board_complaint_url', sa.String(length=512), nullable=False),
        sa.Column('grievance_response_days', sa.Integer(), nullable=False),
        sa.Column('default_language', sa.String(length=8), nullable=False),
        sa.Column('environment', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tenants_code'), 'tenants', ['code'], unique=True)

    # -------------------------------------------------- audit_logs hardening
    op.add_column('audit_logs', sa.Column('entry_hash', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('audit_logs', sa.Column('prev_hash', sa.String(length=64), nullable=True))
    op.add_column('audit_logs', sa.Column('actor_type', sa.String(length=32), nullable=False, server_default='USER'))
    op.add_column('audit_logs', sa.Column('actor_id', sa.String(length=128), nullable=False, server_default=''))
    op.add_column('audit_logs', sa.Column('ip_address', sa.String(length=256), nullable=False, server_default=''))
    op.add_column('audit_logs', sa.Column('user_agent', sa.Text(), nullable=False, server_default=''))
    op.add_column('audit_logs', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_audit_logs_tenant_id'), 'audit_logs', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_audit_logs_tenant_id', 'audit_logs', 'tenants', ['tenant_id'], ['id'])

    # ----------------------------------------------- consent_evidence extension
    op.add_column('consent_evidence', sa.Column('notice_version_id', sa.Integer(), nullable=True))
    op.add_column('consent_evidence', sa.Column('notice_hash', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('language', sa.String(length=8), nullable=False, server_default='en'))
    op.add_column('consent_evidence', sa.Column('ip_address', sa.String(length=256), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('user_agent', sa.Text(), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('session_id', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('ui_control_id', sa.String(length=128), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('banner_version', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('screen_id', sa.String(length=128), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('affirmative_action', sa.String(length=32), nullable=False, server_default='CLICK'))
    op.add_column('consent_evidence', sa.Column('content_hash', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('consent_evidence', sa.Column('signature', sa.Text(), nullable=True))
    op.add_column('consent_evidence', sa.Column('is_legacy', sa.Boolean(), nullable=False, server_default=sa.false()))

    # ----------------------------------------------- tenants FKs on core tables
    op.add_column('customers', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_customers_tenant_id'), 'customers', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_customers_tenant_id', 'customers', 'tenants', ['tenant_id'], ['id'])
    op.add_column('customers', sa.Column('last_interaction_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('purposes', sa.Column('lawful_basis', sa.String(length=32), nullable=False, server_default='CONSENT'))
    op.add_column('purposes', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_purposes_tenant_id'), 'purposes', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_purposes_tenant_id', 'purposes', 'tenants', ['tenant_id'], ['id'])

    op.add_column('purpose_versions', sa.Column('lawful_basis', sa.String(length=32), nullable=False, server_default='CONSENT'))
    op.add_column('purpose_versions', sa.Column('clause_reference', sa.Text(), nullable=False, server_default=''))
    op.add_column('purpose_versions', sa.Column('data_items', sa.JSON(), nullable=True))
    op.add_column('purpose_versions', sa.Column('services_enabled', sa.Text(), nullable=False, server_default=''))
    op.add_column('purpose_versions', sa.Column('child_restricted', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('purpose_versions', sa.Column('retention_policy_id', sa.Integer(), nullable=True))

    op.add_column('policies', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_policies_tenant_id'), 'policies', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_policies_tenant_id', 'policies', 'tenants', ['tenant_id'], ['id'])

    # ---------------------------------------------- notices (pre-consents, so
    # consents.notice_version_id can take a FK)
    op.create_table(
        'notices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('purpose_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notices_purpose_id'), 'notices', ['purpose_id'], unique=False)
    op.create_index(op.f('ix_notices_tenant_id'), 'notices', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_notices_tenant_id', 'notices', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_notices_purpose_id', 'notices', 'purposes', ['purpose_id'], ['id'])

    op.create_table(
        'notice_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('notice_id', sa.Integer(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('data_items', sa.JSON(), nullable=True),
        sa.Column('services_enabled', sa.Text(), nullable=False),
        sa.Column('retention_text', sa.Text(), nullable=False),
        sa.Column('withdraw_url', sa.String(length=512), nullable=False),
        sa.Column('rights_url', sa.String(length=512), nullable=False),
        sa.Column('board_complaint_url', sa.String(length=512), nullable=False),
        sa.Column('dpo_contact', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_by', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notice_versions_language'), 'notice_versions', ['language'], unique=False)
    op.create_index(op.f('ix_notice_versions_notice_id'), 'notice_versions', ['notice_id'], unique=False)
    op.create_index(op.f('ix_notice_versions_status'), 'notice_versions', ['status'], unique=False)
    op.create_foreign_key('fk_notice_versions_notice_id', 'notice_versions', 'notices', ['notice_id'], ['id'])

    op.add_column('consents', sa.Column('notice_version_id', sa.Integer(), nullable=True))
    op.add_column('consents', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.add_column('consents', sa.Column('re_consent_required', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('consents', sa.Column('re_consent_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_consents_tenant_id'), 'consents', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_consents_tenant_id', 'consents', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_consents_notice_version_id', 'consents', 'notice_versions', ['notice_version_id'], ['id'])
    op.create_foreign_key('fk_consent_evidence_notice_version_id', 'consent_evidence', 'notice_versions', ['notice_version_id'], ['id'])

    op.add_column('consent_contexts', sa.Column('token_hash', sa.String(length=64), nullable=False, server_default=''))
    op.create_index(op.f('ix_consent_contexts_token_hash'), 'consent_contexts', ['token_hash'], unique=False)

    op.add_column('consent_decision_logs', sa.Column('latency_ms', sa.Integer(), nullable=True))
    op.add_column('consent_decision_logs', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_consent_decision_logs_tenant_id'), 'consent_decision_logs', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_consent_decision_logs_tenant_id', 'consent_decision_logs', 'tenants', ['tenant_id'], ['id'])

    op.add_column('crm_customers', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_crm_customers_tenant_id'), 'crm_customers', ['tenant_id'], unique=False)
    op.create_foreign_key('fk_crm_customers_tenant_id', 'crm_customers', 'tenants', ['tenant_id'], ['id'])
    op.drop_column('crm_customers', 'aadhar_number')

    # ------------------------------------- widen encrypted columns for AES-GCM
    # EncryptedString adds a 44+ char base64 overhead; widen to model lengths
    # so ciphertext fits (plaintext columns predate encryption).
    for _col, _len in (("external_id", 256), ("name", 512), ("email", 512), ("phone", 256)):
        op.alter_column("customers", _col, type_=sa.String(_len))
    for _col, _len in (("name", 512), ("email", 512), ("phone", 256)):
        op.alter_column("crm_customers", _col, type_=sa.String(_len))

    # ------------------------------------------------------ consent receipts
    op.create_table(
        'consent_receipts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('consent_id', sa.Integer(), nullable=False),
        sa.Column('consent_evidence_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('purpose_id', sa.Integer(), nullable=False),
        sa.Column('notice_version_id', sa.Integer(), nullable=True),
        sa.Column('receipt_number', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('data_items_snapshot', sa.JSON(), nullable=True),
        sa.Column('method', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_consent_receipts_consent_id'), 'consent_receipts', ['consent_id'], unique=False)
    op.create_index(op.f('ix_consent_receipts_consent_evidence_id'), 'consent_receipts', ['consent_evidence_id'], unique=False)
    op.create_index(op.f('ix_consent_receipts_customer_id'), 'consent_receipts', ['customer_id'], unique=False)
    op.create_index(op.f('uq_consent_receipts_number'), 'consent_receipts', ['receipt_number'], unique=True)
    op.create_foreign_key('fk_consent_receipts_evidence', 'consent_receipts', 'consent_evidence', ['consent_evidence_id'], ['id'])
    op.create_foreign_key('fk_consent_receipts_consent', 'consent_receipts', 'consents', ['consent_id'], ['id'])
    op.create_foreign_key('fk_consent_receipts_tenant', 'consent_receipts', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_consent_receipts_customer', 'consent_receipts', 'customers', ['customer_id'], ['id'])
    op.create_foreign_key('fk_consent_receipts_purpose', 'consent_receipts', 'purposes', ['purpose_id'], ['id'])
    op.create_foreign_key('fk_consent_receipts_notice', 'consent_receipts', 'notice_versions', ['notice_version_id'], ['id'])

    # ----------------------------------------------------------- retention
    op.create_table(
        'retention_policies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('record_class', sa.String(length=64), nullable=False),
        sa.Column('scope', sa.String(length=64), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('inactivity_days', sa.Integer(), nullable=True),
        sa.Column('pre_erasure_notice_hours', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('legal_basis_for_retention', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_foreign_key('fk_retention_policies_tenant', 'retention_policies', 'tenants', ['tenant_id'], ['id'])

    op.create_table(
        'legal_holds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=64), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('placed_by', sa.String(length=64), nullable=False),
        sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'erasure_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=32), nullable=False),
        sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notice_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processors_notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processors_acked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('evidence_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_foreign_key('fk_erasure_jobs_tenant', 'erasure_jobs', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_erasure_jobs_customer', 'erasure_jobs', 'customers', ['customer_id'], ['id'])

    # -------------------------------------------------------- scheduler runs
    op.create_table(
        'scheduler_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_name', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('processed_count', sa.Integer(), nullable=False),
        sa.Column('error_count', sa.Integer(), nullable=False),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_scheduler_runs_job_name'), 'scheduler_runs', ['job_name'], unique=False)

    # ------------------------------------------------- data-sharing + objection
    op.create_table(
        'data_sharing_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('consent_id', sa.Integer(), nullable=True),
        sa.Column('processor_id', sa.Integer(), nullable=True),
        sa.Column('purpose_id', sa.Integer(), nullable=False),
        sa.Column('data_category_ids', sa.JSON(), nullable=True),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('hash', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_foreign_key('fk_data_sharing_tenant', 'data_sharing_events', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_data_sharing_customer', 'data_sharing_events', 'customers', ['customer_id'], ['id'])
    op.create_foreign_key('fk_data_sharing_consent', 'data_sharing_events', 'consents', ['consent_id'], ['id'])
    op.create_foreign_key('fk_data_sharing_purpose', 'data_sharing_events', 'purposes', ['purpose_id'], ['id'])

    op.create_table(
        'objections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('purpose_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('objected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_foreign_key('fk_objections_tenant', 'objections', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_objections_customer', 'objections', 'customers', ['customer_id'], ['id'])
    op.create_foreign_key('fk_objections_purpose', 'objections', 'purposes', ['purpose_id'], ['id'])

    # --------------------------------------------------- policy change log
    op.create_table(
        'policy_change_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('purpose_id', sa.Integer(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('published_by', sa.String(length=64), nullable=False),
        sa.Column('is_material', sa.Boolean(), nullable=False),
        sa.Column('is_cosmetic_change', sa.Boolean(), nullable=False),
        sa.Column('diff_summary', sa.JSON(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_foreign_key('fk_policy_change_logs_purpose', 'policy_change_logs', 'purposes', ['purpose_id'], ['id'])

    # ------------------------------------------------------ retention floors
    op.create_table(
        'retention_floors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('record_class', sa.String(length=64), nullable=False),
        sa.Column('minimum_days', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('record_class'),
    )

    # ------------------------------------------------------------- seed tenants
    op.bulk_insert(
        sa.table(
            'tenants',
            sa.column('id', sa.Integer),
            sa.column('code', sa.String),
            sa.column('name', sa.String),
            sa.column('dpo_name', sa.String),
            sa.column('dpo_contact', sa.Text),
            sa.column('withdraw_url', sa.String),
            sa.column('rights_url', sa.String),
            sa.column('grievance_url', sa.String),
            sa.column('board_complaint_url', sa.String),
            sa.column('grievance_response_days', sa.Integer),
            sa.column('default_language', sa.String),
            sa.column('environment', sa.String),
            sa.column('created_at', sa.DateTime),
            sa.column('updated_at', sa.DateTime),
        ),
        [
            {
                'id': 1, 'code': 'CRM_PORTAL', 'name': 'CRM Portal', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 2, 'code': 'CAREER_HUB', 'name': 'Career Hub', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 3, 'code': 'JOBHUB', 'name': 'JobHub', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 4, 'code': 'CODEX', 'name': 'Codex', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 5, 'code': 'SKILLLEARN', 'name': 'SkillLearn', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 6, 'code': 'SYSTEM', 'name': 'System', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 7, 'code': 'UI', 'name': 'Admin UI', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 8, 'code': 'EXTERNAL_APP', 'name': 'External App', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
            {
                'id': 9, 'code': 'PORTAL', 'name': 'Customer Portal', 'dpo_name': '',
                'dpo_contact': '', 'withdraw_url': '', 'rights_url': '', 'grievance_url': '',
                'board_complaint_url': '', 'grievance_response_days': 90, 'default_language': 'en',
                'environment': 'development', 'created_at': _NOW, 'updated_at': _NOW,
            },
        ],
    )

    # ------------------------------------------------------------- backfill tenant_id
    # customers
    op.execute("""
        UPDATE customers
        SET tenant_id = (SELECT t.id FROM tenants t WHERE t.code = customers.source_app)
        WHERE tenant_id IS NULL
    """)
    # purposes
    op.execute("""
        UPDATE purposes
        SET tenant_id = 1
        WHERE tenant_id IS NULL
    """)
    # consents
    op.execute("""
        UPDATE consents
        SET tenant_id = (SELECT t.id FROM tenants t WHERE t.code = consents.source_app)
        WHERE tenant_id IS NULL
    """)
    # policies
    op.execute("""
        UPDATE policies
        SET tenant_id = 1
        WHERE tenant_id IS NULL
    """)
    # audit_logs
    op.execute("""
        UPDATE audit_logs
        SET tenant_id = (SELECT t.id FROM tenants t WHERE t.code = audit_logs.source_app)
        WHERE tenant_id IS NULL
    """)
    # consent_decision_logs
    op.execute("""
        UPDATE consent_decision_logs
        SET tenant_id = (SELECT t.id FROM tenants t WHERE t.code = consent_decision_logs.source_app)
        WHERE tenant_id IS NULL
    """)

    # ------------------------------------------------------------- backfill lawful_basis
    op.execute("""
        UPDATE purposes SET lawful_basis = 'CONSENT' WHERE lawful_basis IS NULL OR lawful_basis = ''
    """)
    op.execute("""
        UPDATE purpose_versions SET lawful_basis = 'CONSENT' WHERE lawful_basis IS NULL OR lawful_basis = ''
    """)

    # -------------------------------------------- mark legacy evidence rows
    op.execute("UPDATE consent_evidence SET is_legacy = TRUE WHERE consent_evidence.is_legacy = FALSE AND consent_evidence.content_hash = '' OR consent_evidence.content_hash IS NULL")

    # --------------------------------------------- revocation of audit delete/update
    # NOTE: Actual DB access-control REVOKE is engine-specific and applied at deploy time
    # via the DB role; this migration records the intent and the application enforces it too.


def downgrade() -> None:
    op.drop_table('retention_floors')
    op.drop_table('policy_change_logs')
    op.drop_table('objections')
    op.drop_table('data_sharing_events')
    op.drop_table('scheduler_runs')
    op.drop_table('erasure_jobs')
    op.drop_table('legal_holds')
    op.drop_table('retention_policies')
    op.drop_table('consent_receipts')
    op.drop_table('notice_versions')
    op.drop_table('notices')

    op.drop_column('consent_receipts', 'consent_id')

    op.add_column('crm_customers', sa.Column('aadhar_number', sa.String(length=256), nullable=True))
    op.drop_column('crm_customers', 'tenant_id')

    op.drop_column('consent_decision_logs', 'tenant_id')
    op.drop_column('consent_decision_logs', 'latency_ms')

    op.drop_column('consent_contexts', 'token_hash')

    op.drop_column('consents', 're_consent_requested_at')
    op.drop_column('consents', 're_consent_required')
    op.drop_column('consents', 'tenant_id')
    op.drop_column('consents', 'notice_version_id')

    op.drop_column('policies', 'tenant_id')

    op.drop_column('purpose_versions', 'retention_policy_id')
    op.drop_column('purpose_versions', 'child_restricted')
    op.drop_column('purpose_versions', 'services_enabled')
    op.drop_column('purpose_versions', 'data_items')
    op.drop_column('purpose_versions', 'clause_reference')
    op.drop_column('purpose_versions', 'lawful_basis')

    op.drop_column('purposes', 'tenant_id')
    op.drop_column('purposes', 'lawful_basis')

    op.drop_column('customers', 'last_interaction_at')
    op.drop_column('customers', 'tenant_id')

    op.drop_column('consent_evidence', 'is_legacy')
    op.drop_column('consent_evidence', 'signature')
    op.drop_column('consent_evidence', 'content_hash')
    op.drop_column('consent_evidence', 'affirmative_action')
    op.drop_column('consent_evidence', 'screen_id')
    op.drop_column('consent_evidence', 'banner_version')
    op.drop_column('consent_evidence', 'ui_control_id')
    op.drop_column('consent_evidence', 'session_id')
    op.drop_column('consent_evidence', 'user_agent')
    op.drop_column('consent_evidence', 'ip_address')
    op.drop_column('consent_evidence', 'language')
    op.drop_column('consent_evidence', 'notice_hash')
    op.drop_column('consent_evidence', 'notice_version_id')

    op.drop_column('audit_logs', 'tenant_id')
    op.drop_column('audit_logs', 'user_agent')
    op.drop_column('audit_logs', 'ip_address')
    op.drop_column('audit_logs', 'actor_id')
    op.drop_column('audit_logs', 'actor_type')
    op.drop_column('audit_logs', 'prev_hash')
    op.drop_column('audit_logs', 'entry_hash')

    op.drop_table('tenants')
