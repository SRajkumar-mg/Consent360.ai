"""R3-07 processor register + alerts; R1-10 retention schedule + actions

Extends the minimal `processors` table added by f8a9b0c1d2e3 (which existed
only to gate the chatbot's LLM provider) into the full R3-07 register:
contact details, contract clause references and dates, an outbound webhook
with its own secret, and a per-processor acknowledgement SLA. Adds
`processor_alerts` for the instructions themselves, and R1-10's
`retention_schedules` / `retention_actions`.

Every added column on `processors` carries a server_default, because the
migration must not fail on the row f8a9b0c1d2e3 already seeded.

Revision ID: a7c1e9d4b302
Revises: 265ec61f7106
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e9d4b302"
down_revision: Union[str, None] = "265ec61f7106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (column, type, nullable, server_default) for every column R3-07 adds to
# `processors`. A NOT NULL column needs a server_default or this migration
# fails against the row f8a9b0c1d2e3 already seeded.
_PROCESSOR_COLUMNS = [
    ("tenant_id", sa.Integer(), True, None),
    ("contact_name", sa.String(256), False, "''"),
    ("contact_email", sa.String(512), False, "''"),
    ("contact_phone", sa.String(256), False, "''"),
    ("escalation_email", sa.String(512), False, "''"),
    ("contract_signed_on", sa.Date(), True, None),
    ("contract_valid_from", sa.Date(), True, None),
    ("security_clause_ref", sa.String(128), False, "''"),
    ("security_measures", sa.Text(), False, "''"),
    ("erasure_clause_ref", sa.String(128), False, "''"),
    ("erasure_sla_days", sa.Integer(), True, None),
    ("webhook_url", sa.String(512), False, "''"),
    # Holds an AES-256-GCM ciphertext, not a plaintext secret - see the
    # Processor docstring in app/models/entities.py for why this one column
    # cannot be a hash like api_keys.key_hash.
    ("webhook_secret", sa.String(512), False, "''"),
    ("webhook_secret_fingerprint", sa.String(64), False, "''"),
    ("webhook_secret_set_at", sa.DateTime(timezone=True), True, None),
    ("ack_sla_hours", sa.Integer(), False, "24"),
    ("notes", sa.Text(), False, "''"),
]

# The retention schedule seeded here mirrors
# app/services/retention.py::RETENTION_CLASSES - each class's own floor, raised
# to 7 years for the four consent record classes the consent_manager_records
# overlay covers. Only `record_class` and `retention_days` are seeded: the
# statutory basis for each class lives in that code constant and is what
# GET /retention/schedule reports, so storing a copy here would create a second
# source of truth that can drift. `ensure_schedule_rows()` re-creates any row
# missing from this list idempotently at runtime, so the two cannot diverge
# into a missing class either.
_RETENTION_SEED = [
    ("consents", 2555),
    ("consent_history", 2555),
    ("consent_evidence", 2555),
    ("consent_receipts", 2555),
    ("audit_logs", 365),
    ("consent_decision_logs", 365),
    ("data_sharing_events", 365),
    ("processor_alerts", 365),
    ("notifications", 365),
    ("consent_manager_records", 2555),
    ("access_logs", 365),
    ("application_logs", 365),
]


def upgrade() -> None:
    for name, type_, nullable, default in _PROCESSOR_COLUMNS:
        op.add_column(
            "processors",
            sa.Column(name, type_, nullable=nullable,
                      server_default=sa.text(default) if default is not None else None),
        )
    op.add_column(
        "processors",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_foreign_key(
        "fk_processors_tenant_id", "processors", "organizations", ["tenant_id"], ["id"]
    )
    op.create_index("ix_processors_tenant_id", "processors", ["tenant_id"])

    op.create_table(
        "processor_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("alert_ref", sa.String(64), nullable=False),
        sa.Column("trigger_ref", sa.String(128), nullable=False),
        sa.Column("processor_id", sa.Integer(), sa.ForeignKey("processors.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("consent_id", sa.Integer(), sa.ForeignKey("consents.id"), nullable=True),
        sa.Column("purpose_id", sa.Integer(), sa.ForeignKey("purposes.id"), nullable=True),
        sa.Column("sharing_event_id", sa.Integer(), sa.ForeignKey("data_sharing_events.id"), nullable=True),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("payload_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("signed_timestamp", sa.Integer(), nullable=True),
        sa.Column("webhook_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("ack_sla_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("ack_reference", sa.String(128), nullable=False, server_default=""),
        sa.Column("ack_method", sa.String(16), nullable=False, server_default=""),
        sa.Column("ack_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_app", sa.String(128), nullable=False, server_default=""),
        sa.Column("actor_username", sa.String(64), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "alert_type IN ('CEASE_PROCESSING','ERASURE_INSTRUCTION','SHARING_EVENT')",
            name="ck_processor_alerts_alert_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SENT','ACKNOWLEDGED','FAILED','ESCALATED')",
            name="ck_processor_alerts_status",
        ),
        sa.CheckConstraint("ack_sla_hours > 0", name="ck_processor_alerts_sla_positive"),
    )
    op.create_index("ix_processor_alerts_alert_ref", "processor_alerts", ["alert_ref"], unique=True)
    op.create_index("ix_processor_alerts_trigger_ref", "processor_alerts", ["trigger_ref"])
    op.create_index("ix_processor_alerts_tenant_id", "processor_alerts", ["tenant_id"])
    op.create_index("ix_processor_alerts_processor_id", "processor_alerts", ["processor_id"])
    op.create_index("ix_processor_alerts_customer_id", "processor_alerts", ["customer_id"])
    op.create_index("ix_processor_alerts_alert_type", "processor_alerts", ["alert_type"])
    op.create_index("ix_processor_alerts_status", "processor_alerts", ["status"])
    op.create_index("ix_processor_alerts_next_attempt_at", "processor_alerts", ["next_attempt_at"])
    op.create_index("ix_processor_alerts_due_at", "processor_alerts", ["due_at"])
    op.create_index("ix_processor_alerts_created_at", "processor_alerts", ["created_at"])

    op.create_table(
        "retention_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("record_class", sa.String(64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("basis", sa.String(512), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.CheckConstraint("retention_days > 0", name="ck_retention_schedules_days_positive"),
    )
    op.create_index(
        "ix_retention_schedules_record_class", "retention_schedules", ["record_class"], unique=True
    )

    op.create_table(
        "retention_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("record_class", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("floor_days_at_execution", sa.Integer(), nullable=False),
        sa.Column("retention_days_at_execution", sa.Integer(), nullable=False),
        sa.Column("rows_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("actor_username", sa.String(64), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('DELETE','ANONYMISE','ARCHIVE')", name="ck_retention_actions_action"),
    )
    op.create_index("ix_retention_actions_tenant_id", "retention_actions", ["tenant_id"])
    op.create_index("ix_retention_actions_record_class", "retention_actions", ["record_class"])
    op.create_index("ix_retention_actions_executed_at", "retention_actions", ["executed_at"])

    for record_class, days in _RETENTION_SEED:
        op.execute(
            sa.text(
                "INSERT INTO retention_schedules (record_class, retention_days, is_active, "
                "basis, notes, updated_at, updated_by) "
                "SELECT :rc, :days, TRUE, '', '', NOW(), 'migration' "
                "WHERE NOT EXISTS (SELECT 1 FROM retention_schedules WHERE record_class = :rc)"
            ).bindparams(rc=record_class, days=days)
        )


def downgrade() -> None:
    op.drop_index("ix_retention_actions_executed_at", table_name="retention_actions")
    op.drop_index("ix_retention_actions_record_class", table_name="retention_actions")
    op.drop_index("ix_retention_actions_tenant_id", table_name="retention_actions")
    op.drop_table("retention_actions")

    op.drop_index("ix_retention_schedules_record_class", table_name="retention_schedules")
    op.drop_table("retention_schedules")

    for index in (
        "ix_processor_alerts_created_at",
        "ix_processor_alerts_due_at",
        "ix_processor_alerts_next_attempt_at",
        "ix_processor_alerts_status",
        "ix_processor_alerts_alert_type",
        "ix_processor_alerts_customer_id",
        "ix_processor_alerts_processor_id",
        "ix_processor_alerts_tenant_id",
        "ix_processor_alerts_trigger_ref",
        "ix_processor_alerts_alert_ref",
    ):
        op.drop_index(index, table_name="processor_alerts")
    op.drop_table("processor_alerts")

    op.drop_index("ix_processors_tenant_id", table_name="processors")
    op.drop_constraint("fk_processors_tenant_id", "processors", type_="foreignkey")
    op.drop_column("processors", "updated_at")
    for name, _type, _nullable, _default in reversed(_PROCESSOR_COLUMNS):
        op.drop_column("processors", name)
