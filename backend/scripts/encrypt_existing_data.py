"""One-time migration: encrypt all existing plaintext sensitive columns.

Run AFTER setting FIELD_ENCRYPTION_KEY in .env::

    cd backend
    .venv/Scripts/python.exe -m scripts.encrypt_existing_data

This script reads every sensitive column in plaintext and re-writes it
encrypted.  Rows that are already encrypted (contain a dot-separated
base64 payload) are skipped automatically.

The script is idempotent — running it multiple times is safe.
"""

from __future__ import annotations

import sys
import os

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import get_settings
from app.core.encryption import encrypt_column_in_table, _is_already_encrypted

settings = get_settings()

if not settings.FIELD_ENCRYPTION_KEY:
    print("ERROR: FIELD_ENCRYPTION_KEY is not set in .env.  Aborting.")
    sys.exit(1)

from app.core.database import engine

# ---- Table → (column, pk_column) mapping of all encrypted columns -------- #

ENCRYPTED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("email", "id"),
    ],
    "customers": [
        ("external_id", "id"),
        ("name", "id"),
        ("email", "id"),
        ("phone", "id"),
    ],
    "crm_customers": [
        ("name", "id"),
        ("email", "id"),
        ("aadhar_number", "id"),
        ("address", "id"),
        ("phone", "id"),
    ],
    "consents": [
        ("consent_text", "id"),
    ],
    "consent_history": [
        ("reason", "id"),
    ],
    "consent_evidence": [
        ("consent_text", "id"),
    ],
    "consent_decision_logs": [
        ("reason", "id"),
    ],
    "audit_logs": [
        ("reason", "id"),
    ],
    "purpose_versions": [
        ("consent_text", "id"),
    ],
}


def main() -> None:
    total = 0
    for table, columns in ENCRYPTED_COLUMNS.items():
        for col, pk in columns:
            try:
                n = encrypt_column_in_table(engine, table, col, pk_column=pk)
                if n:
                    print(f"  {table}.{col}: encrypted {n} rows")
                total += n
            except Exception as exc:
                print(f"  {table}.{col}: SKIPPED ({exc})")
    print(f"\nDone. {total} total rows encrypted.")


if __name__ == "__main__":
    main()
