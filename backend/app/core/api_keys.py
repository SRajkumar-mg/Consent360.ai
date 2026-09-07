"""Tenant-bound integration API keys: c360_<tenant_code>_<32 url-safe chars>.

Only the SHA-256 hash and a short prefix are stored; the plaintext key is
shown to the caller once, at creation or rotation time.
"""
import hashlib
import secrets

API_KEY_PREFIX = "c360"

# `ApiKey.scopes` is a plain list of these strings, checked by
# `app.api.deps.require_scope` (see app/api/deps.py). A key created with no
# explicit scopes (the `ApiKeyCreate` default) gets SCOPE_INTEGRATION_WRITE
# only, i.e. it can identify a customer and mint a consent context - the one
# thing every integration caller needs. Every other capability is a
# deliberate, separate opt-in:
#   - SCOPE_CUSTOMER_PURGE: call DELETE /crm/customers/by-email/{email}.
#   - SCOPE_FIDUCIARY_ASSERT: have /consent/customer-context mark the
#     resulting context as already identity-verified (R3-05's
#     "fiduciary-asserted" handoff) instead of requiring the principal to
#     complete email-OTP verification in the self-service portal. Only
#     meaningful for a tenant that has genuinely authenticated its own user
#     before calling the integration API - granting it to every tenant would
#     let any caller skip verification entirely, which is exactly the kind
#     of blanket trust this scope exists to avoid.
# The legacy, unbound `INTEGRATION_API_KEY` carries no scopes at all and is
# never subject to these checks (see require_scope) - it predates scopes and
# is being phased out, not extended.
SCOPE_INTEGRATION_WRITE = "integration.write"
SCOPE_CUSTOMER_PURGE = "customer.purge"
SCOPE_FIDUCIARY_ASSERT = "context.fiduciary_assert"

# R3-10 Consent Manager / consent-validation scopes. Deliberate per-tenant
# opt-ins rather than capabilities every `integration.write` key inherits:
# validating consent before processing, and brokering artefacts on a
# principal's behalf, are distinct from identifying a customer, and the
# decision endpoint discloses the consent state of a named principal.
SCOPE_DECISION_EVALUATE = "decision.evaluate"
SCOPE_ARTEFACT_READ = "artefact.read"
SCOPE_ARTEFACT_WRITE = "artefact.write"


def generate_api_key(tenant_code: str) -> tuple[str, str, str]:
    """Return (plaintext_key, key_prefix, key_hash).

    `key_prefix` is stored (and indexed) alongside the hash so an admin
    listing can show a stable, human-recognisable fragment of a key without
    ever persisting -- or disclosing part of -- the plaintext. It is drawn
    from its own independent randomness (never a slice of the plaintext's
    random segment, which would leak part of the secret into a value that is
    listed back to admins) and it must always differ between successive
    generations for the same tenant (e.g. after a rotation), which the fresh
    random draw guarantees regardless of tenant-code length. Nothing
    authenticates by key_prefix -- lookup is always by key_hash -- so this
    value is cosmetic only.
    """
    random_part = secrets.token_urlsafe(24)[:32]
    plaintext = f"{API_KEY_PREFIX}_{tenant_code.lower()}_{random_part}"
    tenant_hint = tenant_code.lower()[:4]
    prefix_random = secrets.token_urlsafe(8)[:6]
    key_prefix = f"{API_KEY_PREFIX}_{tenant_hint}_{prefix_random}"
    return plaintext, key_prefix, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def looks_like_api_key(value: str) -> bool:
    return value.startswith(f"{API_KEY_PREFIX}_")
