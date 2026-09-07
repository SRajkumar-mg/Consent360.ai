"""Regression tests for the adversarial-review fix round on R3-02/R3-03/R3-04.

One test (or small group) per defect the review reproduced live:

1. An HMAC search-key change fabricated duplicate identities, because the
   ordinary upsert paths read a lookup miss as "this is a new person".
2. The structured access log wrote a customer's email-shaped external_id
   into its `path` field verbatim.
3. `production_issues()` bare-truthiness-checked FIELD_ENCRYPTION_KEY, so a
   whitespace-only or wrong-length key started fine and then raised an
   uncaught ValueError mid-request.
4. Token revocation is process-local with no Redis, so logout stopped
   holding across a restart.
5. `MFA_ENFORCED` was read by nothing at all.
6. Three call sites decoded a token without checking `ctx`/`type`/`aud`.
7. Org tokens carried neither `iss` nor `aud`.
8. Account lockout was counted per username, making it an unauthenticated
   denial of service against any known account.

These live in their own file rather than in test_auth_hardening.py /
test_encryption_hardening.py / test_observability.py so the fix round is
reviewable as one unit against the report that describes it.

Like test_encryption_hardening.py, any test here that flips a crypto/config
env var must reset BOTH encryption.py's process-global caches AND
get_settings()'s lru_cache afterwards, or it leaks altered state into every
other test in the session - the `crypto_state` fixture does that teardown.
"""
import base64
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core import encryption as enc
from app.core.config import Settings, get_settings
from app.core.encryption import find_by_search_digest, hmac_digest, search_digests
from app.core.rbac import ALL_PERMISSIONS
from app.core.security import (
    create_access_token,
    create_mfa_pending_token,
    hash_password,
    login_failure_delay_seconds,
)
from app.models.entities import (
    ConsentContext,
    CrmCustomer,
    Customer,
    Organization,
    OrganizationUser,
    Role,
    User,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture()
def crypto_state():
    yield
    enc._reset_for_tests()
    get_settings.cache_clear()


def _new_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _settings(**overrides) -> Settings:
    """A Settings built from explicit values only, so backend/.env cannot
    make an assertion here pass or fail by accident."""
    base = dict(
        ENVIRONMENT="production",
        FIELD_ENCRYPTION_KEY=_new_key(),
        JWT_SECRET="x" * 40,
        INTEGRATION_API_KEY="not-the-demo-key",
        ALLOW_LEGACY_INTEGRATION_KEY=False,
        CORS_ORIGINS="https://app.example.com",
        REDIS_URL="redis://localhost:6379/0",
    )
    base.update(overrides)
    return Settings(**base)


class _ForceClientIp:
    """Test-only ASGI shim that pins `scope["client"]`, so a test can send
    two requests that the app sees as coming from two different addresses.
    Starlette's TestClient has no parameter for this."""

    def __init__(self, app, ip: str):
        self.app = app
        self.ip = ip

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["client"] = (self.ip, 44444)
        await self.app(scope, receive, send)


# --------------------------------------------------------------------------- #
# 1. CRITICAL - an HMAC key change must never fabricate a duplicate identity
# --------------------------------------------------------------------------- #

def test_a_digest_written_under_the_old_key_is_still_found_after_a_key_change(monkeypatch, crypto_state):
    """The reviewer's exact operator action: HMAC_SEARCH_KEY was unset (so
    digests were keyed by FIELD_ENCRYPTION_KEY), then it was set to a
    distinct key and the process restarted."""
    aes_key = _new_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", aes_key)
    monkeypatch.delenv("HMAC_SEARCH_KEY", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()
    old_digest = hmac_digest("aarav.patel@example.com")

    monkeypatch.setenv("HMAC_SEARCH_KEY", _new_key())
    get_settings.cache_clear()
    enc._reset_for_tests()

    new_digest = hmac_digest("aarav.patel@example.com")
    candidates = search_digests("aarav.patel@example.com")

    assert new_digest != old_digest, "writes must move to the new key"
    assert candidates[0] == new_digest, "the primary (write) digest must come first"
    assert old_digest in candidates, (
        "the previously-effective key must be retained for LOOKUP - without it every "
        "existing row becomes a miss, and every upsert path reads a miss as 'new customer'"
    )


def test_crm_login_after_an_hmac_key_change_returns_the_original_customer(db, monkeypatch, crypto_state):
    """End-to-end reproduction: replaying the SAME `POST /crm/login` after an
    HMAC key change used to return a brand-new id with `created: true`, and
    to leave a second CrmCustomer AND a second Customer behind."""
    from app.core.database import get_db
    from app.main import app

    def _override():
        yield db

    email = "aarav.patel.fixround@example.com"
    payload = {"name": "Aarav Patel", "email": email, "phone": "+91-90000-00001",
               "source_app": "CRM_PORTAL"}

    aes_key = _new_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", aes_key)
    monkeypatch.delenv("HMAC_SEARCH_KEY", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app) as c:
            first = c.post("/crm/login", json=payload)
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["created"] is True
        original_id = first_body["customer"]["id"]

        # The operator sets a dedicated HMAC key and restarts.
        monkeypatch.setenv("HMAC_SEARCH_KEY", _new_key())
        get_settings.cache_clear()
        enc._reset_for_tests()

        with TestClient(app) as c:
            second = c.post("/crm/login", json=payload)
        assert second.status_code == 200
        second_body = second.json()
    finally:
        app.dependency_overrides.clear()

    assert second_body["created"] is False, "a key change must not make an existing person look new"
    assert second_body["customer"]["id"] == original_id
    assert second_body["customer"]["phone"] == "+91-90000-00001", "profile fields must not be reset"

    db.expire_all()
    crm_rows = db.query(CrmCustomer).filter(
        CrmCustomer.email_search.in_(search_digests(email))
    ).all()
    assert len(crm_rows) == 1, "no duplicate CrmCustomer may be fabricated"
    customers = db.query(Customer).filter(
        Customer.email_search.in_(search_digests(email))
    ).all()
    assert len(customers) == 1, "no duplicate Consent360 Customer may be fabricated"


def test_a_row_found_under_a_retired_key_is_lazily_rehashed_to_the_primary(db, monkeypatch, crypto_state):
    aes_key = _new_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", aes_key)
    monkeypatch.delenv("HMAC_SEARCH_KEY", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()

    email = "lazy.rehash.fixround@example.com"
    row = CrmCustomer(name="Lazy Rehash", email=email, email_search=hmac_digest(email))
    db.add(row)
    db.commit()
    old_digest = row.email_search

    monkeypatch.setenv("HMAC_SEARCH_KEY", _new_key())
    get_settings.cache_clear()
    enc._reset_for_tests()
    new_digest = hmac_digest(email)
    assert new_digest != old_digest

    found = find_by_search_digest(db.query(CrmCustomer), CrmCustomer.email_search, email)
    assert found is not None and found.id == row.id
    db.commit()
    db.expire_all()

    assert db.get(CrmCustomer, row.id).email_search == new_digest, (
        "a hit under a retired key must be rewritten to the primary digest, so the "
        "fallback keys can eventually be dropped"
    )


def test_no_code_points_operators_at_a_remediation_script_that_does_not_exist():
    """encryption.py and config.py both told operators to run
    `scripts/rehash_search_columns.py`. No such file was ever written; the
    real tool is `scripts/rotate_encryption_keys.py --hmac`."""
    offenders = []
    for base in ("app", "scripts"):
        for path in (BACKEND_DIR / base).rglob("*.py"):
            text = path.read_text()
            for match in re.finditer(r"[\w/\.]*rehash_search_columns[\w\.]*", text):
                offenders.append(f"{path.relative_to(BACKEND_DIR).as_posix()}: {match.group(0)}")
    assert not offenders, (
        "Code points operators at a script that does not exist. Either create it or name the "
        "real tool (`python -m scripts.rotate_encryption_keys --hmac`):\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 2. HIGH - the access log must not carry a customer identifier verbatim
# --------------------------------------------------------------------------- #

def test_email_shaped_external_id_is_redacted_in_the_access_log(db, client, staff_token, caplog):
    """The integration API places no format constraint on `customer_id`, so
    an external_id IS routinely an email address; reading that customer used
    to emit `"path": "/customers/leaky.person@example.com"`."""
    leaky = "leaky.person.fixround@example.com"
    db.add(Customer(
        external_id=leaky, name="Leaky Person", email=leaky, source_app="LEAK_TEST",
    ))
    db.commit()

    with caplog.at_level(logging.INFO, logger="consent360.access"):
        resp = client.get(f"/customers/{leaky}", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200

    pii = [r for r in caplog.records if getattr(r, "event", None) == "PII_ACCESS"]
    assert pii, "expected a PII_ACCESS record for the read"
    assert pii[-1].path == "/customers/{id}"
    for record in caplog.records:
        for field in ("path", "query", "event_detail"):
            assert leaky not in str(getattr(record, field, "")), (
                f"{leaky} leaked verbatim into access-log field {field!r}"
            )
        assert leaky not in record.getMessage()


@pytest.mark.parametrize("raw, expected", [
    ("/customers/leaky.person@example.com", "/customers/{id}"),
    ("/customers/CUST-123", "/customers/{id}"),
    ("/organizations/7/customers", "/organizations/{id}/customers"),
    ("/consent/context/consume/eyJhbGciOiJIUzI1NiJ9.abc.def", "/consent/context/consume/{id}"),
    ("/customers", "/customers"),
    ("/admin/roles", "/admin/roles"),
    ("/crm/customers/by-email/x@y.com", "/crm/customers/by-email/{id}"),
])
def test_normalize_path_redacts_every_identifier_segment(raw, expected):
    # Imported inside the test, not at module level, on purpose: importing
    # app.core.access_log at COLLECTION time creates the
    # `consent360.access` logger before conftest's session fixture runs
    # alembic, and alembic/env.py's fileConfig() disables every logger
    # that already exists (logging.config's disable_existing_loggers
    # default). The access log then emits nothing for the rest of the
    # session and the redaction test above silently passes on an empty
    # record list. See the lane report - it is a live footgun for anything
    # that runs alembic in-process after importing the app.
    from app.core.access_log import _normalize_path

    assert _normalize_path(raw) == expected


# --------------------------------------------------------------------------- #
# 3. The production startup gate must check usability, not truthiness
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad_key, expected", [
    ("   ", "empty or whitespace-only"),
    (base64.urlsafe_b64encode(b"x" * 31).decode(), "32 bytes"),
    ("not!base64!at!all", "base64"),
])
def test_present_but_unusable_encryption_key_is_caught_at_startup(bad_key, expected):
    """Each of these passed the old bare `if not self.FIELD_ENCRYPTION_KEY`
    gate and then raised an uncaught ValueError out of `_load_keyring()` on
    the first real encrypt/decrypt - mid-request, in production."""
    issues = _settings(FIELD_ENCRYPTION_KEY=bad_key).production_issues()
    assert any(expected in i and "FIELD_ENCRYPTION_KEY" in i for i in issues), issues


def test_unusable_previous_keys_are_caught_too():
    issues = _settings(FIELD_ENCRYPTION_KEY_PREVIOUS=f"{_new_key()},zzzz").production_issues()
    assert any("FIELD_ENCRYPTION_KEY_PREVIOUS[1]" in i for i in issues), issues


def test_whitespace_only_jwt_secret_is_rejected():
    """40 spaces is 40 characters long and passed the old length check."""
    issues = _settings(JWT_SECRET=" " * 40).production_issues()
    assert any("JWT_SECRET" in i for i in issues), issues


def test_secret_config_issues_is_environment_independent():
    """A malformed key is a latent crash regardless of ENVIRONMENT, so the
    check must be callable outside production too."""
    dev = _settings(ENVIRONMENT="development", FIELD_ENCRYPTION_KEY="   ")
    assert dev.production_issues() == [], "the production gate stays inert outside production"
    assert any("FIELD_ENCRYPTION_KEY" in i for i in dev.secret_config_issues())


def test_a_usable_production_configuration_still_starts():
    assert _settings().production_issues() == []


# --------------------------------------------------------------------------- #
# 4. Token revocation with no durable store must not be deployable unnoticed
# --------------------------------------------------------------------------- #

def test_production_refuses_to_start_without_durable_token_revocation():
    issues = _settings(REDIS_URL="").production_issues()
    assert any("REDIS_URL" in i and "revocation" in i for i in issues), issues


# --------------------------------------------------------------------------- #
# 5. MFA_ENFORCED must not be settable while nothing honours it
# --------------------------------------------------------------------------- #

def test_mfa_enforced_fails_loudly_instead_of_being_silently_ignored():
    with pytest.raises(Exception) as exc:
        Settings(MFA_ENFORCED=True)
    assert "MFA_ENFORCED" in str(exc.value)


# --------------------------------------------------------------------------- #
# 6. Audience/claim checks at the call sites that skipped them
# --------------------------------------------------------------------------- #

def test_consume_context_rejects_a_staff_token_even_with_a_matching_context_row(db, client):
    """`consume_context` checked only the signature and `iss`, never
    `ctx`/`type`/`aud`. It was not exploitable only because the exact
    `consent_contexts.token` row match is a second, independent control -
    a coincidence of the storage design that D-10's token hashing changes.
    This pins the claim check itself, with the row match satisfied."""
    customer = Customer(
        external_id="CUST-FIXROUND-CTX", name="Ctx Fixround",
        email="ctx-fixround@example.com", source_app="FIXROUND_APP",
    )
    db.add(customer)
    db.commit()

    staff_shaped = create_access_token(customer.id, "not-a-context-token", "admin")
    db.add(ConsentContext(
        customer_id=customer.id, token=staff_shaped, source_app="FIXROUND_APP",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15), is_active=True,
    ))
    db.commit()

    resp = client.get(f"/consent/context/consume/{staff_shaped}")
    assert resp.status_code == 401, (
        "a staff-audience token must be refused on its own claims, not merely because it "
        f"happens not to appear in a table (got {resp.status_code})"
    )


def test_logout_does_not_revoke_a_token_that_is_not_this_users_refresh_token(db, client):
    from app.core import token_revocation

    role = db.query(Role).filter(Role.name == "admin").first()
    if not role:
        role = Role(name="admin", description="Full access", permissions=ALL_PERMISSIONS, is_system=True)
        db.add(role)
        db.flush()
    user = User(
        username="fixround-logout-user", full_name="Logout Fixround",
        email="fixround-logout@example.com", email_search=hmac_digest("fixround-logout@example.com"),
        password_hash=hash_password("Fixround@1234"), role_id=role.id, is_active=True,
    )
    db.add(user)
    db.commit()

    access = create_access_token(user.id, user.username, role.name)
    # An MFA-pending token: right `sub`/`sub_type`, wrong ctx/type - the
    # claims the logout branch did not check.
    pending = create_mfa_pending_token(user.id)
    pending_jti = jwt.get_unverified_claims(pending)["jti"]

    resp = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access}"},
        json={"refresh_token": pending},
    )
    assert resp.status_code == 200
    assert token_revocation.is_revoked(jwt.get_unverified_claims(access)["jti"]), \
        "the access token that authenticated the call must still be revoked"
    assert not token_revocation.is_revoked(pending_jti), \
        "logout must only revoke a token that really is this user's refresh token"


