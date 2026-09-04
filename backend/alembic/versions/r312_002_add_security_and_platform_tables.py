"""R3 security & platform tables: api keys, MFA, verification, notifications,
processors, breaches, consent artefacts, fiduciary onboarding, disclosures,
DPIA records, access logs, alert rules.

Revision ID: r312_002
Revises: r312_001
Create Date: 2026-09-03 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r312_002'
down_revision: Union[str, None] = 'r312_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------- R3-01: tenant-bound API keys
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('key_hash', sa.String(length=256), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False, server_default='default'),
        sa.Column('scopes', sa.String(length=512), nullable=False, server_default='integration.use,context.use'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash'),
    )
    op.create_index('ix_api_keys_tenant_id', 'api_keys', ['tenant_id'])
    op.create_index('ix_api_keys_key_hash', 'api_keys', ['key_hash'])
    op.create_foreign_key('fk_api_keys_tenant', 'api_keys', 'tenants', ['tenant_id'], ['id'])

    # ------------------------------------------------- R3-02: staff auth hardening
    op.create_table(
        'token_revocations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_token_revocations_user_id', 'token_revocations', ['user_id'])
    op.create_index('ix_token_revocations_jti', 'token_revocations', ['jti'])
    op.create_foreign_key('fk_token_revocations_user', 'token_revocations', 'users', ['user_id'], ['id'])

    op.create_table(
        'mfa_secrets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('secret', sa.String(length=256), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('enabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_foreign_key('fk_mfa_secrets_user', 'mfa_secrets', 'users', ['user_id'], ['id'])

    op.create_table(
        'mfa_recovery_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=256), nullable=False),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mfa_recovery_codes_user_id', 'mfa_recovery_codes', ['user_id'])
    op.create_foreign_key('fk_mfa_recovery_codes_user', 'mfa_recovery_codes', 'users', ['user_id'], ['id'])

    # ------------------------------------------------- R3-05: identity verification
    op.create_table(
        'verification_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('identifier', sa.String(length=256), nullable=False),
        sa.Column('token_hash', sa.String(length=256), nullable=False),
        sa.Column('verification_method', sa.String(length=32), nullable=False),
        sa.Column('is_consumed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_verification_tokens_identifier', 'verification_tokens', ['identifier'])

    # ------------------------------------------------- R3-06: notification service
    op.create_table(
        'notification_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=False, server_default='en'),
        sa.Column('subject', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notification_templates_tenant_id', 'notification_templates', ['tenant_id'])
    op.create_index('ix_notification_templates_event_type', 'notification_templates', ['event_type'])
    op.create_foreign_key('fk_notification_templates_tenant', 'notification_templates', 'tenants', ['tenant_id'], ['id'])

    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=False, server_default='en'),
        sa.Column('recipient', sa.String(length=512), nullable=False),
        sa.Column('reference_type', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('reference_id', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('subject', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('provider_ref', sa.String(length=256), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notifications_tenant_id', 'notifications', ['tenant_id'])
    op.create_index('ix_notifications_status', 'notifications', ['status'])
    op.create_foreign_key('fk_notifications_tenant', 'notifications', 'tenants', ['tenant_id'], ['id'])

    # ------------------------------------------------- R3-07: processor register
    op.create_table(
        'processors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('type', sa.String(length=64), nullable=False, server_default='PROCESSOR'),
        sa.Column('country', sa.String(length=64), nullable=False, server_default='IN'),
        sa.Column('contact', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('contract_ref', sa.String(length=256), nullable=True),
        sa.Column('contract_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('contract_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('security_clauses', sa.Text(), nullable=False, server_default=''),
        sa.Column('erasure_clause', sa.Text(), nullable=False, server_default=''),
        sa.Column('webhook_url', sa.String(length=512), nullable=False, server_default=''),
        sa.Column('webhook_secret', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_processors_tenant_id', 'processors', ['tenant_id'])
    op.create_foreign_key('fk_processors_tenant', 'processors', 'tenants', ['tenant_id'], ['id'])

    op.create_table(
        'processor_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('processor_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('alert_type', sa.String(length=32), nullable=False),
        sa.Column('reference_type', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('reference_id', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_processor_alerts_processor_id', 'processor_alerts', ['processor_id'])
    op.create_index('ix_processor_alerts_tenant_id', 'processor_alerts', ['tenant_id'])
    op.create_index('ix_processor_alerts_status', 'processor_alerts', ['status'])
    op.create_foreign_key('fk_processor_alerts_processor', 'processor_alerts', 'processors', ['processor_id'], ['id'])
    op.create_foreign_key('fk_processor_alerts_tenant', 'processor_alerts', 'tenants', ['tenant_id'], ['id'])

    # data_sharing_events (created in an earlier R1 migration) needs the
    # R3 propagation-tracking columns and its processor FK tightened now
    # that the processors table exists.
    op.add_column('data_sharing_events', sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'))
    op.add_column('data_sharing_events', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('data_sharing_events', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE data_sharing_events SET created_at = occurred_at WHERE created_at IS NULL")
    op.execute("UPDATE data_sharing_events SET updated_at = occurred_at WHERE updated_at IS NULL")
    op.alter_column('data_sharing_events', 'created_at', nullable=False)
    op.alter_column('data_sharing_events', 'updated_at', nullable=False)
    op.create_index('ix_data_sharing_events_processor_id', 'data_sharing_events', ['processor_id'])
    op.create_foreign_key('fk_data_sharing_processor', 'data_sharing_events', 'processors', ['processor_id'], ['id'])

    # ------------------------------------------------- R3-08: breach management
    op.create_table(
        'breaches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('reference_no', sa.String(length=64), nullable=False),
        sa.Column('breach_type', sa.String(length=32), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('aware_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('nature', sa.Text(), nullable=False, server_default=''),
        sa.Column('extent', sa.Text(), nullable=False, server_default=''),
        sa.Column('timing', sa.Text(), nullable=False, server_default=''),
        sa.Column('location', sa.Text(), nullable=False, server_default=''),
        sa.Column('likely_impact', sa.Text(), nullable=False, server_default=''),
        sa.Column('cause', sa.Text(), nullable=False, server_default=''),
        sa.Column('mitigation', sa.Text(), nullable=False, server_default=''),
        sa.Column('remedial_measures', sa.Text(), nullable=False, server_default=''),
        sa.Column('findings_on_actor', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='DETECTED'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('reference_no'),
    )
    op.create_index('ix_breaches_tenant_id', 'breaches', ['tenant_id'])
    op.create_index('ix_breaches_reference_no', 'breaches', ['reference_no'])
    op.create_index('ix_breaches_status', 'breaches', ['status'])
    op.create_foreign_key('fk_breaches_tenant', 'breaches', 'tenants', ['tenant_id'], ['id'])

    op.create_table(
        'breach_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('breach_id', sa.Integer(), nullable=False),
        sa.Column('recipient_type', sa.String(length=32), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False, server_default='EMAIL'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('content_hash', sa.String(length=256), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_breach_notifications_breach_id', 'breach_notifications', ['breach_id'])
    op.create_foreign_key('fk_breach_notifications_breach', 'breach_notifications', 'breaches', ['breach_id'], ['id'])

    op.create_table(
        'breach_extension_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('breach_id', sa.Integer(), nullable=False),
        sa.Column('clock_type', sa.String(length=32), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('new_deadline_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_breach_extension_requests_breach_id', 'breach_extension_requests', ['breach_id'])
    op.create_foreign_key('fk_breach_extension_requests_breach', 'breach_extension_requests', 'breaches', ['breach_id'], ['id'])

    op.create_table(
        'breach_affected_customers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('breach_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_breach_affected_customers_breach_id', 'breach_affected_customers', ['breach_id'])
    op.create_index('ix_breach_affected_customers_customer_id', 'breach_affected_customers', ['customer_id'])
    op.create_foreign_key('fk_breach_affected_customers_breach', 'breach_affected_customers', 'breaches', ['breach_id'], ['id'])
    op.create_foreign_key('fk_breach_affected_customers_customer', 'breach_affected_customers', 'customers', ['customer_id'], ['id'])

    # ------------------------------------------------- R3-10: consent validation / CM API
    op.create_table(
        'consent_artefacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('artefact_id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('purpose_code', sa.String(length=64), nullable=False),
        sa.Column('data_category_code', sa.String(length=64), nullable=False),
        sa.Column('processing_activity_code', sa.String(length=64), nullable=False),
        sa.Column('subject', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('purpose', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('method', sa.String(length=64), nullable=False, server_default='EXPLICIT'),
        sa.Column('expiry', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revocable', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('data_life', sa.String(length=64), nullable=False, server_default='SINGLE_USE'),
        sa.Column('frequency', sa.String(length=64), nullable=False, server_default='ONE_TIME'),
        sa.Column('access_mode', sa.String(length=32), nullable=False, server_default='PUSH'),
        sa.Column('notification_url', sa.String(length=512), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='ACTIVE'),
        sa.Column('payload_hash', sa.String(length=256), nullable=False),
        sa.Column('signature', sa.String(length=512), nullable=False),
        sa.Column('source_app', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('artefact_id'),
    )
    op.create_index('ix_consent_artefacts_artefact_id', 'consent_artefacts', ['artefact_id'])
    op.create_index('ix_consent_artefacts_customer_id', 'consent_artefacts', ['customer_id'])
    op.create_foreign_key('fk_consent_artefacts_customer', 'consent_artefacts', 'customers', ['customer_id'], ['id'])

    op.create_table(
        'fiduciary_onboarding',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fiduciary_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('contact', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('public_key', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='PENDING'),
        sa.Column('onboarded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fiduciary_id'),
    )
    op.create_index('ix_fiduciary_onboarding_fiduciary_id', 'fiduciary_onboarding', ['fiduciary_id'])

    op.create_table(
        'disclosures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('content', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_disclosures_tenant_id', 'disclosures', ['tenant_id'])
    op.create_foreign_key('fk_disclosures_tenant', 'disclosures', 'tenants', ['tenant_id'], ['id'])

    # ------------------------------------------------- R3-12: SDF readiness
    op.create_table(
        'dpia_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('scope', sa.Text(), nullable=False, server_default=''),
        sa.Column('findings_summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('next_review_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='DRAFT'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # ------------------------------------------------- R3-04: access logging extensions
    op.create_table(
        'access_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('actor_username', sa.String(length=64), nullable=False),
        sa.Column('actor_role', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('resource_type', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('resource_id', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_access_logs_event_type', 'access_logs', ['event_type'])
    op.create_index('ix_access_logs_actor_username', 'access_logs', ['actor_username'])
    op.create_index('ix_access_logs_request_id', 'access_logs', ['request_id'])
    op.create_index('ix_access_logs_created_at', 'access_logs', ['created_at'])

    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('threshold', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('window_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notify_contacts', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('alert_rules')
    op.drop_table('access_logs')
    op.drop_table('dpia_records')
    op.drop_table('disclosures')
    op.drop_table('fiduciary_onboarding')
    op.drop_table('consent_artefacts')
    op.drop_table('breach_affected_customers')
    op.drop_table('breach_extension_requests')
    op.drop_table('breach_notifications')
    op.drop_table('breaches')
    op.drop_constraint('fk_data_sharing_processor', 'data_sharing_events', type_='foreignkey')
    op.drop_index('ix_data_sharing_events_processor_id', table_name='data_sharing_events')
    op.drop_column('data_sharing_events', 'updated_at')
    op.drop_column('data_sharing_events', 'created_at')
    op.drop_column('data_sharing_events', 'status')
    op.drop_table('processor_alerts')
    op.drop_table('processors')
    op.drop_table('notifications')
    op.drop_table('notification_templates')
    op.drop_table('verification_tokens')
    op.drop_table('mfa_recovery_codes')
    op.drop_table('mfa_secrets')
    op.drop_table('token_revocations')
    op.drop_table('api_keys')
