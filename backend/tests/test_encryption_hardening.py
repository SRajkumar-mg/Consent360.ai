"""R3-03 (encryption, secrets, transport) behavioural tests.

Covers: production fails closed with no key, AES key rotation (old
ciphertext keeps decrypting, new writes use the new key, a live re-encrypt
job moves old rows to the new key), a separate HMAC search-digest key, the
AWS KMS envelope-decryption key provider, Docker/K8s-style "*_FILE" secret
loading, Settings.production_issues(), and the CORS/security-header
baseline on real HTTP responses.

`encryption.py`'s key-ring and HMAC-key caches are process-global (by
design - see its module docstring), so any test that flips
FIELD_ENCRYPTION_KEY / HMAC_SEARCH_KEY / KEY_PROVIDER / ENVIRONMENT via
monkeypatch.setenv must reset both those caches AND get_settings()'s
lru_cache before and after, or it leaks altered crypto state into every
other test in the same pytest session (including the `db`/`client`
fixtures used by every other test file, which rely on the
FIELD_ENCRYPTION_KEY conftest.py sets at session start). The
`encryption_state` fixture below does that reset in teardown.
"""
import base64
import os
import sys
import types

import pytest

from app.core import encryption as enc
from app.core.config import Settings, get_settings


@pytest.fixture()
def encryption_state():
    yield
    enc._reset_for_tests()
    get_settings.cache_clear()


def _new_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


# --------------------------------------------------------------------------- #
# Fails closed in production
# --------------------------------------------------------------------------- #

def test_production_without_key_fails_closed(monkeypatch, encryption_state):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "")  # override the non-empty value in backend/.env
    monkeypatch.delenv("KEY_PROVIDER", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()

    with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
        enc._load_keyring()


def test_development_without_key_degrades_to_plaintext_not_a_crash(monkeypatch, encryption_state):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "")  # override the non-empty value in backend/.env
    get_settings.cache_clear()
    enc._reset_for_tests()

    assert enc._load_keyring() is None
    assert enc.encrypt("plain") == "plain"  # unchanged, not encrypted, no exception


def test_production_issues_flags_every_insecure_default():
    s = Settings(
        ENVIRONMENT="production",
        FIELD_ENCRYPTION_KEY="",
        KEY_PROVIDER="env",
        JWT_SECRET="change-me",
        ALLOW_LEGACY_INTEGRATION_KEY=True,
        INTEGRATION_API_KEY="dev-demo-integration-key-2026",
        CORS_ORIGINS="*",
    )
    issues = s.production_issues()
    assert any("FIELD_ENCRYPTION_KEY" in i for i in issues)
    assert any("JWT_SECRET" in i for i in issues)
    assert any("ALLOW_LEGACY_INTEGRATION_KEY" in i for i in issues)
    assert any("INTEGRATION_API_KEY" in i for i in issues)
    assert any("CORS_ORIGINS" in i for i in issues)


def test_production_issues_empty_when_hardened():
    s = Settings(
        ENVIRONMENT="production",
        FIELD_ENCRYPTION_KEY=_new_key(),
        JWT_SECRET="x" * 40,
        ALLOW_LEGACY_INTEGRATION_KEY=False,
        INTEGRATION_API_KEY="a-real-per-tenant-key-value",
        CORS_ORIGINS="https://admin.example.com",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.production_issues() == []


def test_production_issues_empty_outside_production():
    s = Settings(ENVIRONMENT="development", JWT_SECRET="change-me", FIELD_ENCRYPTION_KEY="")
    assert s.production_issues() == []


# --------------------------------------------------------------------------- #
# Key rotation: old ciphertext keeps decrypting, new writes use the new key
# --------------------------------------------------------------------------- #

def test_key_rotation_old_ciphertext_still_decrypts_new_writes_use_new_key(monkeypatch, encryption_state):
    key_a, key_b = _new_key(), _new_key()

    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key_a)
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY_PREVIOUS", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()
    ct_old = enc.encrypt("secret-under-key-a")
    fp_a = enc.current_key_fingerprint()
    assert ct_old.split(".")[0] == fp_a

    # Rotate: key_b becomes current, key_a demoted to "previous" (still
    # decryptable).
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key_b)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY_PREVIOUS", key_a)
    get_settings.cache_clear()
    enc._reset_for_tests()

    assert enc.decrypt(ct_old) == "secret-under-key-a"
    fp_b = enc.current_key_fingerprint()
    assert fp_b != fp_a

    ct_new = enc.encrypt("secret-under-key-b")
    assert ct_new.split(".")[0] == fp_b
    assert enc.decrypt(ct_new) == "secret-under-key-b"


