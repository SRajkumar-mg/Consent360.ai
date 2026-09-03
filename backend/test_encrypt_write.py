from app.core.database import SessionLocal
from app.models.entities import Customer

db = SessionLocal()
try:
    # Write new record
    c = Customer(external_id="enc-test-001", name="Test Encrypt", email="test@enc.com", phone="+91-99999-00000")
    db.add(c)
    db.commit()
    db.refresh(c)
    pk = c.id
    print(f"Write OK. PK={pk}")
    print(f"Read after write: name={c.name}, email={c.email}, phone={c.phone}")

    # Read back by PK in a fresh session
    db.expire_all()
    c2 = db.query(Customer).get(pk)
    print(f"Fresh read by PK: name={c2.name}, email={c2.email}, phone={c2.phone}")

    # Cleanup
    db.delete(c2)
    db.commit()
    print("WRITE + READ test PASSED!")
finally:
    db.close()
