"""extend consent_evidence with notice/context/affirmative-act fields

Revision ID: d6f7a8b9c0e1
Revises: c5e6f7a8b9d0
Create Date: 2026-09-03 00:25:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6f7a8b9c0e1"
down_revision: Union[str, None] = "c5e6f7a8b9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("consent_evidence", sa.Column("notice_version_id", sa.Integer(), sa.ForeignKey("purpose_versions.id"), nullable=True))
    op.add_column("consent_evidence", sa.Column("notice_hash", sa.String(64), nullable=True))
    op.add_column("consent_evidence", sa.Column("language", sa.String(8), nullable=False, server_default="en"))
    op.add_column("consent_evidence", sa.Column("ip_address", sa.String(256), nullable=True))
    op.add_column("consent_evidence", sa.Column("user_agent", sa.String(512), nullable=True))
    op.add_column("consent_evidence", sa.Column("session_id", sa.String(128), nullable=True))
    op.add_column("consent_evidence", sa.Column("ui_control_id", sa.String(128), nullable=True))
    op.add_column("consent_evidence", sa.Column("banner_version", sa.String(32), nullable=True))
    op.add_column("consent_evidence", sa.Column("screen_id", sa.String(128), nullable=True))
    op.add_column("consent_evidence", sa.Column("affirmative_action", sa.String(16), nullable=False, server_default="CLICK"))
    op.add_column("consent_evidence", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("consent_evidence", sa.Column("signature", sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("signature", "content_hash", "affirmative_action", "screen_id", "banner_version",
                "ui_control_id", "session_id", "user_agent", "ip_address", "language",
                "notice_hash", "notice_version_id"):
        op.drop_column("consent_evidence", col)
