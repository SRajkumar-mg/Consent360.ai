"""Issue a tenant-bound integration API key for each demo tenant.

Closes R3-01 ("no shared static key"): every demo site should authenticate
to the integration API (`POST /consent/customer-context`,
`DELETE /crm/customers/by-email/{email}`) with its own `c360_...` key rather
than the unbound legacy `INTEGRATION_API_KEY` (see
`app.core.config.Settings.ALLOW_LEGACY_INTEGRATION_KEY`).

Idempotent and safe to re-run: a tenant that already has an active (not
revoked, not expired) key is left alone - the plaintext of an existing key
was already shown once at creation time and cannot be recovered (only its
hash is stored), so this script never regenerates or reprints one just
because it exists. Run it again after a rotation (see
`POST /organizations/{id}/api-keys/{id}/rotate`) if you need the new
plaintext echoed to a log instead of the admin console response.

Each tenant's Organization row must already exist - run `seed_orgs.py`
first (or let `app.services.tenancy.resolve_tenant_id` auto-provision it the
first time that source_app's traffic touches the platform).

CAREER_HUB additionally gets SCOPE_FIDUCIARY_ASSERT: the job-portal demo
authenticates its own visitor before ever calling the consent platform and
has no OTP-verification UI of its own (see R3-05 / test_fiduciary_assertion),
so its key is allowed to assert that identity at handoff instead of forcing
a portal-side email-OTP step. No other demo tenant gets this scope - Codex,
SkillLearn and the CRM portal hand off through the admin console's
self-service portal, which does implement OTP verification.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.api_keys import SCOPE_FIDUCIARY_ASSERT, SCOPE_INTEGRATION_WRITE, generate_api_key
from app.core.database import SessionLocal
from app.models.entities import ApiKey, Organization

# (tenant_code, extra_scopes) - every tenant gets SCOPE_INTEGRATION_WRITE.
DEMO_TENANT_KEYS = [
    ("JOBHUB", []),
    ("CODEX", []),
    ("SKILLLEARN", []),
    ("CRM_PORTAL", []),
    ("CAREER_HUB", [SCOPE_FIDUCIARY_ASSERT]),
]

db = SessionLocal()

for code, extra_scopes in DEMO_TENANT_KEYS:
    org = db.query(Organization).filter(Organization.code == code).first()
    if not org:
        print(f"Skipping {code}: no Organization row yet - run seed_orgs.py first")
        continue

    existing = (
        db.query(ApiKey)
        .filter(ApiKey.tenant_id == org.id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
        .first()
    )
    if existing:
        print(f"{code}: already has an active key ({existing.key_prefix}...) - not reissuing")
        continue

    scopes = [SCOPE_INTEGRATION_WRITE, *extra_scopes]
    plaintext, prefix, key_hash = generate_api_key(code)
    key = ApiKey(tenant_id=org.id, name=f"{code} demo integration key", key_prefix=prefix,
                 key_hash=key_hash, scopes=scopes)
    db.add(key)
    db.commit()
    print(f"{code}: created key {prefix}... scopes={scopes}")
    print(f"  plaintext (shown once - store it now): {plaintext}")

db.close()
print("Done!")
