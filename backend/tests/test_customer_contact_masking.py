"""Contact masking is a server-side control, not a display convention.

The admin console's Customers page has always announced that "email and phone
are masked for privacy" while `GET /customers` returned both in full - the
masking happened in the browser, so anyone holding a `customer.view` token
(devtools, a script, a stolen session) read every principal's real contact
details straight out of the JSON. A screen that promises a protection the API
does not provide is worse than one that promises nothing, because it invites
false confidence.

These tests pin the control down at the API boundary:

  * a caller WITHOUT `customer.contact.view` gets `mask_identifier(...)`
    output for `email` and `phone` - and the raw values appear nowhere in the
    response body, not merely in those two fields;
  * a caller WITH it gets the record untouched;
  * `name` stays visible to both, deliberately (see PERM_CUSTOMER_CONTACT_VIEW
    in app/core/rbac.py for the argument);
  * the data principal's own portal is unaffected - she sees her own email
    address and telephone number in full;
  * masking lives in the response layer only. The ORM row and plain
    `CustomerOut.model_validate()` still carry real values, because
    notifications, the erasure engine and the CRM sync read through them and a
    masked address handed to `queue_notification` would be an outage.

Delete the masking from app/api/routes/customers.py and the `_masked` tests
below fail.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.encryption import hmac_digest
from app.core.rbac import (
    ALL_PERMISSIONS,
    ORG_SCOPE_MAP,
    PERM_CUSTOMER_CONTACT_VIEW,
    PERM_CUSTOMER_VIEW,
    ROLE_PERMISSIONS,
)
from app.core.security import create_access_token, hash_password
from app.core.utils import mask_identifier
from app.models.entities import Customer, Role, User

REAL_EMAIL = "ananya.gupta@example.com"
REAL_PHONE = "+91-98444-55666"
REAL_NAME = "Ananya Gupta"
MASKED_EMAIL = mask_identifier(REAL_EMAIL)
MASKED_PHONE = mask_identifier(REAL_PHONE)


def _token_for(db, role_name: str, username: str) -> str:
    role = db.query(Role).filter(Role.name == role_name).first()
    assert role is not None, f"expected '{role_name}' to be seeded by rbac.sync_roles at startup"
    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(
            username=username, full_name=username, email=f"{username}@example.com",
            email_search=hmac_digest(f"{username}@example.com"),
            password_hash=hash_password("Str0ng!Passw0rd"), role_id=role.id, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return create_access_token(user.id, user.username, role.name)


@pytest.fixture()
def principal(db):
    """One customer with real, recognisable contact details."""
    external_id = "CUST-MASKGAP-001"
    existing = db.query(Customer).filter(Customer.external_id_search == hmac_digest(external_id)).first()
    if existing:
        return existing
    customer = Customer(
        external_id=external_id,
        external_id_search=hmac_digest(external_id),
        name=REAL_NAME,
        email=REAL_EMAIL,
        email_search=hmac_digest(REAL_EMAIL),
        phone=REAL_PHONE,
        status="ACTIVE",
        source_app="MASKGAP",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _row_for(body, external_id):
    rows = [r for r in body if r["external_id"] == external_id]
    assert rows, f"{external_id} not in the customer listing"
    return rows[0]


# --------------------------------------------------------------------------- #
# The permission itself
# --------------------------------------------------------------------------- #

def test_customer_contact_view_is_a_declared_permission():
    assert PERM_CUSTOMER_CONTACT_VIEW == "customer.contact.view"
    assert PERM_CUSTOMER_CONTACT_VIEW in ALL_PERMISSIONS


def test_only_admin_and_dpo_hold_customer_contact_view():
    """Least privilege, and a deliberately narrow starting point. Widening
    this grant should be a conscious edit that trips this test, not something
    that drifts in unnoticed."""
    holders = {
        name for name, perms in ROLE_PERMISSIONS.items()
        if PERM_CUSTOMER_CONTACT_VIEW in perms or "*" in perms
    }
    assert holders == {"admin", "dpo"}


def test_contact_view_is_strictly_narrower_than_customer_view():
    """Every role that can read contact details can obviously also read the
    directory; the reverse must not hold, or the new permission grants
    nothing. `auditor` and `viewer` are the point of the exercise: neither
    needs a principal's telephone number to review compliance."""
    directory_readers = {
        name for name, perms in ROLE_PERMISSIONS.items()
        if PERM_CUSTOMER_VIEW in perms or "*" in perms
    }
    contact_readers = {
        name for name, perms in ROLE_PERMISSIONS.items()
        if PERM_CUSTOMER_CONTACT_VIEW in perms or "*" in perms
    }
    assert contact_readers < directory_readers
    assert {"auditor", "viewer", "consent_manager", "operator"} <= (directory_readers - contact_readers)


