import sys
sys.path.insert(0, '.')
from app.core.database import engine
from sqlalchemy import text

tables = [
    'audit_logs',
    'consent_evidence',
    'consent_history',
    'consents',
    'consent_contexts',
    'customers',
    'crm_customers',
]

with engine.connect() as conn:
    for table in tables:
        try:
            r = conn.execute(text(f'DELETE FROM {table}'))
            print(f'Deleted {r.rowcount} from {table}')
        except Exception as e:
            print(f'{table}: {e}')
            conn.rollback()

    conn.commit()

    r = conn.execute(text('SELECT count(*) FROM customers'))
    print(f'\nCustomers remaining: {r.scalar()}')
    r = conn.execute(text('SELECT count(*) FROM crm_customers'))
    print(f'CRM customers remaining: {r.scalar()}')
