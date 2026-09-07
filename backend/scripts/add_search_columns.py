"""Add _search HMAC digest columns and backfill from encrypted data."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.core.database import engine
from app.core.encryption import decrypt, hmac_digest


def migrate():
    with engine.begin() as conn:
        # --- customers ---
        conn.execute(text("""
            ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS external_id_search VARCHAR(64)
        """))
        conn.execute(text("""
            ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS email_search VARCHAR(64)
        """))
        # --- crm_customers ---
        conn.execute(text("""
            ALTER TABLE crm_customers
            ADD COLUMN IF NOT EXISTS email_search VARCHAR(64)
        """))
        # --- users ---
        conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS email_search VARCHAR(64)
        """))

        # Backfill customers
        rows = conn.execute(text("SELECT id, external_id, email FROM customers")).fetchall()
        for r in rows:
            ext_plain = decrypt(r[1])
            email_plain = decrypt(r[2])
            conn.execute(
                text("UPDATE customers SET external_id_search = :s, email_search = :e WHERE id = :id"),
                {"s": hmac_digest(ext_plain), "e": hmac_digest(email_plain), "id": r[0]},
            )
        print(f"Backfilled {len(rows)} customers")

        # Backfill crm_customers
        rows = conn.execute(text("SELECT id, email FROM crm_customers")).fetchall()
        for r in rows:
            email_plain = decrypt(r[1])
            conn.execute(
                text("UPDATE crm_customers SET email_search = :e WHERE id = :id"),
                {"e": hmac_digest(email_plain), "id": r[0]},
            )
        print(f"Backfilled {len(rows)} crm_customers")

        # Backfill users
        rows = conn.execute(text("SELECT id, email FROM users")).fetchall()
        for r in rows:
            email_plain = decrypt(r[1])
            conn.execute(
                text("UPDATE users SET email_search = :e WHERE id = :id"),
                {"e": hmac_digest(email_plain), "id": r[0]},
            )
        print(f"Backfilled {len(rows)} users")

        print("Migration complete!")


if __name__ == "__main__":
    migrate()
