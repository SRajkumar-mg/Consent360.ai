"""Encrypt every sensitive column value still stored in the clear.

Run it after setting ``FIELD_ENCRYPTION_KEY``, and again after any incident in
which plaintext may have reached an encrypted column::

    cd backend
    python -m scripts.encrypt_existing_data            # dry run: reports only
    python -m scripts.encrypt_existing_data --apply    # writes

Dry run is the default. ``--apply`` is the only thing that writes.

Why this exists (and why it is written this way)
------------------------------------------------
``EncryptedString`` / ``EncryptedText`` / ``EncryptedJSON`` skip encryption for
a value that is already one of our ciphertext blobs, and they ask
``app.core.encryption.is_encrypted`` whether it is. That predicate used to
decode base64 non-strictly; Python's decoder silently *discards* characters
outside the alphabet instead of raising, so an ordinary sentence ending in a
full stop satisfied it and was written to the database in the clear. Nothing
surfaced it, because the read path made the identical misjudgement: it tried to
decrypt, failed, and returned the raw value, so the column round-tripped
perfectly while holding personal data in plaintext.

Two consequences shape this script:

1. **The detection must be the fixed predicate.** A remediation tool that asks
   the broken question skips precisely the rows it exists to repair — on the
   shared dev database, 6312 of the 6313 plaintext values were classified as
   "already encrypted" by the old predicate. The work is done by
   ``encrypt_column_in_table``, which reads the *raw stored* value with plain
   SQL (bypassing the type decorator) and tests it with the current
   ``is_encrypted``. ``--self-check`` prints the provenance of that predicate.

2. **The column list must not be hand-maintained.** The map this script used to
   carry listed 15 columns when the models declared 41, and it omitted
   ``notifications.body`` — one of the affected ones. A hand-maintained list of
   things-to-encrypt silently omits the next new column, which is this exact
   failure mode repeating. The list is therefore derived from
   ``Base.metadata`` by inspecting each column's type, so a new encrypted
   column is covered the moment it is declared.

``audit_logs`` is append-only: a database trigger blocks UPDATE for every role
(see ``app/core/audit_chain.py`` and ``scripts/harden_audit_role.sql``). It is
reported but never written. A plaintext value there is an incident to be
appended to, not a row to mutate — re-read it, and if it carries personal data,
escalate rather than reaching for the trigger.

The script is idempotent; running it twice is safe.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the backend package is importable when run as a plain file.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import inspect as sa_inspect, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.encryption import (  # noqa: E402
    EncryptedJSON,
    EncryptedString,
    EncryptedText,
    decrypt,
    encrypt_column_in_table,
    is_encrypted,
    is_encryption_enabled,
)

# Importing the models is what populates Base.metadata — without it the
# derivation below finds nothing.
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.models import entities  # noqa: F401,E402

ENCRYPTED_TYPES = (EncryptedString, EncryptedText, EncryptedJSON)

# Tables whose rows cannot be rewritten. Reported, never written.
APPEND_ONLY_TABLES = {"audit_logs"}


# --------------------------------------------------------------------------- #
#  Column discovery — derived from the models, never hand-maintained
# --------------------------------------------------------------------------- #

def discover_encrypted_columns() -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Every ``EncryptedString`` / ``EncryptedText`` / ``EncryptedJSON`` column
    declared in the models.

    Returns ``(present, absent)`` where each present entry is
    ``(table, column, pk_column)`` and each absent entry is a
    ``(table, column)`` whose table this database does not have (a schema
    behind head, say). Absent columns are reported rather than swallowed, so a
    missing table is never mistaken for a clean one.
    """
    live_tables = set(sa_inspect(engine).get_table_names())
    present: list[tuple[str, str, str]] = []
    absent: list[tuple[str, str]] = []
    for table in Base.metadata.sorted_tables:
        encrypted = [c for c in table.columns if isinstance(c.type, ENCRYPTED_TYPES)]
        if not encrypted:
            continue
        if table.name not in live_tables:
            absent.extend((table.name, c.name) for c in encrypted)
            continue
        pk_cols = list(table.primary_key.columns)
        if len(pk_cols) != 1:
            # encrypt_column_in_table addresses rows by a single pk column.
            print(f"  ! {table.name}: composite/absent primary key — skipped")
            absent.extend((table.name, c.name) for c in encrypted)
            continue
        pk = pk_cols[0].name
        present.extend((table.name, c.name, pk) for c in encrypted)
    return present, absent


