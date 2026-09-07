"""add notices + notice_versions tables; link consents/evidence to
notice_version_id

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-04 02:00:00.000000

R1-04 (A-01, A-02, A-07, D-04, A-12): introduces the Notice/NoticeVersion
entity the gap register calls for, and finishes wiring `notice_version_id`
into both `consents` and `consent_evidence`.

`consent_evidence.notice_version_id` already existed (added by
d6f7a8b9c0e1) as a stand-in FK to `purpose_versions.id`, written by
services/consent.py as `ctx.notice_version or consent.purpose_version_id`
before any real Notice entity existed. Once it is repointed at
`notice_versions.id`, any existing value is a purpose_version id, not a
notice_version id - a real row in the *wrong* table, not a null. Left in
place it would either violate the new FK (if no notice_version happens to
share that id) or silently reference an unrelated notice_version (if one
does). Both are worse than null, so existing values are cleared here; the
same is true of `notice_hash`, which was computed from purpose_version
content, not any real notice. Application code (services/consent.py) now
populates both correctly from a real, published NoticeVersion going forward
- exactly the "referenced by every consent recorded after publication"
requirement, which by construction cannot apply retroactively to consents
recorded before this migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("purpose_id", sa.Integer(), sa.ForeignKey("purposes.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notices_tenant_id", "notices", ["tenant_id"])
    # unique=True (not a separate UniqueConstraint + index): mirrors how
    # every other unique=True,index=True column in this codebase compiles
    # (see e.g. ix_purposes_code), and is what `alembic check` expects to
    # match Notice.purpose_id's `mapped_column(..., unique=True, index=True)`.
    op.create_index("ix_notices_purpose_id", "notices", ["purpose_id"], unique=True)

    op.create_table(
        "notice_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notice_id", sa.Integer(), sa.ForeignKey("notices.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("language_default", sa.String(8), nullable=False, server_default="en"),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("translations", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("data_items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("purposes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("services_enabled", sa.Text(), nullable=False, server_default=""),
        sa.Column("retention_period_days", sa.Integer(), nullable=True),
        sa.Column("retention_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("child_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("links", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("contact_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_by", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
    )
    op.create_index("ix_notice_versions_notice_id", "notice_versions", ["notice_id"])

    # Link consents to the notice version pinned at grant/renew time.
    op.add_column("consents", sa.Column("notice_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "consents_notice_version_id_fkey", "consents", "notice_versions", ["notice_version_id"], ["id"]
    )

    # Repoint consent_evidence.notice_version_id at notice_versions instead
    # of purpose_versions - see module docstring for why existing values
    # must be cleared first.
    op.execute("UPDATE consent_evidence SET notice_version_id = NULL, notice_hash = NULL WHERE notice_version_id IS NOT NULL")
    op.drop_constraint("consent_evidence_notice_version_id_fkey", "consent_evidence", type_="foreignkey")
    op.create_foreign_key(
        "consent_evidence_notice_version_id_fkey", "consent_evidence", "notice_versions",
        ["notice_version_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("consent_evidence_notice_version_id_fkey", "consent_evidence", type_="foreignkey")
    # Values were cleared on upgrade and cannot be recovered; re-point the FK
    # back at purpose_versions (its pre-R1-04 target) with the column left null.
    op.execute("UPDATE consent_evidence SET notice_version_id = NULL WHERE notice_version_id IS NOT NULL")
    op.create_foreign_key(
        "consent_evidence_notice_version_id_fkey", "consent_evidence", "purpose_versions",
        ["notice_version_id"], ["id"],
    )

    op.drop_constraint("consents_notice_version_id_fkey", "consents", type_="foreignkey")
    op.drop_column("consents", "notice_version_id")

    op.drop_index("ix_notice_versions_notice_id", table_name="notice_versions")
    op.drop_table("notice_versions")

    op.drop_index("ix_notices_purpose_id", table_name="notices")
    op.drop_index("ix_notices_tenant_id", table_name="notices")
    op.drop_table("notices")
