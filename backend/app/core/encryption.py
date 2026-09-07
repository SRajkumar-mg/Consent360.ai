"""Field-level AES-256-GCM encryption for sensitive database columns.

Provides transparent encrypt-on-write / decrypt-on-read via a SQLAlchemy
TypeDecorator.  Sensitive columns use ``EncryptedString`` / ``EncryptedText``
/ ``EncryptedJSON`` as their column type, and encryption is handled entirely
by SQLAlchemy's type system (no event hooks required).

Key management
--------------
The 256-bit key is read from the ``FIELD_ENCRYPTION_KEY`` environment
variable (base-64 encoded, 44 characters), or fetched from AWS KMS via
envelope decryption when ``KEY_PROVIDER=kms`` (see ``_fetch_kms_key``).
Generate a local key with::

    python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

Layout of a ciphertext blob stored in the database, current format::

    <8-hex-char key fingerprint>.<12-byte nonce base64>.<ciphertext+tag base64>

The fingerprint (``sha256(raw_key)[:8]``) lets ``decrypt`` find the exact
key that produced a given blob even after ``FIELD_ENCRYPTION_KEY`` has been
rotated one or more times since — see ``rotate_column_key`` for the
"rotate without downtime" job. Blobs written before key versioning existed
have the legacy 2-part layout (no fingerprint) and are decrypted by trying
every known key in turn; AES-GCM's authentication tag makes a wrong key
fail loudly rather than returning garbage, so this is safe.

AES-256-GCM is an authenticated encryption mode: the 16-byte GCM tag is
appended automatically by the ``cryptography`` library.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, Text, TypeDecorator

_log = logging.getLogger("consent360.encryption")

_NONCE_BYTES = 12  # 96-bit nonce recommended for GCM
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{8}$")


def _fingerprint(raw_key: bytes) -> str:
    """Stable short identifier for a key, embedded in new ciphertext so a
    future decrypt can find the exact key that encrypted it — derived from
    the key material itself, not a counter, so it stays correct across any
    number of rotations without needing a database column to track "which
    key version wrote this row" (entities.py is out of scope for this
    change; this is what lets key rotation work without one)."""
    return hashlib.sha256(raw_key).hexdigest()[:8]


def _decode_b64_key(b64_key: str, label: str) -> bytes:
    raw = base64.urlsafe_b64decode(b64_key)
    if len(raw) != 32:
        raise ValueError(f"{label} must decode to 32 bytes (AES-256), got {len(raw)}")
    return raw


@dataclass
class _KeyRing:
    current_fp: str
    current_raw: bytes
    current_aes: AESGCM
    by_fp: dict[str, AESGCM]
    legacy_order: list[AESGCM]  # current first, then previous — for old 2-part blobs


_keyring: _KeyRing | None = None
_keyring_loaded = False
_hmac_loaded = False
_legacy_hmac_reuse_warned = False


def _fetch_kms_key() -> bytes:
    """Envelope decryption via AWS KMS: ``KMS_ENCRYPTED_DATA_KEY`` is a small
    ciphertext blob produced once with ``aws kms encrypt --key-id ... --plaintext
    <32 random bytes>``; this calls KMS ``Decrypt`` to recover the 32-byte AES
    data key at process start. The plaintext key is then only ever held in
    memory — never written to disk or logged. ``boto3`` is imported lazily so
    installations using the default ``KEY_PROVIDER=env`` never need it."""
    from app.core.config import get_settings

    s = get_settings()
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised via mocked client in tests
        raise RuntimeError("KEY_PROVIDER=kms requires the 'boto3' package (pip install boto3)") from exc
    client = boto3.client("kms", region_name=s.AWS_REGION or None)
    resp = client.decrypt(CiphertextBlob=base64.b64decode(s.KMS_ENCRYPTED_DATA_KEY), KeyId=s.KMS_KEY_ID)
    plaintext = resp["Plaintext"]
    if len(plaintext) != 32:
        raise RuntimeError(f"KMS data key must be 32 bytes, got {len(plaintext)}")
    return plaintext


def _load_keyring() -> _KeyRing | None:
    """Load (and cache) the active key material. Fails closed: in
    production, a missing/unusable key raises immediately rather than
    silently degrading to plaintext (R3-03/H-01)."""
    global _keyring, _keyring_loaded
    if _keyring_loaded:
        return _keyring
    _keyring_loaded = True
    from app.core.config import get_settings

    s = get_settings()
    current_raw: bytes | None = None
    previous_raw: list[bytes] = [
        _decode_b64_key(k.strip(), "FIELD_ENCRYPTION_KEY_PREVIOUS")
        for k in s.FIELD_ENCRYPTION_KEY_PREVIOUS.split(",")
        if k.strip()
    ]

    if s.KEY_PROVIDER == "kms":
        try:
            current_raw = _fetch_kms_key()
        except Exception:
            _log.exception("Failed to load encryption key from KMS")
            if s.ENVIRONMENT == "production":
                raise
            current_raw = None
    elif s.FIELD_ENCRYPTION_KEY.strip():
        # `.strip()`: a whitespace-only value is not a key. Treating it as
        # one raised binascii.Error out of here on the first encrypt -
        # mid-request - instead of the clear "not set" RuntimeError below.
        # `Settings.secret_config_issues()` refuses to start on it in the
        # first place; this is the second line of defence.
        current_raw = _decode_b64_key(s.FIELD_ENCRYPTION_KEY.strip(), "FIELD_ENCRYPTION_KEY")

    if current_raw is None:
        if s.ENVIRONMENT == "production":
            raise RuntimeError(
                "FIELD_ENCRYPTION_KEY is not set (and KEY_PROVIDER is not 'kms'). "
                "Refusing to store personal data in plaintext in production (R3-03)."
            )
        _log.warning(
            "FIELD_ENCRYPTION_KEY is not set — field-level encryption is disabled "
            "and sensitive columns are stored in plaintext. This is only permitted "
            "outside production."
        )
        _keyring = None
        return None

    current_fp = _fingerprint(current_raw)
    current_aes = AESGCM(current_raw)
    by_fp: dict[str, AESGCM] = {current_fp: current_aes}
    legacy_order: list[AESGCM] = [current_aes]
    for raw in previous_raw:
        fp = _fingerprint(raw)
        by_fp.setdefault(fp, AESGCM(raw))
        legacy_order.append(AESGCM(raw))

    _keyring = _KeyRing(
        current_fp=current_fp, current_raw=current_raw, current_aes=current_aes,
        by_fp=by_fp, legacy_order=legacy_order,
    )
    _log.info(
        "Field-level AES-256-GCM encryption enabled (key %s, %d previous key(s) retained).",
        current_fp, len(previous_raw),
    )
    return _keyring


def _reset_for_tests() -> None:
    """Test-only: forget cached key material so a test can flip
    FIELD_ENCRYPTION_KEY / HMAC_SEARCH_KEY / KEY_PROVIDER mid-process (after
    also calling ``app.core.config.get_settings.cache_clear()``) and see the
    new configuration actually take effect."""
    global _keyring, _keyring_loaded, _hmac_ring, _hmac_loaded, _legacy_hmac_reuse_warned
    _keyring = None
    _keyring_loaded = False
    _hmac_ring = None
    _hmac_loaded = False
    _legacy_hmac_reuse_warned = False


# --------------------------------------------------------------------------- #
#  Core encrypt / decrypt helpers
# --------------------------------------------------------------------------- #

def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string.  Returns ``None``/``""`` unchanged."""
    if plaintext is None or plaintext == "":
        return plaintext
    ring = _load_keyring()
    if ring is None:
        return plaintext
    nonce = os.urandom(_NONCE_BYTES)
    ct = ring.current_aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return (
        ring.current_fp
        + "."
        + base64.urlsafe_b64encode(nonce).decode("ascii")
        + "."
        + base64.urlsafe_b64encode(ct).decode("ascii")
    )


