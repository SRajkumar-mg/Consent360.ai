"""add otp_challenges and identity-verification columns

Revision ID: e7a8b9c0d1f2
Revises: d6f7a8b9c0e1
Create Date: 2026-09-03 00:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a8b9c0d1f2"
down_revision: Union[str, None] = "d6f7a8b9c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "otp_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_id", sa.Integer(), sa.ForeignKey("consent_contexts.id"), nullable=False),
        sa.Column("email_search", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_otp_challenges_context_id", "otp_challenges", ["context_id"])
    op.create_index("ix_otp_challenges_email_search", "otp_challenges", ["email_search"])

    op.add_column("consent_contexts", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("consent_contexts", sa.Column("verification_method", sa.String(32), nullable=True))
    op.add_column("customers", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "last_verified_at")
    op.drop_column("consent_contexts", "verification_method")
    op.drop_column("consent_contexts", "verified_at")
    op.drop_index("ix_otp_challenges_email_search", table_name="otp_challenges")
    op.drop_index("ix_otp_challenges_context_id", table_name="otp_challenges")
    op.drop_table("otp_challenges")