def plaintext_residue(db, table: str, column: str) -> tuple[int, int]:
    """``(non-null values, values still in the clear)`` for one column.

    Reads the raw stored text with plain SQL so the type decorator cannot
    decrypt it on the way past, and judges it with the *fixed* predicate.
    Never returns or logs a value.
    """
    total = plain = 0
    for (value,) in db.execute(
        text(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL')
    ):
        if value == "":
            continue
        total += 1
        if not is_encrypted(value):
            plain += 1
    return total, plain


def decryptable(db, table: str, column: str, limit: int = 200) -> tuple[int, int]:
    """Sample stored values and count how many decrypt cleanly.

    ``decrypt`` returns its input unchanged when it cannot make sense of a
    blob, so "came back different from what is stored" is a sound readability
    check that never needs the plaintext itself.
    """
    rows = db.execute(
        text(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL '
             f"LIMIT :limit"),
        {"limit": limit},
    ).fetchall()
    sampled = readable = 0
    for (value,) in rows:
        if not value:
            continue
        sampled += 1
        if is_encrypted(value) and decrypt(value) != value:
            readable += 1
    return sampled, readable


# --------------------------------------------------------------------------- #
#  Self-check — prove the detection is not the broken heuristic
# --------------------------------------------------------------------------- #

def _pre_fix_is_encrypted(val) -> bool:
    """The predicate as it stood before the fix, reconstructed verbatim.

    Kept only so ``--self-check`` can measure how far the two disagree on real
    stored data. Nothing else may call it.
    """
    import base64
    import re

    if not isinstance(val, str) or "." not in val or len(val) <= 20:
        return False
    parts = val.split(".")
    if len(parts) not in (2, 3):
        return False
    if len(parts) == 3 and not re.match(r"^[0-9a-f]{8}$", parts[0]):
        return False
    try:
        for p in parts[-2:]:
            base64.urlsafe_b64decode(p)  # non-strict: silently discards junk
        return True
    except Exception:
        return False


def self_check(db, present: list[tuple[str, str, str]]) -> None:
    """Show where this script's notion of "already encrypted" comes from, and
    measure it against the broken one on the data actually in this database.

    A remediation tool inherits its blind spots from its predicate. If this
    script asked the old question it would classify the very rows it exists to
    repair as "already encrypted" and skip them, reporting a clean run over an
    untouched database. Printing counts only — never a value.
    """
    import hashlib
    import inspect

    from app.core.encryption import encrypt

    print("Self-check: provenance of the already-encrypted predicate")
    print("-" * 66)
    resolved = encrypt_column_in_table.__globals__["is_encrypted"]
    print(f"  encrypt_column_in_table asks : "
          f"{resolved.__module__}.{resolved.__qualname__}")
    print(f"  same object as is_encrypted  : {resolved is is_encrypted}")
    src = inspect.getsource(is_encrypted)
    print(f"  decodes base64 strictly      : {'_strict_b64' in src}")
    print(f"  enforces the 12-byte nonce   : {'_NONCE_BYTES' in src}")
    print(f"  sha256(source)[:16]          : "
          f"{hashlib.sha256(src.encode()).hexdigest()[:16]}")

    # The sentence from the encryption.py docstring: one clause, one full stop,
    # and a letter count that happens to be a multiple of four, so the old
    # non-strict decoder accepted it as base64.
    probe = "Took a while but it was sorted."
    print(f"  on the canonical bug trigger : old=True fixed="
          f"{is_encrypted(probe)}  (fixed must be False)")
    print(f"  on a genuine ciphertext blob : fixed="
          f"{is_encrypted(encrypt(probe))}  (must be True)")

    # The decisive measurement: how the two predicates rate the stored values.
    plain_fixed = old_would_skip = fixed_would_reject = 0
    for table, column, _pk in present:
        for (value,) in db.execute(text(
                f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL')):
            if not value:
                continue
            new, old = is_encrypted(value), _pre_fix_is_encrypted(value)
            if not new:
                plain_fixed += 1
                if old:
                    old_would_skip += 1
            elif not old:
                fixed_would_reject += 1
    print("  on this database's stored values:")
    print(f"    in the clear per the fixed predicate       : {plain_fixed}")
    print(f"    of those, the old predicate called encrypted: {old_would_skip}"
          "   <- would have been skipped")
    print(f"    genuine ciphertext the old predicate missed : {fixed_would_reject}"
          "   <- must be 0 (no double-encryption)")
    print()


# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Encrypt sensitive column values still stored in the clear.")
    parser.add_argument("--apply", action="store_true",
                        help="write; without it the script only reports")
    parser.add_argument("--self-check", action="store_true",
                        help="report where the already-encrypted predicate comes "
                             "from, and how far it disagrees with the pre-fix one "
                             "on this database's stored values")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.FIELD_ENCRYPTION_KEY or not is_encryption_enabled():
        print("ERROR: FIELD_ENCRYPTION_KEY is not set or not usable. Aborting.")
        return 1

    db_label = str(engine.url).rsplit("/", 1)[-1]
    print(f"database          : {db_label}")
    print("encryption        : enabled")
    print(f"mode              : {'APPLY (writes)' if args.apply else 'dry run (no writes)'}")
    print()

    present, absent = discover_encrypted_columns()
    tables = len({t for t, _, _ in present})
    print(f"encrypted columns : {len(present)} across {tables} tables, "
          f"derived from Base.metadata")
    if absent:
        absent_tables = sorted({t for t, _ in absent})
        print(f"                    {len(absent)} more declared on tables this "
              f"database does not have: {', '.join(absent_tables)}")
    print()

    db = SessionLocal()
    try:
        if args.self_check:
            self_check(db, present)

        print(f"{'table.column':<44} {'values':>8} {'plaintext':>10}")
        print("-" * 66)
        before: dict[tuple[str, str], tuple[int, int]] = {}
        for table, column, _pk in present:
            total, plain = plaintext_residue(db, table, column)
            before[(table, column)] = (total, plain)
            if plain:
                note = "  <- append-only, reported not written" \
                    if table in APPEND_ONLY_TABLES else ""
                print(f"{table + '.' + column:<44} {total:>8} {plain:>10}{note}")
        total_values = sum(t for t, _ in before.values())
        total_plain = sum(p for _, p in before.values())
        frozen = sum(p for (t, _), (_, p) in before.items() if t in APPEND_ONLY_TABLES)
        print("-" * 66)
        print(f"{'TOTAL':<44} {total_values:>8} {total_plain:>10}")
        print(f"{'  of which in append-only tables':<44} {'':>8} {frozen:>10}")
        print(f"{'  repairable by this script':<44} {'':>8} "
              f"{total_plain - frozen:>10}")
        print()

        if not args.apply:
            if total_plain - frozen:
                print("Dry run. Re-run with --apply to encrypt the "
                      f"{total_plain - frozen} repairable value(s).")
            else:
                print("Nothing to do.")
            return 0

        if total_plain - frozen == 0:
            print("Nothing to write.")
            return 0

        print("Encrypting (already-encrypted values are skipped row by row)...")
        written = 0
        for table, column, pk in present:
            if table in APPEND_ONLY_TABLES or not before[(table, column)][1]:
                continue
            n = encrypt_column_in_table(engine, table, column, pk_column=pk)
            written += n
            print(f"  {table}.{column}: encrypted {n} value(s)")
        print(f"\nrows written      : {written}")
        print()

        print("Verifying...")
        remaining = 0
        for table, column, _pk in present:
            _total, plain = plaintext_residue(db, table, column)
            if plain:
                remaining += plain
                print(f"  plaintext remaining {table}.{column}: {plain}"
                      + ("  (append-only)" if table in APPEND_ONLY_TABLES else
                         "  <- UNEXPECTED"))
        print(f"  plaintext values remaining : {remaining} "
              f"(expected {frozen}, all in append-only tables)")

        for (table, column), (_t, plain) in before.items():
            if plain and table not in APPEND_ONLY_TABLES:
                sampled, readable = decryptable(db, table, column)
                print(f"  {table}.{column}: {readable}/{sampled} sampled values "
                      f"decrypt cleanly")

        from app.core.audit_chain import verify_chain

        chain = verify_chain(db)
        print(f"  audit chain: checked={chain['checked']} "
              f"tenants={chain['tenants']} broken={chain['broken']}")

        ok = remaining == frozen and not chain["broken"]
        print(f"\n{'Done.' if ok else 'Done WITH PROBLEMS — read the lines above.'}")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
