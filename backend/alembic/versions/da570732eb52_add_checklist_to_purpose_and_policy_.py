"""add checklist column to purpose_versions and policy_versions

Revision ID: da570732eb52
Revises: d91c0170285a
Create Date: 2026-09-04 08:20:00.000000

R2-09/A-09: persists the plain-language & dark-pattern review record that
gates publishing server-side, instead of only in the reviewing browser's
localStorage (see frontend/src/components/NoticeChecklist.tsx).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "da570732eb52"
down_revision: Union[str, None] = "d91c0170285a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("purpose_versions", sa.Column("checklist", sa.JSON(), nullable=True))
    op.add_column("policy_versions", sa.Column("checklist", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("policy_versions", "checklist")
    op.drop_column("purpose_versions", "checklist")
