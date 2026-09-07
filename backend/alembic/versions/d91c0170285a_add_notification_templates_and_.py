"""add notification_templates and notifications tables

Revision ID: d91c0170285a
Revises: c6d7e8f9a0b1
Create Date: 2026-09-04 08:10:00.000000

R3-06 (O-01, O-02, O-04, C-04): the interim notification service - templates
keyed by (tenant, event_type, channel, language) and one row per
notification attempt/delivery, with the retry/backoff and
delivered/acknowledged bookkeeping app/services/notifications.py needs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d91c0170285a"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("subject", sa.String(512), nullable=False, server_default=""),
        sa.Column("body_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.UniqueConstraint(
            "tenant_id", "event_type", "channel", "language",
            name="uq_notification_templates_identity",
        ),
        sa.CheckConstraint("channel IN ('EMAIL','SMS','IN_APP')", name="ck_notification_templates_channel"),
    )
    op.create_index("ix_notification_templates_tenant_id", "notification_templates", ["tenant_id"])
    op.create_index("ix_notification_templates_event_type", "notification_templates", ["event_type"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("notification_templates.id"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("recipient", sa.String(512), nullable=False, server_default=""),
        sa.Column("recipient_search", sa.String(64), nullable=True),
        sa.Column("subject", sa.String(512), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("provider_ref", sa.String(256), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("source_app", sa.String(128), nullable=False, server_default=""),
        sa.Column("actor_username", sa.String(64), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("channel IN ('EMAIL','SMS','IN_APP')", name="ck_notifications_channel"),
        sa.CheckConstraint(
            "status IN ('PENDING','SENT','DELIVERED','FAILED','ACKNOWLEDGED')",
            name="ck_notifications_status",
        ),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index("ix_notifications_customer_id", "notifications", ["customer_id"])
    op.create_index("ix_notifications_event_type", "notifications", ["event_type"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notifications_next_attempt_at", "notifications", ["next_attempt_at"])
    op.create_index("ix_notifications_recipient_search", "notifications", ["recipient_search"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_recipient_search", table_name="notifications")
    op.drop_index("ix_notifications_next_attempt_at", table_name="notifications")
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index("ix_notifications_event_type", table_name="notifications")
    op.drop_index("ix_notifications_customer_id", table_name="notifications")
    op.drop_index("ix_notifications_tenant_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_notification_templates_event_type", table_name="notification_templates")
    op.drop_index("ix_notification_templates_tenant_id", table_name="notification_templates")
    op.drop_table("notification_templates")
