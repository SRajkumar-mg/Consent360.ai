from app.core.encryption import hmac_digest
from app.models.entities import Consent, Customer, CrmCustomer, DataCategory, Organization, ProcessingActivity, Purpose, PurposeVersion
from app.services import consent as consent_service
from app.services.context import create_context_for_customer
from app.services.tenancy import PLATFORM_TENANT_CODE, platform_tenant_id, resolve_tenant_id


def test_platform_tenant_exists_after_migration(db):
    org = db.query(Organization).filter(Organization.code == PLATFORM_TENANT_CODE).first()
    assert org is not None
    assert org.grievance_response_days <= 90


def test_resolve_tenant_id_creates_new_tenant_for_unseen_source_app(db):
    tenant_id = resolve_tenant_id(db, "BRAND_NEW_APP")
    db.commit()
    org = db.get(Organization, tenant_id)
    assert org.code == "BRAND_NEW_APP"


def test_resolve_tenant_id_maps_placeholders_to_platform(db):
    assert resolve_tenant_id(db, "") == platform_tenant_id(db)
    assert resolve_tenant_id(db, "SYSTEM") == platform_tenant_id(db)
    assert resolve_tenant_id(db, "UI") == platform_tenant_id(db)


def test_new_customer_and_consent_carry_tenant_id(db):
    ctx = create_context_for_customer(db, name="Tenant Test", email="tenant-test@example.com", source_app="TENANCY_TEST")
    # external_id is encrypted (AES-GCM, random nonce per write), so equality
    # lookups must go through the deterministic external_id_search digest —
    # see docs/ARCHITECTURE.md's field-encryption note and app/core/encryption.py.
    customer = db.query(Customer).filter(Customer.external_id_search == hmac_digest(ctx.customer_id)).first()
    assert customer.tenant_id is not None
    tenant = db.get(Organization, customer.tenant_id)
    assert tenant.code == "TENANCY_TEST"

    category = DataCategory(name="tenancy-cat", code="tenancy_cat")
    activity = ProcessingActivity(name="tenancy-act", code="tenancy_act")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name="Tenancy Purpose", code="tenancy_purpose", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="consent", is_current=True, created_by="test",
    ))
    db.commit()
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="TENANCY_TEST"
    )
    assert consent.tenant_id == customer.tenant_id


def test_tenant_settings_endpoint_roundtrip(client, staff_token, db):
    org = Organization(name="Settings Test", code="SETTINGS_TEST", is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)

    resp = client.put(
        f"/organizations/{org.id}/settings",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "dpo_name": "Jane DPO", "dpo_email": "dpo@settings-test.example",
            "withdraw_url": "https://settings-test.example/withdraw",
            "rights_url": "https://settings-test.example/rights",
            "grievance_url": "https://settings-test.example/grievance",
            "board_complaint_url": "https://settings-test.example/board",
            "grievance_response_days": 30,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dpo_name"] == "Jane DPO"
    assert body["grievance_response_days"] == 30

    # Clearing a previously-set DPO email by sending "" must succeed (not 422) —
    # the admin console's settings form always sends the full form including
    # blanked-out fields. See TenantSettingsUpdate._validate_dpo_email.
    resp = client.put(
        f"/organizations/{org.id}/settings",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"dpo_email": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["dpo_email"] == ""

    # A genuinely malformed email must still be rejected.
    resp = client.put(
        f"/organizations/{org.id}/settings",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"dpo_email": "not-an-email"},
    )
    assert resp.status_code == 422


def test_public_privacy_contact_and_rights_endpoints(client, db):
    org = Organization(
        name="Public Test", code="PUBLIC_TEST", is_active=True,
        dpo_name="Public DPO", dpo_email="dpo@public-test.example",
        withdraw_url="https://public-test.example/withdraw",
        rights_url="https://public-test.example/rights",
        grievance_url="https://public-test.example/grievance",
        board_complaint_url="https://public-test.example/board",
        grievance_response_days=45,
    )
    db.add(org)
    db.commit()

    resp = client.get("/public/PUBLIC_TEST/privacy-contact")
    assert resp.status_code == 200
    assert resp.json()["dpo_email"] == "dpo@public-test.example"

    resp = client.get("/public/PUBLIC_TEST/rights")
    assert resp.status_code == 200
    assert resp.json()["grievance_response_days"] == 45

    resp = client.get("/public/NO_SUCH_TENANT/privacy-contact")
    assert resp.status_code == 404


def test_crm_directory_login_creates_customer_with_tenant_id(client, db):
    """Goes through the real CRM directory login endpoint (app/api/routes/crm_directory.py),
    which internally creates both a CrmCustomer and a linked platform Customer
    (`_ensure_consent360_customer`). Regression test for the fix-round-1 bug where
    that Customer(...) construction never set tenant_id.

    R3: /crm/login's source_app is now constrained to CRM_FAMILY_SOURCE_APPS
    (see app/api/routes/crm.py) - an arbitrary label like the original
    "CRM_DIRECTORY_TENANCY_TEST" is exactly the free-form value that let an
    anonymous caller claim to be any tenant, so this now uses "CODEX", one
    of the three allowed names, which still exercises the same tenant_id
    assertion below."""
    resp = client.post(
        "/crm/login",
        json={"name": "Directory Test", "email": "directory-test@example.com",
              "phone": "+911234567890", "source_app": "CODEX"},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] is True

    customer = db.query(Customer).filter(
        Customer.email_search == hmac_digest("directory-test@example.com")
    ).first()
    assert customer is not None
    assert customer.tenant_id is not None
    tenant = db.get(Organization, customer.tenant_id)
    assert tenant.code == "CODEX"


def test_crm_consent_preferences_creates_linked_customer_with_tenant_id(client, db):
    """Goes through the real CRM consent-preferences endpoint (app/api/routes/crm.py),
    which lazily creates the linked platform Customer via `_link_customer` if one
    doesn't exist yet (simulating "Consent360 was unreachable at CRM login time").
    The prerequisite CrmCustomer is inserted directly since there is no other way
    to have a CrmCustomer without also going through crm_directory's own linking;
    the Customer under test is created by the route under test, not by this setup."""
    crm_customer = CrmCustomer(
        name="Preferences Test", email="prefs-test@example.com",
        email_search=hmac_digest("prefs-test@example.com"), phone="+911234567891",
    )
    db.add(crm_customer)
    db.commit()
    db.refresh(crm_customer)

    resp = client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        json={"lang": "en", "categories": {"necessary": True, "analytics": False}},
    )
    assert resp.status_code == 200

    customer = db.query(Customer).filter(
        Customer.email_search == hmac_digest("prefs-test@example.com")
    ).first()
    assert customer is not None
    assert customer.tenant_id is not None
    tenant = db.get(Organization, customer.tenant_id)
    assert tenant.code == "CRM_PORTAL"
