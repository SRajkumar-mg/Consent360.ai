from sqlalchemy import create_engine, text
from app.core.config import get_settings
s = get_settings()
engine = create_engine(s.DATABASE_URL)
with engine.connect() as conn:
    r = conn.execute(text("SELECT id, data_items, created_at, status FROM notices"))
    for row in r:
        print(f"id={row[0]} data_items_type={type(row[1]).__name__} data_items={row[1]!r} created_at={row[2]} status={row[3]}")
