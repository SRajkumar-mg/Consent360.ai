"""add gpc_signal column to consent_evidence

Revision ID: aa4dd50f6f92
Revises: da570732eb52
Create Date: 2026-09-04 08:25:00.000000

Persists the Global Privacy Control signal (Sec-GPC request header), read
server-side at the moment a consent decision is recorded
(app/api/routes/portal.py), so a GPC objection is durable evidence rather
than something only ever enforced client-side in the browser.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "aa4dd50f6f92"
down_revision: Union[str, None] = "da570732eb52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("consent_evidence", sa.Column("gpc_signal", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("consent_evidence", "gpc_signal")
