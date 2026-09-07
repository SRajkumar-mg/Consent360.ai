from app.core.encryption import hmac_digest
from app.core.security import hash_password
from app.models.entities import Customer, CrmCustomer, User, Role


def test_customer_insert_autofills_search_columns(db):
    customer = Customer(
        external_id="CUST-T0-001",
        name="Test Person",
        email="t0-person@example.com",
        phone="+91-90000-00001",
        status="ACTIVE",
        source_app="TEST",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    assert customer.external_id_search == hmac_digest("CUST-T0-001")
    assert customer.email_search == hmac_digest("t0-person@example.com")

    found = db.query(Customer).filter(Customer.external_id_search == hmac_digest("CUST-T0-001")).first()
    assert found is not None and found.id == customer.id


def test_crm_customer_insert_autofills_email_search(db):
    crm_customer = CrmCustomer(name="CRM Person", email="crm-t0@example.com")
    db.add(crm_customer)
    db.commit()
    db.refresh(crm_customer)
    assert crm_customer.email_search == hmac_digest("crm-t0@example.com")


def test_user_insert_autofills_email_search_and_seed_style_creation_works(db):
    role = db.query(Role).filter(Role.name == "viewer").first()
    if not role:
        role = Role(name="viewer", description="Read-only", permissions=["dashboard.view"], is_system=True)
        db.add(role)
        db.flush()
    user = User(
        username="t0-user",
        full_name="T0 User",
        email="t0-user@example.com",
        password_hash=hash_password("Passw0rd!"),
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.email_search == hmac_digest("t0-user@example.com")
