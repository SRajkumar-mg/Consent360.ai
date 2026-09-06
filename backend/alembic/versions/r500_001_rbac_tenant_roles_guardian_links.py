"""R5-001: RBAC extension - tenant-aware role assignments + guardian-child links

Purely additive: two new tables, no changes to existing tables/data.

Revision ID: r500_001
Revises: r400_001
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "r500_001"
down_revision = "r400_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_tenant_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "tenant_id", "role_id", name="uq_user_tenant_role"),
    )
    op.create_index("ix_user_tenant_roles_user_id", "user_tenant_roles", ["user_id"])
    op.create_index("ix_user_tenant_roles_tenant_id", "user_tenant_roles", ["tenant_id"])
    op.create_index("ix_user_tenant_roles_role_id", "user_tenant_roles", ["role_id"])

    op.create_table(
        "guardian_child_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guardian_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("child_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("verification_method", sa.String(64), server_default=""),
        sa.Column("status", sa.String(32), server_default="PENDING"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("guardian_user_id", "child_customer_id", name="uq_guardian_child"),
    )
    op.create_index("ix_guardian_child_links_guardian_user_id", "guardian_child_links", ["guardian_user_id"])
    op.create_index("ix_guardian_child_links_child_customer_id", "guardian_child_links", ["child_customer_id"])


def downgrade() -> None:
    op.drop_index("ix_guardian_child_links_child_customer_id", table_name="guardian_child_links")
    op.drop_index("ix_guardian_child_links_guardian_user_id", table_name="guardian_child_links")
    op.drop_table("guardian_child_links")

    op.drop_index("ix_user_tenant_roles_role_id", table_name="user_tenant_roles")
    op.drop_index("ix_user_tenant_roles_tenant_id", table_name="user_tenant_roles")
    op.drop_index("ix_user_tenant_roles_user_id", table_name="user_tenant_roles")
    op.drop_table("user_tenant_roles")