def test_org_scoped_admins_do_not_hold_contact_view():
    """Recorded as a decision rather than left as an oversight - see the note
    beside PERM_CUSTOMER_CONTACT_VIEW in rbac.py. If this is ever widened,
    widen it here on purpose."""
    for role_name in ORG_SCOPE_MAP:
        assert PERM_CUSTOMER_CONTACT_VIEW not in ROLE_PERMISSIONS[role_name], role_name


# --------------------------------------------------------------------------- #
# GET /customers
# --------------------------------------------------------------------------- #

def test_customer_list_is_masked_for_a_viewer(db, client, principal):
    token = _token_for(db, "viewer", "maskgap-viewer")
    resp = client.get("/customers", params={"limit": 500}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    row = _row_for(resp.json(), principal.external_id)
    assert row["email"] == MASKED_EMAIL
    assert row["phone"] == MASKED_PHONE
    # Not just those two fields: the real values must not be anywhere in the
    # body a browser or a script actually receives.
    assert REAL_EMAIL not in resp.text
    assert REAL_PHONE not in resp.text


def test_customer_list_is_masked_for_an_auditor(db, client, principal):
    token = _token_for(db, "auditor", "maskgap-auditor")
    resp = client.get("/customers", params={"limit": 500}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    row = _row_for(resp.json(), principal.external_id)
    assert row["email"] == MASKED_EMAIL
    assert row["phone"] == MASKED_PHONE


def test_customer_list_keeps_the_name_visible_to_a_viewer(db, client, principal):
    """Deliberate asymmetry. A staff member cannot work a grievance or a
    rights request without knowing which person it concerns; a full telephone
    number is for contacting them, which is a different and rarer need."""
    token = _token_for(db, "viewer", "maskgap-viewer")
    resp = client.get("/customers", params={"limit": 500}, headers={"Authorization": f"Bearer {token}"})
    row = _row_for(resp.json(), principal.external_id)
    assert row["name"] == REAL_NAME


def test_customer_list_is_unmasked_for_admin(db, client, principal, staff_token):
    resp = client.get("/customers", params={"limit": 500}, headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200
    row = _row_for(resp.json(), principal.external_id)
    assert row["email"] == REAL_EMAIL
    assert row["phone"] == REAL_PHONE


def test_customer_list_is_unmasked_for_dpo(db, client, principal):
    token = _token_for(db, "dpo", "maskgap-dpo")
    resp = client.get("/customers", params={"limit": 500}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    row = _row_for(resp.json(), principal.external_id)
    assert row["email"] == REAL_EMAIL
    assert row["phone"] == REAL_PHONE


def test_customer_search_still_matches_on_email_but_returns_it_masked(db, client, principal):
    """The search path is a separate code path from the plain listing and had
    to be masked separately. Search continues to run over the real values so
    the console's one search box keeps working."""
    token = _token_for(db, "viewer", "maskgap-viewer")
    resp = client.get(
        "/customers", params={"search": "ananya.gupta", "limit": 500},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    row = _row_for(resp.json(), principal.external_id)
    assert row["email"] == MASKED_EMAIL
    assert REAL_EMAIL not in resp.text


# --------------------------------------------------------------------------- #
# GET /customers/{external_id}
# --------------------------------------------------------------------------- #

def test_customer_detail_is_masked_for_a_viewer(db, client, principal):
    token = _token_for(db, "viewer", "maskgap-viewer")
    resp = client.get(f"/customers/{principal.external_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == MASKED_EMAIL
    assert body["phone"] == MASKED_PHONE
    assert body["name"] == REAL_NAME
    assert REAL_EMAIL not in resp.text
    assert REAL_PHONE not in resp.text


def test_customer_detail_is_unmasked_for_admin(db, client, principal, staff_token):
    resp = client.get(
        f"/customers/{principal.external_id}", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == REAL_EMAIL
    assert body["phone"] == REAL_PHONE


# --------------------------------------------------------------------------- #
# The bypass: the same CustomerOut is embedded in the consent summary
# --------------------------------------------------------------------------- #

def test_consent_summary_masks_the_embedded_customer_for_a_viewer(db, client, principal):
    """`GET /consents/summary/{id}` embeds a full CustomerOut. Left unmasked
    it would have made `customer.contact.view` decorative: the same viewer
    just asks for the consent summary instead of the directory row."""
    token = _token_for(db, "viewer", "maskgap-viewer")
    resp = client.get(
        f"/consents/summary/{principal.external_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    customer = resp.json()["customer"]
    assert customer["email"] == MASKED_EMAIL
    assert customer["phone"] == MASKED_PHONE
    assert customer["name"] == REAL_NAME
    assert REAL_EMAIL not in resp.text
    assert REAL_PHONE not in resp.text


def test_consent_summary_is_unmasked_for_admin(db, client, principal, staff_token):
    resp = client.get(
        f"/consents/summary/{principal.external_id}",
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert resp.status_code == 200
    customer = resp.json()["customer"]
    assert customer["email"] == REAL_EMAIL
    assert customer["phone"] == REAL_PHONE


# --------------------------------------------------------------------------- #
# Masking is in the response layer only - nothing internal sees masked values
# --------------------------------------------------------------------------- #

def test_the_orm_row_is_never_masked(db, principal):
    """Notifications (app/services/notifications.py reads customer.email /
    customer.phone), the erasure engine and the CRM sync all go through the
    ORM. A masked address reaching queue_notification would be an outage."""
    fresh = db.get(Customer, principal.id)
    assert fresh.email == REAL_EMAIL
    assert fresh.phone == REAL_PHONE


def test_plain_customerout_validation_is_unmasked(principal):
    """`CustomerOut.model_validate` is what /portal/overview builds its
    `customer` field from. If masking were a validator on the model, the data
    principal would see her own contact details masked in her own portal."""
    from app.schemas.schemas import CustomerOut

    out = CustomerOut.model_validate(principal)
    assert out.email == REAL_EMAIL
    assert out.phone == REAL_PHONE


def test_for_staff_masks_only_the_contact_fields(principal):
    from app.schemas.schemas import CustomerOut

    masked = CustomerOut.for_staff(principal, contact_visible=False)
    assert masked.email == MASKED_EMAIL
    assert masked.phone == MASKED_PHONE
    assert masked.name == REAL_NAME
    assert masked.external_id == principal.external_id
    assert masked.status == principal.status
    assert masked.source_app == principal.source_app

    visible = CustomerOut.for_staff(principal, contact_visible=True)
    assert visible.email == REAL_EMAIL
    assert visible.phone == REAL_PHONE


def test_user_has_permission_fails_closed_without_a_role():
    from types import SimpleNamespace

    from app.api.deps import user_has_permission

    assert user_has_permission(None, PERM_CUSTOMER_CONTACT_VIEW) is False
    assert user_has_permission(SimpleNamespace(role=None), PERM_CUSTOMER_CONTACT_VIEW) is False
    granted = SimpleNamespace(role=SimpleNamespace(name="x", permissions=[PERM_CUSTOMER_CONTACT_VIEW]))
    assert user_has_permission(granted, PERM_CUSTOMER_CONTACT_VIEW) is True


# --------------------------------------------------------------------------- #
# The data principal's own portal must not be affected
# --------------------------------------------------------------------------- #

def _make_purpose(db, code):
    from app.models.entities import DataCategory, ProcessingActivity, Purpose, PurposeVersion

    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose


def test_portal_overview_shows_a_principal_her_own_contact_details_in_full(db, client):
    """A data principal reading her OWN record is not a staff member browsing
    a directory, and `customer.contact.view` has nothing to do with her. If
    this ever fails, masking has leaked out of the staff response layer and
    into the model or the ORM."""
    from app.core.security import create_context_token
    from app.models.entities import ConsentContext

    _make_purpose(db, "maskgap_portal")
    external_id = "CUST-MASKGAP-PORTAL"
    customer = Customer(
        external_id=external_id, external_id_search=hmac_digest(external_id),
        name="Portal Principal", email="portal.principal@example.com",
        email_search=hmac_digest("portal.principal@example.com"),
        phone="+91-90000-11111", status="ACTIVE", source_app="MASKGAP_PORTAL",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    token = create_context_token(customer.id, "MASKGAP_PORTAL")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="MASKGAP_PORTAL",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert resp.status_code == 200, resp.text
    body = resp.json()["customer"]
    assert body["email"] == "portal.principal@example.com"
    assert body["phone"] == "+91-90000-11111"