def _try_decrypt(aes: AESGCM, nonce: bytes, ct: bytes) -> str | None:
    try:
        return aes.decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        return None


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a ciphertext string produced by :func:`encrypt` (current
    3-part format) or by the pre-rotation 2-part format."""
    if ciphertext is None or ciphertext == "":
        return ciphertext
    ring = _load_keyring()
    if ring is None:
        return ciphertext
    if "." not in ciphertext:
        return ciphertext  # legacy plaintext — return as-is
    parts = ciphertext.split(".")
    try:
        if len(parts) == 3:
            fp, b64_nonce, b64_ct = parts
            nonce = base64.urlsafe_b64decode(b64_nonce)
            ct = base64.urlsafe_b64decode(b64_ct)
            aes = ring.by_fp.get(fp)
            if aes is not None:
                result = _try_decrypt(aes, nonce, ct)
                if result is not None:
                    return result
            _log.warning("No known encryption key for fingerprint %s", fp)
            return ciphertext
        if len(parts) == 2:
            b64_nonce, b64_ct = parts
            nonce = base64.urlsafe_b64decode(b64_nonce)
            ct = base64.urlsafe_b64decode(b64_ct)
            for aes in ring.legacy_order:
                result = _try_decrypt(aes, nonce, ct)
                if result is not None:
                    return result
            _log.warning("Failed to decrypt legacy-format field with any known key")
            return ciphertext
        return ciphertext
    except Exception:
        _log.warning("Failed to decrypt field — returning raw value")
        return ciphertext


def _strict_b64(part: str) -> bytes | None:
    """Decode `part` only if it is EXACTLY what :func:`base64.urlsafe_b64encode`
    would have produced. ``None`` otherwise.

    ``validate=True`` is the whole point. Python's base64 decoder *discards*
    characters outside the alphabet by default, so ``b64decode("Took a while
    but it was sorted")`` happily returns bytes instead of raising - which is
    how ordinary prose used to be mistaken for ciphertext below.
    """
    if not part or len(part) % 4:
        return None
    try:
        # altchars + validate: the urlsafe alphabet, strictly enforced.
        return base64.b64decode(part.encode("ascii"), altchars=b"-_", validate=True)
    except Exception:
        return None


def is_encrypted(val: Any) -> bool:
    """Is `val` one of our own ciphertext blobs?

    An encrypted value is a '.'-joined 2- or 3-part blob whose last two parts
    are strict base64 - a 12-byte GCM nonce and a ciphertext of at least the
    16-byte GCM tag - and, for the 3-part form, whose first part is an
    8-hex-char key fingerprint.

    **Why the strictness matters, and what it fixes.** This predicate is not
    advisory: `EncryptedString`/`EncryptedText`/`EncryptedJSON` call it on
    every WRITE to avoid double-encrypting a value that is already ciphertext,
    and a `True` here means "store this as-is". The previous implementation
    decoded base64 non-strictly and imposed no length constraints, so a
    perfectly ordinary sentence ending in a full stop - the last segment is
    then empty, and the rest decodes because the decoder silently drops
    spaces and punctuation - was classified as already-encrypted and written
    to the database IN PLAINTEXT. Not a failed encryption anyone would see:
    the read path made the same misjudgement, tried to decrypt, failed, and
    returned the raw value, so the column round-tripped correctly while
    storing personal data in the clear. Every encrypted free-text column was
    exposed to this (`Notification.body`, `Consent.reason`, `Objection.reason`,
    the notice/purpose `consent_text` fields, `CrmCustomer.address`, and
    `Grievance.description`, which is what surfaced it).

    The two constraints added - strict base64 (see :func:`_strict_b64`) and
    the exact 12-byte nonce - are properties every blob :func:`encrypt` has
    ever produced satisfies, so no genuine ciphertext, current or legacy
    2-part, is newly rejected. Rows already stored as plaintext by the old
    behaviour keep reading back correctly (they are now simply returned
    as-is instead of being run through a decrypt that failed) and are
    encrypted properly the next time they are written.
    """
    if not isinstance(val, str) or "." not in val or len(val) <= 20:
        return False
    parts = val.split(".")
    if len(parts) not in (2, 3):
        return False
    if len(parts) == 3 and not _FINGERPRINT_RE.match(parts[0]):
        return False
    nonce = _strict_b64(parts[-2])
    if nonce is None or len(nonce) != _NONCE_BYTES:
        return False
    ciphertext = _strict_b64(parts[-1])
    # AES-GCM appends a 16-byte authentication tag, so even the ciphertext of
    # the empty string is 16 bytes; anything shorter cannot be ours.
    return ciphertext is not None and len(ciphertext) >= 16


def is_encryption_enabled() -> bool:
    return _load_keyring() is not None


def current_key_fingerprint() -> str | None:
    """The active key's fingerprint, or ``None`` if encryption is disabled.
    Used for K-34-style "key age / which key is active" reporting."""
    ring = _load_keyring()
    return ring.current_fp if ring else None


# --------------------------------------------------------------------------- #
#  HMAC search digests for encrypted equality lookups
# --------------------------------------------------------------------------- #

@dataclass
class _HmacKeyRing:
    """The search-digest key material, split by role.

    ``primary`` is the ONLY key ever used to WRITE a new digest.
    ``fallbacks`` are keys a digest already stored in the database may have
    been written under — every one of them is tried on LOOKUP, and a row
    found under one is lazily rewritten to ``primary`` (see
    :func:`find_by_search_digest`).

    Why this exists (the R3-03 review's headline finding): ``*_search``
    digests are the only way an encrypted ``email``/``external_id`` column
    can be matched. Before this key ring, changing the effective HMAC key —
    which is exactly what an operator does when they follow R3-03's own
    guidance and set ``HMAC_SEARCH_KEY`` for the first time, since the key
    silently defaulted to ``FIELD_ENCRYPTION_KEY`` before that — made every
    stored digest unmatchable in one step. That is not merely "lookups
    return nothing": the ordinary upsert paths (`POST /crm/login`,
    `create_context_for_customer`) read a miss as "this is a new person"
    and FABRICATE a duplicate ``CrmCustomer`` + ``Customer`` with all
    profile fields reset, while the real rows become permanently
    unreachable. Retaining the previous keys for lookup removes the window
    entirely, so an HMAC key change needs no downtime and no rehash run
    before it is safe.
    """
    primary: bytes | None
    fallbacks: list[bytes]


_hmac_ring: _HmacKeyRing | None = None


def _collect_previous_keys(raw_csv: str, label: str) -> list[bytes]:
    keys: list[bytes] = []
    for k in (raw_csv or "").split(","):
        k = k.strip()
        if not k:
            continue
        try:
            keys.append(_decode_b64_key(k, label))
        except Exception:
            _log.warning("Ignoring an unusable entry in %s (not a base64 32-byte key)", label)
    return keys


def _load_hmac_keyring() -> _HmacKeyRing:
    """Build (and cache) the search-digest key ring.

    Primary key:
      ``HMAC_SEARCH_KEY`` when set (R3-03: the search-digest key must be a
      separate secret from the AES data key), otherwise the current AES data
      key — the historical behaviour, kept so an install that has never set
      ``HMAC_SEARCH_KEY`` keeps matching its existing digests unchanged.

    Fallback keys, tried on lookup in this order:
      1. ``HMAC_SEARCH_KEY_PREVIOUS`` — an explicit, operator-declared
         rotation of the dedicated key.
      2. The current AES data key and ``FIELD_ENCRYPTION_KEY_PREVIOUS`` —
         the keys digests were written under back when the HMAC key was not
         a separate secret. This entry is what makes "set HMAC_SEARCH_KEY
         for the first time and restart" a safe, no-downtime operation
         WITHOUT the operator having to know to copy the old AES key into
         ``HMAC_SEARCH_KEY_PREVIOUS`` first. Nothing here is ever used to
         write a digest, so the key-reuse anti-pattern H-01/T-07 objects to
         is still gone from the write path the moment HMAC_SEARCH_KEY is set.
    """
    global _hmac_ring, _hmac_loaded, _legacy_hmac_reuse_warned
    if _hmac_loaded and _hmac_ring is not None:
        return _hmac_ring
    _hmac_loaded = True
    from app.core.config import get_settings

    s = get_settings()
    aes_ring = _load_keyring()
    aes_keys: list[bytes] = []
    if aes_ring is not None:
        aes_keys.append(aes_ring.current_raw)
    aes_keys.extend(_collect_previous_keys(s.FIELD_ENCRYPTION_KEY_PREVIOUS, "FIELD_ENCRYPTION_KEY_PREVIOUS"))

    primary: bytes | None
    if s.HMAC_SEARCH_KEY.strip():
        primary = _decode_b64_key(s.HMAC_SEARCH_KEY.strip(), "HMAC_SEARCH_KEY")
    elif aes_keys:
        if not _legacy_hmac_reuse_warned:
            _legacy_hmac_reuse_warned = True
            _log.warning(
                "HMAC_SEARCH_KEY is not set — search digests are keyed by "
                "FIELD_ENCRYPTION_KEY (R3-03 wants these separate). Set "
                "HMAC_SEARCH_KEY and run `python -m scripts.rotate_encryption_keys --hmac`; "
                "lookups keep working throughout (the old key is retained "
                "for lookup automatically), so no downtime is needed."
            )
        primary = aes_keys[0]
    else:
        primary = None

    fallbacks: list[bytes] = []
    seen: set[bytes] = {primary} if primary is not None else set()
    for candidate in _collect_previous_keys(s.HMAC_SEARCH_KEY_PREVIOUS, "HMAC_SEARCH_KEY_PREVIOUS") + aes_keys:
        if candidate in seen:
            continue
        seen.add(candidate)
        fallbacks.append(candidate)

    _hmac_ring = _HmacKeyRing(primary=primary, fallbacks=fallbacks)
    if fallbacks:
        _log.info("Search-digest key ring loaded: 1 primary key, %d retained lookup key(s).", len(fallbacks))
    return _hmac_ring


def _resolve_hmac_key() -> bytes | None:
    """The single key used to WRITE new search digests (and to key
    :func:`hmac_signature`). See :func:`_load_hmac_keyring` for the lookup
    fallbacks that are deliberately NOT part of the write path."""
    return _load_hmac_keyring().primary


def _digest_with(key: bytes | None, plaintext: str) -> str:
    normalized = plaintext.lower().strip()
    if key is None:
        return normalized
    return _hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_digest(plaintext: str | None) -> str | None:
    """Compute HMAC-SHA256 digest of a plaintext value for equality lookups.

    The digest is deterministic (same input → same output), unlike AES-GCM
    ciphertext.  Store this alongside the encrypted column so that
    ``WHERE digest = hmac_digest(:value)`` works for equality queries.

    Always keyed by the key ring's PRIMARY key — this is the write path.
    Read paths must go through :func:`search_digests` /
    :func:`find_by_search_digest` instead, so a key change cannot turn an
    existing row into a "miss" (and therefore into a fabricated duplicate).
    """
    if plaintext is None or plaintext == "":
        return plaintext
    return _digest_with(_load_hmac_keyring().primary, plaintext)


def search_digests(plaintext: str | None) -> list[str]:
    """Every digest `plaintext` could currently be stored under: the primary
    first, then one per retained fallback key, de-duplicated and in priority
    order. A lookup should match against ALL of them."""
    if plaintext is None or plaintext == "":
        return []
    ring = _load_hmac_keyring()
    digests: list[str] = []
    for key in [ring.primary, *ring.fallbacks]:
        d = _digest_with(key, plaintext)
        if d and d not in digests:
            digests.append(d)
    return digests


# Kept under its original R3-03 name; `search_digests` is the same thing
# with a name that says what it is for.
hmac_digest_candidates = search_digests


def search_filter(column, plaintext: str | None):
    """SQLAlchemy criterion matching `column` against every digest
    `plaintext` could be stored under. Drop-in replacement for
    ``column == hmac_digest(value)`` at any lookup site."""
    digests = search_digests(plaintext)
    if not digests:
        return column.is_(None) if plaintext in (None, "") else column == plaintext
    if len(digests) == 1:
        return column == digests[0]
    return column.in_(digests)


def find_by_search_digest(query, column, plaintext: str | None):
    """Run `query` filtered on `column` matching `plaintext` under any
    currently-valid search digest, and return the first row.

    On a hit found under a FALLBACK key, the row's digest is rewritten to
    the primary key's digest in place (lazy migration): the change joins
    the caller's own unit of work, so it is persisted by whatever commit
    the caller already makes and simply retried on the next lookup if the
    caller never commits. Together with the retained fallback keys this is
    what makes an HMAC key rotation a no-downtime operation — the bulk
    `python -m scripts.rotate_encryption_keys --hmac` run becomes an
    optimisation (so the fallback keys can eventually be dropped), not a
    prerequisite for correctness.
    """
    digests = search_digests(plaintext)
    if not digests:
        return None
    row = query.filter(column.in_(digests) if len(digests) > 1 else column == digests[0]).first()
    if row is None:
        return None
    attr = getattr(column, "key", None)
    if attr and getattr(row, attr, None) != digests[0]:
        try:
            setattr(row, attr, digests[0])
        except Exception:  # pragma: no cover - a read-only/detached row must never break a lookup
            _log.debug("Could not lazily rehash %s on a %s row", attr, type(row).__name__)
    return row


def _signing_key() -> bytes:
    """Key material for :func:`hmac_signature`. Prefers ``HMAC_SEARCH_KEY``,
    then the current AES data key (the same 256-bit secret already used for
    field encryption), then ``JWT_SECRET`` — so a signature is always keyed
    by *some* server secret rather than silently degrading to an unkeyed
    (and therefore forgeable-by-anyone-with-DB-access) hash.
    """
    from app.core.config import get_settings

    key = _resolve_hmac_key()
    if key is not None:
        return key
    return get_settings().JWT_SECRET.encode("utf-8")


def hmac_signature(*parts: str | None) -> str:
    """HMAC-SHA256 signature over one or more string parts (joined by a
    separator that cannot occur inside a hex digest), keyed by
    :func:`_signing_key`.

    Used to sign a :class:`~app.models.entities.ConsentEvidence` row's
    ``content_hash`` and ``notice_hash`` (see
    ``app.services.consent._create_evidence``) so that recomputing this
    value later and comparing it to the stored ``signature`` detects any
    tampering with either hash - e.g. a direct DB edit that bypasses the
    ORM and the consent lifecycle entirely.
    """
    message = "|".join(p or "" for p in parts).encode("utf-8")
    return _hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def hmac_signature_matches(signature: str | None, *parts: str | None) -> bool:
    """Constant-time check of a stored :func:`hmac_signature` against a
    freshly-recomputed one, accepting a signature produced under ANY key in
    the search-digest key ring rather than only the current primary.

    Same rotation problem as :func:`find_by_search_digest`, different
    consequence: a signature is written once and verified much later, so
    after an HMAC key change every pre-rotation ``ConsentReceipt`` /
    ``ConsentEvidence`` row would otherwise report itself as TAMPERED - a
    false integrity alarm on exactly the compliance evidence a DPDP audit
    relies on.

    Wired into every signature-verification call site:
    ``app/services/receipts.py::verify_receipt``,
    ``app/services/consent_manager.py`` (artefact event signatures) and
    ``app/api/routes/sharing_events.py``. Any new verification path must use
    this rather than comparing against ``hmac_signature`` directly, or it
    reintroduces the false-TAMPERED alarm described above the moment a key
    is rotated.
    """
    if not signature:
        return False
    ring = _load_hmac_keyring()
    message = "|".join(p or "" for p in parts).encode("utf-8")
    keys: list[bytes] = [k for k in [ring.primary, *ring.fallbacks] if k is not None]
    if not keys:
        from app.core.config import get_settings

        keys = [get_settings().JWT_SECRET.encode("utf-8")]
    matched = False
    for key in keys:
        # No early exit: every key is tried so the work done does not depend
        # on which one (if any) matched.
        if _hmac.compare_digest(_hmac.new(key, message, hashlib.sha256).hexdigest(), signature):
            matched = True
    return matched


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
#  Bulk encryption / rotation migration helpers
# --------------------------------------------------------------------------- #

def encrypt_column_in_table(
    engine,
    table_name: str,
    column_name: str,
    pk_column: str = "id",
    batch_size: int = 500,
) -> int:
    """Encrypt all plaintext values in a single column.

    Rows that are already encrypted (under any known key) are skipped.
    Returns the number of rows updated.
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


def rotate_column_key(
    engine,
    table_name: str,
    column_name: str,
    pk_column: str = "id",
    batch_size: int = 500,
) -> int:
    """Re-encrypt every non-null row in a column under the CURRENT key,
    regardless of which key (or none) it is presently under.

    Safe to run against a live database while the app keeps serving traffic:
    each row is re-encrypted independently in its own small transaction, and
    every row not yet processed keeps decrypting fine under whatever key it
    is already in (as long as that key is still listed in
    ``FIELD_ENCRYPTION_KEY_PREVIOUS``) — this is what "keys rotate without
    downtime" (R3-03) means in practice. Run once after moving the old
    ``FIELD_ENCRYPTION_KEY`` value into ``FIELD_ENCRYPTION_KEY_PREVIOUS`` and
    setting a new ``FIELD_ENCRYPTION_KEY``; once every row's fingerprint
    matches the new key (verifiable via ``current_key_fingerprint()``), the
    old value can be dropped from ``FIELD_ENCRYPTION_KEY_PREVIOUS``.
    """
    from sqlalchemy import text

    ring = _load_keyring()
    if ring is None:
        raise RuntimeError("Cannot rotate: no active encryption key configured")
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
                pk_val, raw = row[0], row[1]
                if not raw:
                    continue
                already_current = (
                    is_encrypted(raw) and raw.count(".") == 2 and raw.split(".", 1)[0] == ring.current_fp
                )
                if already_current:
                    continue
                plaintext = decrypt(raw) if is_encrypted(raw) else raw
                new_value = encrypt(plaintext)
                conn.execute(
                    text(f"UPDATE {table_name} SET {column_name} = :val WHERE {pk_column} = :pk"),
                    {"val": new_value, "pk": pk_val},
                )
                updated += 1
            offset += batch_size
    return updated
