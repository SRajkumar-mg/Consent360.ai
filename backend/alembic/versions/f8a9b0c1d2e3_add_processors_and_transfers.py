"""add processors and transfers tables; seed the LLM provider

Revision ID: f8a9b0c1d2e3
Revises: e7a8b9c0d1f2
Create Date: 2026-09-03 00:35:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e7a8b9c0d1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("type", sa.String(64), nullable=False, server_default=""),
        sa.Column("country", sa.String(8), nullable=False, server_default=""),
        sa.Column("contract_ref", sa.String(128), nullable=False, server_default=""),
        sa.Column("contract_valid_until", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("processor_id", sa.Integer(), sa.ForeignKey("processors.id"), nullable=False),
        sa.Column("destination_country", sa.String(8), nullable=False),
        sa.Column("data_categories", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("lawful_basis", sa.String(64), nullable=False, server_default=""),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transfers_processor_id", "transfers", ["processor_id"])

    op.execute(
        """
        INSERT INTO processors (name, type, country, contract_ref, contract_valid_until, is_active, created_at)
        SELECT 'Groq LLM Inference', 'LLM_PROVIDER', 'US', 'DPA-GROQ-2026-001', '2027-12-31', TRUE, NOW()
        WHERE NOT EXISTS (SELECT 1 FROM processors WHERE name = 'Groq LLM Inference')
        """
    )
    op.execute(
        """
        INSERT INTO transfers (processor_id, destination_country, data_categories, lawful_basis, restricted, created_at)
        SELECT p.id, 'US', '["aggregate_platform_metrics"]', 'LEGITIMATE_INTEREST', FALSE, NOW()
        FROM processors p
        WHERE p.name = 'Groq LLM Inference'
        AND NOT EXISTS (SELECT 1 FROM transfers t WHERE t.processor_id = p.id AND t.destination_country = 'US')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_transfers_processor_id", table_name="transfers")
    op.drop_table("transfers")
    op.drop_table("processors")
