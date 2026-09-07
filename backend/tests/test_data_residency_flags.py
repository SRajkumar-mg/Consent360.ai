"""R3-12: data-residency / SDF-readiness config flags (app/core/config.py).

These flags are declarative only - see the module docstring on
DATA_RESIDENCY_PRIMARY_DB_REGION and BACKUP_ENCRYPTION_ENABLED in config.py,
and backend/docs/compliance/DATA_RESIDENCY_SDF_READINESS.md - so these tests
check exactly what is actually implemented: the fields exist with the
documented defaults, and the one narrow, opt-in self-consistency check in
production_issues() fires only when a deployment actively asserts
DATA_RESIDENCY_ASSERT_INDIA_ONLY without recording a region. They do not
(and cannot) test that any real infrastructure is actually India-resident or
encrypted - no code in this repo makes that claim.
"""
import base64
import os

from app.core.config import Settings


def _new_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _hardened_kwargs(**overrides) -> dict:
    """A production Settings baseline that satisfies every OTHER
    production_issues() check (valid 32-byte encryption key, a real Redis
    URL, etc.), so these tests only ever exercise the R3-12 residency check
    itself rather than re-asserting every other lane's own hardening
    checks. See tests/test_encryption_hardening.py for those checks'
    dedicated coverage."""
    base = dict(
        ENVIRONMENT="production",
        FIELD_ENCRYPTION_KEY=_new_key(),
        JWT_SECRET="x" * 40,
        ALLOW_LEGACY_INTEGRATION_KEY=False,
        INTEGRATION_API_KEY="a-real-per-tenant-key-value",
        CORS_ORIGINS="https://admin.example.com",
        REDIS_URL="redis://localhost:6379/0",
    )
    base.update(overrides)
    return base


def test_residency_and_backup_flags_default_to_unasserted():
    s = Settings()
    assert s.DATA_RESIDENCY_PRIMARY_DB_REGION == "unspecified"
    assert s.DATA_RESIDENCY_BACKUP_REGION == "unspecified"
    assert s.DATA_RESIDENCY_ASSERT_INDIA_ONLY is False
    assert s.SDF_NOTIFIED is False
    assert s.BACKUP_ENCRYPTION_ENABLED is False
    assert s.BACKUP_RETENTION_DAYS == 35
    assert s.BACKUP_RPO_MINUTES == 60
    assert s.BACKUP_RTO_MINUTES == 240


def test_default_flags_never_block_production_startup():
    """The whole point of these flags defaulting to false/'unspecified' is
    that a deployment which has never heard of R3-12 is not newly blocked
    from starting in production - see config.py's comment on why
    BACKUP_ENCRYPTION_ENABLED is deliberately NOT part of this list."""
    s = Settings(**_hardened_kwargs())
    assert s.production_issues() == []


def test_asserting_india_only_residency_without_a_region_fails_closed():
    s = Settings(**_hardened_kwargs(DATA_RESIDENCY_ASSERT_INDIA_ONLY=True))
    issues = s.production_issues()
    assert any("DATA_RESIDENCY_ASSERT_INDIA_ONLY" in i for i in issues)


def test_asserting_india_only_residency_with_recorded_regions_passes():
    s = Settings(**_hardened_kwargs(
        DATA_RESIDENCY_ASSERT_INDIA_ONLY=True,
        DATA_RESIDENCY_PRIMARY_DB_REGION="IN-MUMBAI",
        DATA_RESIDENCY_BACKUP_REGION="IN-MUMBAI",
    ))
    assert s.production_issues() == []


def test_flags_are_not_asserted_outside_production():
    s = Settings(
        ENVIRONMENT="development",
        DATA_RESIDENCY_ASSERT_INDIA_ONLY=True,
    )
    assert s.production_issues() == []
