"""drop aadhar_number from crm_customers

Revision ID: 265ec61f7106
Revises: aa4dd50f6f92
Create Date: 2026-09-04 08:30:00.000000

R1-12/H-11: Aadhaar collection needs a specific lawful basis under the
Aadhaar Act 2016 that this demo CRM never established - CrmCustomerLoginIn
never even collects it, only seed.py's demo rows ever set a value - so it
was pure unnecessary data-minimisation risk with no purpose behind it.
Removed from the default schema entirely rather than merely gated, per the
workbook's definition of done ("No Aadhaar column in default schema").
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "265ec61f7106"
down_revision: Union[str, None] = "aa4dd50f6f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("crm_customers", "aadhar_number")


def downgrade() -> None:
    # Schema-shape reversal only - the encrypted values dropped by upgrade()
    # are not recoverable from this migration (see the pre-migration
    # database snapshot taken before this migration was ever applied for
    # that purpose). The column is nullable, matching its pre-drop
    # definition, so downgrading never requires backfilling a value.
    op.add_column("crm_customers", sa.Column("aadhar_number", sa.String(256), nullable=True))
