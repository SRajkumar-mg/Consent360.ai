"""add source_app to consent identity uniqueness

Allows independent consent records per (customer, purpose, data category,
processing activity, version) per website/app source, so the customer portal
can strictly scope data to the source the customer registered through.

Revision ID: a1b2c3d4e5f6
Revises: 96c45ff69f72
Create Date: 2026-08-13 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '96c45ff69f72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_consent_identity_version', 'consents', type_='unique')
    op.create_unique_constraint(
        'uq_consent_identity_version',
        'consents',
        ['customer_id', 'purpose_id', 'data_category_id', 'processing_activity_id', 'consent_version', 'source_app'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_consent_identity_version', 'consents', type_='unique')
    op.create_unique_constraint(
        'uq_consent_identity_version',
        'consents',
        ['customer_id', 'purpose_id', 'data_category_id', 'processing_activity_id', 'consent_version'],
    )