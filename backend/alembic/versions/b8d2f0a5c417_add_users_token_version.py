"""add users.token_version for durable token revocation

Token revocation currently lives in an in-memory (or Redis-backed) store, so
"log out, restart the process, replay the token" succeeds and a deployment
without Redis has no revocation at all. A per-user generation counter fixes
that durably: bump it on logout and on deactivation, embed it as a claim, and
refuse any token whose claim is behind.

This migration adds the column only. Minting the claim
(app/core/security.py) and comparing it per request (app/api/deps.py) belong
to another workstream - until those land, nothing writes or reads this value,
which is why the default is 0 and the column is NOT NULL: every existing user
starts at generation 0, so the first bump is unambiguous.

Revision ID: b8d2f0a5c417
Revises: a7c1e9d4b302
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f0a5c417"
down_revision: Union[str, None] = "a7c1e9d4b302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
