"""Field-level AES-256-GCM encryption for sensitive database columns.

Provides transparent encrypt-on-write / decrypt-on-read via a SQLAlchemy
TypeDecorator.  Sensitive columns use ``EncryptedString`` / ``EncryptedText``
/ ``EncryptedJSON`` as their column type, and encryption is handled entirely
by SQLAlchemy's type system (no event hooks required).

Key management
--------------
The 256-bit key is read from the ``FIELD_ENCRYPTION_KEY`` environment variable
(base-64 encoded, 44 characters).  Generate one with::

    python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

Layout of a ciphertext blob stored in the database::

    <12-byte nonce base64>.<ciphertext+tag base64>

AES-256-GCM is an authenticated encryption mode: the 16-byte GCM tag is
appended automatically by the ``cryptography`` library.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

_log = logging.getLogger("consent360.encryption")

_NONCE_BYTES = 12  # 96-bit nonce recommended for GCM

# Module-level singleton; initialised lazily on first use.
_aesgcm: AESGCM | None = None
_key_loaded = False


def _load_key() -> AESGCM | None:
    """Load the AES-256 key from settings (cached)."""
    global _aesgcm, _key_loaded
    if _key_loaded:
        return _aesgcm
    _key_loaded = True
    from app.core.config import get_settings

    b64_key = get_settings().FIELD_ENCRYPTION_KEY
    if not b64_key:
        _log.warning(
            "FIELD_ENCRYPTION_KEY is not set — field-level encryption is disabled "
            "and sensitive columns are stored in plaintext."
        )
        return None
    raw = base64.urlsafe_b64decode(b64_key)
    if len(raw) != 32:
        raise ValueError(
            f"FIELD_ENCRYPTION_KEY must decode to 32 bytes (AES-256), got {len(raw)}"
        )
    _aesgcm = AESGCM(raw)
    _log.info("Field-level AES-256-GCM encryption enabled.")
    return _aesgcm


# --------------------------------------------------------------------------- #
#  Core encrypt / decrypt helpers
# --------------------------------------------------------------------------- #

def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string.  Returns ``None`` for ``None`` input."""
    if plaintext is None or plaintext == "":
        return plaintext
    aes = _load_key()
    if aes is None:
        return plaintext
    import os
    nonce = os.urandom(_NONCE_BYTES)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return (
        base64.urlsafe_b64encode(nonce).decode("ascii")
        + "."
        + base64.urlsafe_b64encode(ct).decode("ascii")
    )


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a ciphertext string produced by :func:`encrypt`."""
    if ciphertext is None or ciphertext == "":
        return ciphertext
    aes = _load_key()
    if aes is None:
        return ciphertext
    if "." not in ciphertext:
        return ciphertext  # legacy plaintext — return as-is
    try:
        b64_nonce, b64_ct = ciphertext.split(".", 1)
        nonce = base64.urlsafe_b64decode(b64_nonce)
        ct = base64.urlsafe_b64decode(b64_ct)
        return aes.decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        _log.warning("Failed to decrypt field — returning raw value")
        return ciphertext


def is_encrypted(val: Any) -> bool:
    """Heuristic: encrypted values contain a '.' separating nonce and ciphertext."""
    if isinstance(val, str) and "." in val and len(val) > 20:
        try:
            parts = val.split(".", 1)
            base64.urlsafe_b64decode(parts[0])
            base64.urlsafe_b64decode(parts[1])
            return True
        except Exception:
            return False
    return False


def is_encryption_enabled() -> bool:
    return _load_key() is not None


# --------------------------------------------------------------------------- #
#  HMAC search digests for encrypted equality lookups
# --------------------------------------------------------------------------- #

def hmac_digest(plaintext: str | None) -> str | None:
    """Compute HMAC-SHA256 digest of a plaintext value for equality lookups.

    The digest is deterministic (same input → same output), unlike AES-GCM
    ciphertext.  Store this alongside the encrypted column so that
    ``WHERE digest = hmac_digest(:value)`` works for equality queries.
    """
    if plaintext is None or plaintext == "":
        return plaintext
    import hashlib
    import hmac as _hmac
    from app.core.config import get_settings
    aes = _load_key()
    if aes is None:
        return plaintext.lower().strip()
    raw_key = base64.urlsafe_b64decode(get_settings().FIELD_ENCRYPTION_KEY)
    normalized = plaintext.lower().strip().encode("utf-8")
    return _hmac.new(raw_key, normalized, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
#  SQLAlchemy TypeDecorators — transparent encryption at the type level
# --------------------------------------------------------------------------- #

class EncryptedString(TypeDecorator):
    """String column with automatic AES-256-GCM encryption."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        if is_encrypted(value):
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        if not is_encrypted(value):
            return value
        return decrypt(value)


class EncryptedText(TypeDecorator):
    """Text column with automatic AES-256-GCM encryption."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        if is_encrypted(value):
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        if not is_encrypted(value):
            return value
        return decrypt(value)


class EncryptedJSON(TypeDecorator):
    """JSON column with automatic AES-256-GCM encryption.

    The JSON value is serialised to a string, encrypted, and stored as text.
    On read, it is decrypted and parsed back to a dict/list.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # Normalise empty containers so NOT NULL columns never get NULL
        if value == {} or value == []:
            value = {}
        # If it's already an encrypted string (from a re-entrant write), pass through
        if isinstance(value, str) and is_encrypted(value):
            return value
        serialised = json.dumps(value, default=str)
        return encrypt(serialised)

    def process_result_value(self, value, dialect):
        if value is None:
            return {}
        if not is_encrypted(value):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}
        decrypted = decrypt(value)
        if decrypted is None:
            return {}
        try:
            return json.loads(decrypted)
        except (json.JSONDecodeError, TypeError):
            return {}


# --------------------------------------------------------------------------- #
#  Bulk encryption migration helper
# --------------------------------------------------------------------------- #

def encrypt_column_in_table(
    engine,
    table_name: str,
    column_name: str,
    pk_column: str = "id",
    batch_size: int = 500,
) -> int:
    """Encrypt all plaintext values in a single column.

    Rows that are already encrypted are skipped.  Returns the number of
    rows updated.
    """
    from sqlalchemy import text

    updated = 0
    with engine.begin() as conn:
        offset = 0
        while True:
            rows = conn.execute(
                text(f"SELECT {pk_column}, {column_name} FROM {table_name} "
                     f"WHERE {column_name} IS NOT NULL ORDER BY {pk_column} "
                     f"LIMIT :limit OFFSET :offset"),
                {"limit": batch_size, "offset": offset},
            ).fetchall()
            if not rows:
                break
            for row in rows:
                pk_val = row[0]
                raw = row[1]
                if raw and not is_encrypted(raw):
                    encrypted_val = encrypt(raw)
                    conn.execute(
                        text(f"UPDATE {table_name} SET {column_name} = :val WHERE {pk_column} = :pk"),
                        {"val": encrypted_val, "pk": pk_val},
                    )
                    updated += 1
            offset += batch_size
    return updated
