"""add consent_receipts, data_sharing_events and objections tables

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-04 03:00:00.000000

R1-08 (B-09, D-05, M-02, C-06): consent receipts issued on every grant/renew,
a disclosure log for sharing personal data with a processor, and an
objection register for s.7(a) voluntary-provision purposes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consent_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("receipt_ref", sa.String(64), nullable=False),
        sa.UniqueConstraint("receipt_ref"),
        sa.Column("consent_id", sa.Integer(), sa.ForeignKey("consents.id"), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("consent_evidence.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("source_app", sa.String(128), nullable=False, server_default=""),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notice_version_id", sa.Integer(), sa.ForeignKey("notice_versions.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_consent_receipts_tenant_id", "consent_receipts", ["tenant_id"])
    op.create_index("ix_consent_receipts_consent_id", "consent_receipts", ["consent_id"])
    op.create_index("ix_consent_receipts_evidence_id", "consent_receipts", ["evidence_id"])
    op.create_index("ix_consent_receipts_customer_id", "consent_receipts", ["customer_id"])
    op.create_index("ix_consent_receipts_issued_at", "consent_receipts", ["issued_at"])

    op.create_table(
        "data_sharing_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("processor_id", sa.Integer(), sa.ForeignKey("processors.id"), nullable=False),
        sa.Column("purpose_id", sa.Integer(), sa.ForeignKey("purposes.id"), nullable=False),
        sa.Column("consent_id", sa.Integer(), sa.ForeignKey("consents.id"), nullable=True),
        sa.Column("data_category_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("legal_basis", sa.String(64), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_username", sa.String(64), nullable=False, server_default="system"),
        sa.Column("source_app", sa.String(128), nullable=False, server_default=""),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_type IN ('REQUESTED','SENT','DENIED')", name="ck_data_sharing_events_event_type"),
    )
    op.create_index("ix_data_sharing_events_tenant_id", "data_sharing_events", ["tenant_id"])
    op.create_index("ix_data_sharing_events_customer_id", "data_sharing_events", ["customer_id"])
    op.create_index("ix_data_sharing_events_processor_id", "data_sharing_events", ["processor_id"])
    op.create_index("ix_data_sharing_events_purpose_id", "data_sharing_events", ["purpose_id"])
    op.create_index("ix_data_sharing_events_occurred_at", "data_sharing_events", ["occurred_at"])

    op.create_table(
        "objections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("purpose_id", sa.Integer(), sa.ForeignKey("purposes.id"), nullable=False),
        sa.Column("consent_id", sa.Integer(), sa.ForeignKey("consents.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("source_app", sa.String(128), nullable=False, server_default=""),
        sa.Column("actor_username", sa.String(64), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("objected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_objections_tenant_id", "objections", ["tenant_id"])
    op.create_index("ix_objections_customer_id", "objections", ["customer_id"])
    op.create_index("ix_objections_purpose_id", "objections", ["purpose_id"])
    op.create_index("ix_objections_objected_at", "objections", ["objected_at"])


def downgrade() -> None:
    op.drop_index("ix_objections_objected_at", table_name="objections")
    op.drop_index("ix_objections_purpose_id", table_name="objections")
    op.drop_index("ix_objections_customer_id", table_name="objections")
    op.drop_index("ix_objections_tenant_id", table_name="objections")
    op.drop_table("objections")

    op.drop_index("ix_data_sharing_events_occurred_at", table_name="data_sharing_events")
    op.drop_index("ix_data_sharing_events_purpose_id", table_name="data_sharing_events")
    op.drop_index("ix_data_sharing_events_processor_id", table_name="data_sharing_events")
    op.drop_index("ix_data_sharing_events_customer_id", table_name="data_sharing_events")
    op.drop_index("ix_data_sharing_events_tenant_id", table_name="data_sharing_events")
    op.drop_table("data_sharing_events")

    op.drop_index("ix_consent_receipts_issued_at", table_name="consent_receipts")
    op.drop_index("ix_consent_receipts_customer_id", table_name="consent_receipts")
    op.drop_index("ix_consent_receipts_evidence_id", table_name="consent_receipts")
    op.drop_index("ix_consent_receipts_consent_id", table_name="consent_receipts")
    op.drop_index("ix_consent_receipts_tenant_id", table_name="consent_receipts")
    op.drop_table("consent_receipts")