def test_access_log_actor_requires_the_full_staff_claim_set():
    from app.core.access_log import _extract_actor

    settings = get_settings()
    pending = create_mfa_pending_token(1)
    assert _extract_actor({b"authorization": f"Bearer {pending}".encode()}) == "unknown-bearer"

    good = create_access_token(1, "real-staff", "admin")
    assert _extract_actor({b"authorization": f"Bearer {good}".encode()}) == "real-staff"
    assert settings.JWT_AUDIENCE  # the claim the check now compares against


# --------------------------------------------------------------------------- #
# 7. Org tokens must meet the same RFC 8725 bar as staff tokens
# --------------------------------------------------------------------------- #

def _make_org_user(db, suffix: str):
    org = Organization(name=f"Fixround Org {suffix}", code=f"FIXROUND_{suffix}", is_active=True)
    db.add(org)
    db.flush()
    user = OrganizationUser(
        organization_id=org.id, username=f"fixround-org-{suffix}", full_name="Fixround Org User",
        email=f"fixround-org-{suffix}@example.com",
        email_search=hmac_digest(f"fixround-org-{suffix}@example.com"),
        password_hash=hash_password("OrgFixround@1234"), role="org_admin", is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(org)
    return org, user


def test_org_login_still_works_and_its_token_now_carries_iss_and_aud(db, client):
    org, user = _make_org_user(db, "AUD")
    settings = get_settings()

    login = client.post("/organizations/auth/login",
                        json={"username": user.username, "password": "OrgFixround@1234"})
    assert login.status_code == 200
    token = login.json()["access_token"]

    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == settings.JWT_ISSUER
    assert claims["aud"] == settings.JWT_AUDIENCE_ORG
    assert claims["aud"] != settings.JWT_AUDIENCE, "an org token must not share the staff audience"
    assert claims.get("jti")

    dash = client.get("/organizations/portal/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert dash.status_code == 200, "existing org login/dashboard must keep working"


@pytest.mark.parametrize("drop", ["iss", "aud"])
def test_a_legacy_shaped_org_token_is_rejected(db, client, drop):
    """Pre-fix org tokens carried neither claim; both must now fail closed."""
    org, user = _make_org_user(db, f"LEGACY{drop.upper()}")
    settings = get_settings()

    payload = {
        "sub": str(user.id), "sub_type": "org_user", "username": user.username,
        "org_id": org.id, "role": user.role, "type": "org-access", "ctx": "org-auth",
        "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE_ORG,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        "iat": datetime.now(timezone.utc),
    }
    payload.pop(drop)
    legacy = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    resp = client.get("/organizations/portal/dashboard", headers={"Authorization": f"Bearer {legacy}"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 8. Account lockout must not be an unauthenticated denial of service
# --------------------------------------------------------------------------- #

def _make_staff(db, username: str, password: str):
    role = db.query(Role).filter(Role.name == "admin").first()
    if not role:
        role = Role(name="admin", description="Full access", permissions=ALL_PERMISSIONS, is_system=True)
        db.add(role)
        db.flush()
    user = User(
        username=username, full_name="Lockout Fixround", email=f"{username}@example.com",
        email_search=hmac_digest(f"{username}@example.com"),
        password_hash=hash_password(password), role_id=role.id, is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _client_from(db, ip: str) -> TestClient:
    from app.core.database import get_db
    from app.main import app

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(_ForceClientIp(app, ip))


def test_an_attacker_cannot_lock_the_real_user_out_from_their_own_address(db):
    """The headline defect: five wrong passwords locked the legitimate user
    out for fifteen minutes, and the sixth attempt WITH THE CORRECT PASSWORD
    returned 423."""
    from app.core.config import get_settings as _gs
    from app.main import app

    password = "LockoutFixround@1234"
    username = "fixround-lockout-victim"
    _make_staff(db, username, password)
    threshold = _gs().ACCOUNT_LOCKOUT_THRESHOLD

    try:
        with _client_from(db, "203.0.113.9") as attacker:
            for _ in range(threshold):
                assert attacker.post(
                    "/auth/login", json={"username": username, "password": "wrong"}
                ).status_code == 401
            # The attacker has locked out their OWN address, as intended.
            assert attacker.post(
                "/auth/login", json={"username": username, "password": "wrong"}
            ).status_code == 423

        with _client_from(db, "198.51.100.4") as victim:
            resp = victim.post("/auth/login", json={"username": username, "password": password})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, (
        "the account holder logging in from their own address must be unaffected by "
        f"another address's failures (got {resp.status_code})"
    )


def test_repeated_failures_from_one_address_still_stop_that_address(db):
    from app.core.config import get_settings as _gs
    from app.main import app

    username = "fixround-lockout-same-ip"
    _make_staff(db, username, "LockoutFixround@1234")
    threshold = _gs().ACCOUNT_LOCKOUT_THRESHOLD

    try:
        with _client_from(db, "203.0.113.55") as attacker:
            for _ in range(threshold):
                attacker.post("/auth/login", json={"username": username, "password": "wrong"})
            locked = attacker.post(
                "/auth/login", json={"username": username, "password": "LockoutFixround@1234"}
            )
    finally:
        app.dependency_overrides.clear()

    assert locked.status_code == 423, "online guessing from one origin must still stop dead"


def test_login_failure_delay_is_zero_below_threshold_and_capped_above():
    assert login_failure_delay_seconds(0, threshold=5, cap_seconds=4.0) == 0.0
    assert login_failure_delay_seconds(4, threshold=5, cap_seconds=4.0) == 0.0
    assert login_failure_delay_seconds(5, threshold=5, cap_seconds=4.0) == 1.0
    assert login_failure_delay_seconds(6, threshold=5, cap_seconds=4.0) == 2.0
    # Capped: an unbounded backoff would occupy a worker thread per attempt
    # and become its own denial of service.
    assert login_failure_delay_seconds(30, threshold=5, cap_seconds=4.0) == 4.0


# --------------------------------------------------------------------------- #
# 9. OTP codes are a credential and must come from the CSPRNG
# --------------------------------------------------------------------------- #

def test_otp_codes_do_not_come_from_a_predictable_generator():
    """`random.randint` is Mersenne Twister: seedable, and its state is
    recoverable from observed outputs, so every later code becomes
    predictable. The OTP is the only thing gating another person's consent
    record on the self-service portal."""
    import app.services.otp as otp_module

    source = (BACKEND_DIR / "app" / "services" / "otp.py").read_text()
    assert not re.search(r"^\s*import random\b", source, re.MULTILINE), \
        "app/services/otp.py must not import the non-cryptographic `random` module"
    assert not re.search(r"(?<![\w.])random\.(randint|choice|randrange|random)\(", source), \
        "OTP generation must not use `random.*`"

    import random as stdlib_random

    stdlib_random.seed(1234)
    first = [otp_module._generate_code() for _ in range(5)]
    stdlib_random.seed(1234)
    second = [otp_module._generate_code() for _ in range(5)]
    assert first != second, (
        "codes reproduced after re-seeding stdlib `random` - the generator is seedable "
        "and therefore predictable"
    )
    assert all(len(c) == 6 and c.isdigit() for c in first)


def test_no_security_relevant_path_uses_the_stdlib_random_module():
    """Sweep: token, nonce, code, salt and secret generation must use
    `secrets` / `os.urandom` / `uuid4`, never `random`."""
    offenders = []
    for path in (BACKEND_DIR / "app").rglob("*.py"):
        text = path.read_text()
        if re.search(r"^\s*import random\b|^\s*from random import", text, re.MULTILINE):
            offenders.append(path.relative_to(BACKEND_DIR).as_posix())
    assert not offenders, (
        "The stdlib `random` module is a Mersenne Twister and must not appear anywhere in "
        "app/ - use `secrets` for anything that is a credential:\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 10. The residency/backup settings the residency tests import must exist
# --------------------------------------------------------------------------- #

def test_data_residency_and_backup_settings_exist_with_their_reviewed_defaults():
    """`tests/test_data_residency_flags.py` was committed while these fields
    were still uncommitted in config.py, so a clean checkout failed. Pin the
    exact names and defaults so the pair cannot drift apart again."""
    s = Settings(ENVIRONMENT="development")
    assert s.DATA_RESIDENCY_PRIMARY_DB_REGION == "unspecified"
    assert s.DATA_RESIDENCY_BACKUP_REGION == "unspecified"
    assert s.DATA_RESIDENCY_ASSERT_INDIA_ONLY is False
    assert s.SDF_NOTIFIED is False
    assert s.BACKUP_ENCRYPTION_ENABLED is False
    assert s.BACKUP_RETENTION_DAYS == 35
    assert s.BACKUP_RPO_MINUTES == 60
    assert s.BACKUP_RTO_MINUTES == 240


def test_asserting_india_only_residency_without_naming_a_region_still_fails_closed():
    """The one self-consistency check those flags feed in production_issues()
    must survive this round's hardening of the same function."""
    issues = _settings(DATA_RESIDENCY_ASSERT_INDIA_ONLY=True).production_issues()
    assert any("DATA_RESIDENCY" in i for i in issues), issues
    ok = _settings(
        DATA_RESIDENCY_ASSERT_INDIA_ONLY=True,
        DATA_RESIDENCY_PRIMARY_DB_REGION="IN-MUMBAI",
        DATA_RESIDENCY_BACKUP_REGION="IN-HYDERABAD",
    ).production_issues()
    assert ok == [], ok
