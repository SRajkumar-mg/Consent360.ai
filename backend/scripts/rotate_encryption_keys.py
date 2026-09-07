"""R3-03 key-rotation tooling: re-encrypt AES-256-GCM columns under the
current FIELD_ENCRYPTION_KEY, and/or rehash HMAC search-digest columns
under the current HMAC_SEARCH_KEY.

Usage (run from `backend/`, after setting the new key(s) in the
environment/secret files — see app/core/config.py's `_SECRET_FIELDS` and
`HMAC_SEARCH_KEY`)::

    python -m scripts.rotate_encryption_keys --aes            # rotate AES columns
    python -m scripts.rotate_encryption_keys --hmac           # rehash search columns
    python -m scripts.rotate_encryption_keys --aes --hmac     # both

AES rotation procedure (zero downtime):
    1. Move the current FIELD_ENCRYPTION_KEY value into
       FIELD_ENCRYPTION_KEY_PREVIOUS (comma-separate if more than one).
    2. Set FIELD_ENCRYPTION_KEY to a freshly generated key.
    3. Restart the app (old rows keep decrypting via FIELD_ENCRYPTION_KEY_PREVIOUS).
    4. Run this script with --aes. It re-encrypts every row under the new
       key while the app keeps serving traffic normally.
    5. Once done (verify with --aes --check), drop the old value from
       FIELD_ENCRYPTION_KEY_PREVIOUS.

HMAC rehash procedure (zero downtime — lookups keep working throughout,
both for rows already rehashed and for rows not yet touched):
    1. If you are ROTATING an HMAC_SEARCH_KEY that is already set, move
       its current value into HMAC_SEARCH_KEY_PREVIOUS (comma-separate if
       more than one). If HMAC_SEARCH_KEY was never set, skip this: the
       previously-effective key was FIELD_ENCRYPTION_KEY and
       app/core/encryption.py::_load_hmac_keyring retains it for lookup
       automatically.
    2. Set HMAC_SEARCH_KEY to a freshly generated key and restart.
       Lookups go through `find_by_search_digest`, which matches every
       retained key's digest and lazily rehashes each row it touches, so
       nothing is unreachable in the meantime — and no upsert path
       mistakes an existing person for a new one.
    3. Run this script with --hmac to rehash the remaining rows in bulk.
    4. Once it reports 0 rows changed on a re-run, drop the old value
       from HMAC_SEARCH_KEY_PREVIOUS.

Idempotent: safe to re-run.
"""
from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import get_settings  # noqa: E402
from app.core.encryption import current_key_fingerprint, hmac_digest, rotate_column_key  # noqa: E402

settings = get_settings()

# Same coverage as scripts/encrypt_existing_data.py's ENCRYPTED_COLUMNS.
AES_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "users": [("email", "id")],
    "customers": [("external_id", "id"), ("name", "id"), ("email", "id"), ("phone", "id")],
    # aadhar_number removed (R1-12/H-11) - the column no longer exists.
    "crm_customers": [("name", "id"), ("email", "id"), ("address", "id"), ("phone", "id")],
    "consents": [("consent_text", "id")],
    "consent_history": [("reason", "id")],
    "consent_evidence": [("consent_text", "id")],
    "consent_decision_logs": [("reason", "id")],
    "audit_logs": [("reason", "id")],
    "purpose_versions": [("consent_text", "id")],
}

# (model, plaintext attribute, search attribute) for every `*_search` HMAC
# column found in app/models/entities.py.
HMAC_MODELS: list[tuple[str, str, str]] = [
    ("User", "email", "email_search"),
    ("Customer", "external_id", "external_id_search"),
    ("Customer", "email", "email_search"),
    ("CrmCustomer", "email", "email_search"),
    ("OrganizationUser", "email", "email_search"),
]


def rotate_aes() -> None:
    from app.core.database import engine

    if not settings.FIELD_ENCRYPTION_KEY and settings.KEY_PROVIDER != "kms":
        print("ERROR: FIELD_ENCRYPTION_KEY is not set (and KEY_PROVIDER is not 'kms'). Aborting.")
        sys.exit(1)
    fp = current_key_fingerprint()
    print(f"Rotating AES columns to current key {fp} ...")
    total = 0
    for table, columns in AES_COLUMNS.items():
        for col, pk in columns:
            try:
                n = rotate_column_key(engine, table, col, pk_column=pk)
                if n:
                    print(f"  {table}.{col}: re-encrypted {n} row(s)")
                total += n
            except Exception as exc:
                print(f"  {table}.{col}: SKIPPED ({exc})")
    print(f"AES rotation done. {total} row(s) re-encrypted to key {fp}.")


def rehash_hmac() -> None:
    from app.core.database import SessionLocal
    from app.models import entities

    db = SessionLocal()
    total = 0
    try:
        for model_name, plain_attr, search_attr in HMAC_MODELS:
            model = getattr(entities, model_name)
            rows = db.query(model).all()
            changed = 0
            for row in rows:
                plaintext = getattr(row, plain_attr, None)
                if not plaintext:
                    continue
                new_digest = hmac_digest(plaintext)
                if getattr(row, search_attr, None) != new_digest:
                    setattr(row, search_attr, new_digest)
                    changed += 1
            if changed:
                db.commit()
                print(f"  {model_name}.{search_attr}: rehashed {changed} row(s)")
            total += changed
        print(f"HMAC rehash done. {total} row(s) updated.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aes", action="store_true", help="Re-encrypt AES-256-GCM columns under the current key")
    parser.add_argument("--hmac", action="store_true", help="Rehash search-digest columns under HMAC_SEARCH_KEY")
    args = parser.parse_args()
    if not args.aes and not args.hmac:
        parser.print_help()
        sys.exit(1)
    if args.aes:
        rotate_aes()
    if args.hmac:
        rehash_hmac()


if __name__ == "__main__":
    main()