def test_key_rotation_without_previous_key_cannot_decrypt_old_data(monkeypatch, encryption_state):
    """If an operator drops the old key from FIELD_ENCRYPTION_KEY_PREVIOUS
    too early, decrypt fails safe (returns the ciphertext, never raises,
    never returns garbage) rather than silently producing wrong plaintext."""
    key_a, key_b = _new_key(), _new_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key_a)
    get_settings.cache_clear()
    enc._reset_for_tests()
    ct_old = enc.encrypt("only-under-key-a")

    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key_b)
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY_PREVIOUS", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()

    assert enc.decrypt(ct_old) == ct_old  # unreadable, returned as-is, not corrupted


def test_rotate_column_key_reencrypts_live_rows_under_the_current_key(db, monkeypatch, encryption_state):
    from sqlalchemy import text

    from app.core.database import engine

    key_a, key_b = _new_key(), _new_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key_a)
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY_PREVIOUS", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()

    db.execute(text("CREATE TABLE IF NOT EXISTS test_rotate_scratch (id serial primary key, val text)"))
    db.commit()
    try:
        ct_a = enc.encrypt("rotate-me")
        db.execute(text("INSERT INTO test_rotate_scratch (val) VALUES (:v)"), {"v": ct_a})
        db.commit()

        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", key_b)
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_PREVIOUS", key_a)
        get_settings.cache_clear()
        enc._reset_for_tests()

        updated = enc.rotate_column_key(engine, "test_rotate_scratch", "val")
        assert updated == 1

        row = db.execute(text("SELECT val FROM test_rotate_scratch")).fetchone()
        assert row[0].split(".")[0] == enc.current_key_fingerprint()
        assert enc.decrypt(row[0]) == "rotate-me"

        # Idempotent: running it again re-encrypts nothing more.
        assert enc.rotate_column_key(engine, "test_rotate_scratch", "val") == 0
    finally:
        db.execute(text("DROP TABLE IF EXISTS test_rotate_scratch"))
        db.commit()


# --------------------------------------------------------------------------- #
# Separate HMAC search-digest key
# --------------------------------------------------------------------------- #

