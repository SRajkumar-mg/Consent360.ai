"""add scheduler_runs and kpi_snapshots tables

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5e7
Create Date: 2026-09-03 00:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="RUNNING"),
        sa.Column("counts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_scheduler_runs_job_name", "scheduler_runs", ["job_name"])

    op.create_table(
        "kpi_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_kpi_snapshots_captured_at", "kpi_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_kpi_snapshots_captured_at", table_name="kpi_snapshots")
    op.drop_table("kpi_snapshots")
    op.drop_index("ix_scheduler_runs_job_name", table_name="scheduler_runs")
    op.drop_table("scheduler_runs")
