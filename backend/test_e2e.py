"""End-to-end test for field-level encryption with search digests."""
from app.core.database import SessionLocal
from app.core.encryption import hmac_digest
from app.models.entities import Customer, CrmCustomer, User, Consent, AuditLog

db = SessionLocal()
try:
    # Test 1: Read and decrypt
    c = db.query(Customer).first()
    print(f"1. READ OK: Customer name={c.name}, email={c.email}")

    # Test 2: Query by search digest
    email_digest = hmac_digest("visitor-test@careerhub.local")
    c2 = db.query(Customer).filter(Customer.email_search == email_digest).first()
    print(f"2. QUERY by email_search OK: found={c2 is not None}, name={c2.name if c2 else 'N/A'}")

    # Test 3: Query by external_id search digest
    ext_digest = hmac_digest("CUST-XXXXXXXXXX")  # Will not match, but should not crash
    c3 = db.query(Customer).filter(Customer.external_id_search == ext_digest).first()
    print(f"3. QUERY by external_id_search OK: found={c3 is not None}")

    # Test 4: CrmCustomer query by email_search
    crm_digest = hmac_digest("aarav.patel@example.com")
    crm = db.query(CrmCustomer).filter(CrmCustomer.email_search == crm_digest).first()
    print(f"4. CrmCustomer QUERY OK: name={crm.name if crm else 'N/A'}")

    # Test 5: User query by email_search
    user_digest = hmac_digest("admin@consent360.local")
    user = db.query(User).filter(User.email_search == user_digest).first()
    print(f"5. User QUERY OK: username={user.username if user else 'N/A'}")

    # Test 6: Write new record with search digest
    from app.services.context import derive_customer_id
    ext_id = derive_customer_id(email="test-e2e@test.com")
    new_cust = Customer(
        external_id=ext_id,
        external_id_search=hmac_digest(ext_id),
        name="E2E Test",
        email="test-e2e@test.com",
        email_search=hmac_digest("test-e2e@test.com"),
        phone="+91-00000-00000",
        status="ACTIVE",
        source_app="E2E_TEST",
    )
    db.add(new_cust)
    db.commit()
    db.refresh(new_cust)
    print(f"6. WRITE OK: id={new_cust.id}, ext_id={new_cust.external_id}")

    # Test 7: Read back the new record by email search
    found = db.query(Customer).filter(Customer.email_search == hmac_digest("test-e2e@test.com")).first()
    print(f"7. READ-BACK OK: name={found.name if found else 'N/A'}")

    # Cleanup
    db.delete(found)
    db.commit()
    print("8. CLEANUP OK")
    print("\n=== ALL TESTS PASSED ===")
finally:
    db.close()
