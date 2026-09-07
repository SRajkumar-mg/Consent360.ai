"""add checklist column to notice_versions

Revision ID: d1e2f3a4b5c6
Revises: c3f4a5b6c7d8
Create Date: 2026-09-04 21:00:00.000000

A-09/R2-09: closes the gap the checklist column added to purpose_versions
and policy_versions (da570732eb52) left open on the notice path - see
app/api/routes/notices.py::publish_notice, which now refuses to publish a
notice version without one, and NoticeVersion.checklist's docstring for the
grandfathering decision on rows that predate this column (they stay NULL
and stay published; nothing already live is retroactively un-published).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notice_versions", sa.Column("checklist", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("notice_versions", "checklist")
