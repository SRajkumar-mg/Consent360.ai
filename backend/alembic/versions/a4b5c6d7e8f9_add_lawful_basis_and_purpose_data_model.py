"""enumerate lawful gateways on purposes/purpose_versions; add itemised
data_items, services_enabled, child_restricted, retention_policy_id

Revision ID: a4b5c6d7e8f9
Revises: f8a9b0c1d2e3
Create Date: 2026-09-04 01:00:00.000000

R1-05 (B-06, L-01, L-02, L-03): `Purpose.legal_basis` / `PurposeVersion.legal_basis`
were free text and the seed data used "LEGITIMATE_INTEREST", a GDPR concept
that does not exist under DPDP - the only lawful gateways are s.6 CONSENT or
one of the nine s.7(a)-(i) "certain legitimate uses" clauses. This migration:

  1. Fixes every existing row with an invalid legal_basis to a real gateway
     BEFORE the CHECK constraint is added, so the constraint can actually be
     validated against live data (the dev DB already has a
     "LEGITIMATE_INTEREST" purpose from the old seed.py). The `strictly_necessary`
     purpose (login/security/consent-record cookies) is remapped to S7_A
     (voluntary provision by the principal for a specified purpose, not
     objected to) - the same clause R1-08's objections register targets, so
     this is also the natural purpose to exercise C-06 against.
  2. Adds a CHECK constraint enumerating the ten valid values, so no future
     write - through the API or otherwise - can persist an invalid gateway.
  3. Adds `data_items`, `services_enabled`, `child_restricted` and
     `retention_policy_id` to both tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGAL_BASIS_CHECK = (
    "legal_basis IN ('CONSENT','S7_A','S7_B','S7_C','S7_D','S7_E','S7_F','S7_G','S7_H','S7_I')"
)


def upgrade() -> None:
    # 1. Fix invalid legal_basis values before the CHECK constraint lands.
    #    Any unrecognised legacy value (not just the known "LEGITIMATE_INTEREST")
    #    is remapped to S7_A rather than left to violate the new constraint.
    op.execute(
        f"""
        UPDATE purposes SET legal_basis = 'S7_A'
        WHERE legal_basis IS NULL OR NOT ({_LEGAL_BASIS_CHECK})
        """
    )
    op.execute(
        f"""
        UPDATE purpose_versions SET legal_basis = 'S7_A'
        WHERE legal_basis IS NULL OR NOT ({_LEGAL_BASIS_CHECK})
        """
    )

    # 2. New columns.
    op.add_column("purposes", sa.Column("services_enabled", sa.Text(), nullable=False, server_default=""))
    op.add_column("purposes", sa.Column("child_restricted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("purposes", sa.Column("retention_policy_id", sa.String(64), nullable=True))

    op.add_column("purpose_versions", sa.Column("data_items", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("purpose_versions", sa.Column("services_enabled", sa.Text(), nullable=False, server_default=""))
    op.add_column("purpose_versions", sa.Column("child_restricted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("purpose_versions", sa.Column("retention_policy_id", sa.String(64), nullable=True))

    # 3. Enumerate the lawful-gateway taxonomy at the database layer.
    op.create_check_constraint("ck_purposes_legal_basis", "purposes", _LEGAL_BASIS_CHECK)
    op.create_check_constraint("ck_purpose_versions_legal_basis", "purpose_versions", _LEGAL_BASIS_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_purpose_versions_legal_basis", "purpose_versions", type_="check")
    op.drop_constraint("ck_purposes_legal_basis", "purposes", type_="check")

    op.drop_column("purpose_versions", "retention_policy_id")
    op.drop_column("purpose_versions", "child_restricted")
    op.drop_column("purpose_versions", "services_enabled")
    op.drop_column("purpose_versions", "data_items")

    op.drop_column("purposes", "retention_policy_id")
    op.drop_column("purposes", "child_restricted")
    op.drop_column("purposes", "services_enabled")
    # Note: the S7_A remap of previously-invalid legal_basis values in
    # upgrade() is not reversed here - there is no way to recover what the
    # original (invalid) value was, and leaving a now-valid, defensible
    # gateway in place on downgrade is strictly safer than reintroducing a
    # value ("LEGITIMATE_INTEREST") that was never lawful under DPDP.
