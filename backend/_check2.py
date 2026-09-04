import sys
sys.path.insert(0, '.')
from sqlalchemy import create_engine, text
from app.core.config import get_settings
s = get_settings()
engine = create_engine(s.DATABASE_URL)
with engine.connect() as conn:
    r = conn.execute(text("SELECT id, tenant_id, purpose_id, title, version_number, language, status FROM notices"))
    for row in r:
        print(f"id={row[0]} tenant={row[1]} purpose={row[2]} title={row[3]!r} v={row[4]} lang={row[5]} status={row[6]}")