def test_hmac_search_key_is_independent_of_field_encryption_key(monkeypatch, encryption_state):
    field_key, hmac_key = _new_key(), _new_key()

    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.delenv("HMAC_SEARCH_KEY", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()
    legacy_digest = enc.hmac_digest("someone@example.com")

    monkeypatch.setenv("HMAC_SEARCH_KEY", hmac_key)
    get_settings.cache_clear()
    enc._reset_for_tests()
    separate_digest = enc.hmac_digest("someone@example.com")

    assert legacy_digest != separate_digest
    assert enc.hmac_digest("someone@example.com") == separate_digest  # deterministic

    # Changing FIELD_ENCRYPTION_KEY no longer changes the search digest once
    # HMAC_SEARCH_KEY is set - the two are genuinely independent secrets now.
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", _new_key())
    get_settings.cache_clear()
    enc._reset_for_tests()
    assert enc.hmac_digest("someone@example.com") == separate_digest


# --------------------------------------------------------------------------- #
# KMS-backed key provider (envelope decryption, boto3 mocked)
# --------------------------------------------------------------------------- #

def test_kms_key_provider_fetches_and_uses_the_data_key(monkeypatch, encryption_state):
    real_key = os.urandom(32)
    ciphertext_blob = base64.b64encode(b"opaque-kms-ciphertext-blob")

    class FakeKmsClient:
        def decrypt(self, CiphertextBlob, KeyId):
            assert CiphertextBlob == base64.b64decode(ciphertext_blob)
            assert KeyId == "alias/test-key"
            return {"Plaintext": real_key}

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda service, region_name=None: FakeKmsClient()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    monkeypatch.setenv("KEY_PROVIDER", "kms")
    monkeypatch.setenv("KMS_KEY_ID", "alias/test-key")
    monkeypatch.setenv("KMS_ENCRYPTED_DATA_KEY", ciphertext_blob.decode())
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    enc._reset_for_tests()

    ct = enc.encrypt("hello via kms")
    assert ct != "hello via kms"
    assert enc.decrypt(ct) == "hello via kms"
    assert enc.current_key_fingerprint() == enc._fingerprint(real_key)


def test_kms_provider_without_boto3_installed_gives_actionable_error(monkeypatch, encryption_state):
    monkeypatch.setitem(sys.modules, "boto3", None)  # simulates ImportError on `import boto3`
    monkeypatch.setenv("KEY_PROVIDER", "kms")
    monkeypatch.setenv("KMS_KEY_ID", "alias/test-key")
    monkeypatch.setenv("KMS_ENCRYPTED_DATA_KEY", base64.b64encode(b"x").decode())
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    enc._reset_for_tests()

    # Non-production: falls back to disabled (logged), never crashes the process.
    assert enc._load_keyring() is None


# --------------------------------------------------------------------------- #
# Secrets loaded from a mounted file (Docker/K8s secret convention)
# --------------------------------------------------------------------------- #

def test_secret_can_be_loaded_from_a_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "jwt_secret"
    secret_file.write_text("file-provided-secret-value\n")
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("JWT_SECRET", "should-be-overridden")

    s = Settings()
    assert s.JWT_SECRET == "file-provided-secret-value"


def test_missing_secret_file_path_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_FILE", "/nonexistent/path/does-not-exist")
    monkeypatch.setenv("JWT_SECRET", "inline-value-should-survive")
    s = Settings()
    assert s.JWT_SECRET == "inline-value-should-survive"


# --------------------------------------------------------------------------- #
# `is_encrypted` classification (2-part legacy / 3-part versioned)
# --------------------------------------------------------------------------- #

def test_is_encrypted_recognises_legacy_and_versioned_formats(monkeypatch, encryption_state):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", _new_key())
    get_settings.cache_clear()
    enc._reset_for_tests()

    ct = enc.encrypt("some value long enough to pass the length check")
    assert enc.is_encrypted(ct)
    assert ct.count(".") == 2

    legacy_style = ct.split(".", 1)[1]  # drop the fingerprint -> 2-part shape
    assert enc.is_encrypted(legacy_style)

    assert not enc.is_encrypted("just a plain short string")
    assert not enc.is_encrypted("a.b")  # too short, not valid base64 pairs


# --------------------------------------------------------------------------- #
# CORS methods restricted + security headers on real HTTP responses
# --------------------------------------------------------------------------- #

def test_cors_preflight_does_not_allow_arbitrary_methods(client):
    resp = client.options(
        "/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "TRACE"},
    )
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "TRACE" not in allow_methods
    assert "CONNECT" not in allow_methods


def test_cors_preflight_allows_the_real_methods_this_api_uses(client):
    resp = client.options(
        "/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"},
    )
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "DELETE" in allow_methods


def test_security_headers_present_on_every_response(client):
    resp = client.get("/health")
    assert resp.headers.get("strict-transport-security", "").startswith("max-age=")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp


def test_docs_gets_a_looser_csp_for_swagger_ui(client):
    resp = client.get("/docs")
    csp = resp.headers.get("content-security-policy", "")
    assert "cdn.jsdelivr.net" in csp
    assert "default-src 'none'" not in csp
