from app.core.database import SessionLocal
from app.models.entities import Customer, CrmCustomer, Consent, AuditLog

db = SessionLocal()
try:
    c = db.query(Customer).first()
    print(f"Customer name: {c.name}")
    print(f"Customer email: {c.email}")
    crm = db.query(CrmCustomer).first()
    print(f"CrmCustomer name: {crm.name}")
    print(f"CrmCustomer email: {crm.email}")
    print(f"CrmCustomer phone: {crm.phone}")
    cons = db.query(Consent).first()
    ct = cons.consent_text[:80] if cons.consent_text else "(empty)"
    print(f"Consent text: {ct}")
    audit = db.query(AuditLog).first()
    ar = audit.reason[:80] if audit.reason else "(empty)"
    print(f"Audit reason: {ar}")
    print("SUCCESS - All decrypted!")
finally:
    db.close()
