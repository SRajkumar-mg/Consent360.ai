"""Reset the consent_platform dev database to a clean public schema.

Drops and recreates the public schema (removing all extra/corrupt tables),
then runs alembic upgrade head. This is destructive — dev only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.core.database import engine

if __name__ == "__main__":
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    print("public schema reset. Running alembic upgrade head...")
    os.system(f"cd {os.path.dirname(os.path.abspath(__file__))} && alembic upgrade head")
    print("done")