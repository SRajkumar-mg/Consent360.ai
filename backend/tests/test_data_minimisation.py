"""R1-12 (H-11, B-04): data minimisation clean-up - no Aadhaar column, no
synthetic customer records for anonymous visitors, necessity flag enforced
on purpose data items."""
import pytest
from fastapi import HTTPException
from sqlalchemy import inspect

from app.core.database import engine
from app.models.entities import CrmCustomer
from app.schemas.schemas import DataItemIn, PurposeIn, check_data_items_cover_categories


def test_crm_customer_model_has_no_aadhar_number_attribute():
    assert not hasattr(CrmCustomer, "aadhar_number")


def test_crm_customers_table_has_no_aadhar_number_column():
    insp = inspect(engine)
    columns = [c["name"] for c in insp.get_columns("crm_customers")]
    assert "aadhar_number" not in columns


def test_create_context_rejects_synthetic_visitor_email(db):
    from app.services.context import create_context_for_customer

    with pytest.raises(HTTPException) as exc_info:
        create_context_for_customer(
            db, name="Visitor", email="visitor-1738000000000@careerhub.local", source_app="CAREER_HUB",
        )
    assert exc_info.value.status_code == 422

    from app.models.entities import Customer

    assert db.query(Customer).filter(Customer.source_app == "CAREER_HUB").count() == 0


def test_create_context_accepts_real_email(db):
    from app.services.context import create_context_for_customer

    out = create_context_for_customer(
        db, name="Real Person", email="real.person@example.com", source_app="CAREER_HUB_REAL",
    )
    assert out.customer_id


def test_create_context_still_allows_explicit_customer_id_even_if_email_looks_synthetic(db):
    """An explicit customer_id is a real asserted identity, not a derived
    one - the synthetic-email heuristic must not block it."""
    from app.services.context import create_context_for_customer

    out = create_context_for_customer(
        db, name="Visitor", email="visitor-1738000000001@careerhub.local", source_app="CAREER_HUB_EXPLICIT",
        customer_id="employee-4471",
    )
    assert out.customer_id == "employee-4471"


# =========================================================================== #
#  B-04, second half: the guard has to hold on the CREDENTIAL-FREE path too
#
#  THE DEFECT THESE PIN. The refusal above lived inside
#  `create_context_for_customer`, so it only ever fired for callers that mint a
#  context - `POST /consent/customer-context` and crm.py's own
#  `.../consent-context`. `POST /crm/login` writes the CrmCustomer directory row
#  and, through `_ensure_consent360_customer`, the platform Customer row
#  directly, and never mints a context - so it sailed straight past the guard,
#  on the ONE customer-creating endpoint that takes no credential at all.
#  Reproduced live against the running backend:
#      POST /crm/login {"email": "visitor-1788531480@careerhub.local", ...}
#      -> 200, CrmCustomer id 29 and platform Customer id 47 created,
#  while the very same address on POST /consent/customer-context -> 422.
#
#  A consent attributed to `visitor-<timestamp>@...` names nobody: it cannot be
#  honoured, withdrawn, or produced as evidence that any identifiable person
#  agreed (s.6(1)).
# =========================================================================== #
SYNTHETIC_EMAIL = "visitor-1788531480@careerhub.local"


def _crm_login(client, email, **extra):
    return client.post("/crm/login", json={
        "name": "Anonymous Visitor", "email": email, "source_app": "CRM_PORTAL", **extra,
    })


def test_crm_login_refuses_a_synthesised_visitor_identity(db, client):
    """The reproduction, inverted: the credential-free signup path must refuse
    the address the credentialed one already refuses."""
    from app.models.entities import Customer

    before_crm = db.query(CrmCustomer).count()
    before_platform = db.query(Customer).count()

    resp = _crm_login(client, SYNTHETIC_EMAIL)

    assert resp.status_code == 422, resp.text
    assert "unidentified visitor" in resp.json()["detail"]
    # Nothing was written - not the directory row, and not the platform
    # Customer row `_ensure_consent360_customer` would have derived from it.
    db.expire_all()
    assert db.query(CrmCustomer).count() == before_crm
    assert db.query(Customer).count() == before_platform


def test_crm_login_still_accepts_a_real_identity(db, client):
    """The control: the guard must not turn the signup flow off."""
    resp = _crm_login(client, "real.signup@example.com")
    assert resp.status_code == 200, resp.text
    assert resp.json()["customer"]["email"] == "real.signup@example.com"


def test_crm_login_accepts_a_placeholder_email_when_a_real_phone_is_given(client):
    """Exactly the exemption `create_context_for_customer` grants: a phone is a
    real identifier, and the placeholder email is then only a display value
    beside it. The two paths must agree on the exemptions too, not just on the
    refusal."""
    resp = _crm_login(client, "visitor-1788531999@careerhub.local", phone="+919000000042")
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("email,refused", [
    ("visitor-1788531480@careerhub.local", True),
    ("visitor-0@x.test", True),
    ("VISITOR-1788531480@CareerHub.local", True),   # the predicate is case-insensitive
    ("  visitor-1788531480@careerhub.local", True),  # ... and strips
    ("visitor@careerhub.local", False),              # no timestamp: an ordinary local-part
    ("visitor-alpha@careerhub.local", False),
    ("real.person@example.com", False),
])
def test_both_customer_creating_paths_agree_on_every_address(db, client, email, refused):
    """ONE PREDICATE, TWO CALL SITES. This is the test that fails if either
    guard is removed, and equally if someone reimplements it at one call site
    and the copies drift - which is how this gap existed in the first place.

    `POST /crm/login` (no credential) and `create_context_for_customer` (the
    integration API's path) are asked about the same address and must give the
    same verdict."""
    from app.services.context import create_context_for_customer

    crm_refused = _crm_login(client, email).status_code == 422

    try:
        create_context_for_customer(
            db, name="Visitor", email=email, source_app="B04_PARITY",
        )
        context_refused = False
    except HTTPException as exc:
        context_refused = exc.status_code == 422
    finally:
        db.rollback()

    assert crm_refused == context_refused == refused, (
        f"{email!r}: /crm/login refused={crm_refused}, "
        f"create_context_for_customer refused={context_refused}, expected {refused}"
    )


def test_purpose_in_rejects_data_category_missing_necessity_item():
    with pytest.raises(Exception):
        PurposeIn(
            name="x", code="x_necessity", data_category_ids=[1, 2],
            data_items=[DataItemIn(data_category_id=1, necessity=True)],
        )


def test_purpose_in_rejects_extra_data_item_not_in_categories():
    with pytest.raises(Exception):
        PurposeIn(
            name="x", code="x_necessity2", data_category_ids=[1],
            data_items=[
                DataItemIn(data_category_id=1, necessity=True),
                DataItemIn(data_category_id=2, necessity=False),
            ],
        )


def test_purpose_in_accepts_matching_categories_and_items():
    p = PurposeIn(
        name="x", code="x_necessity3", data_category_ids=[1, 2],
        data_items=[
            DataItemIn(data_category_id=1, necessity=True),
            DataItemIn(data_category_id=2, necessity=False),
        ],
    )
    assert len(p.data_items) == 2


def test_check_data_items_cover_categories_rejects_duplicates():
    with pytest.raises(ValueError):
        check_data_items_cover_categories([1], [
            DataItemIn(data_category_id=1, necessity=True),
            DataItemIn(data_category_id=1, necessity=False),
        ])


def test_purpose_update_route_enforces_necessity_on_merged_state(db, client, staff_token):
    from app.models.entities import DataCategory

    cat1 = DataCategory(name="Necessity Cat 1", code="necessity_cat_1")
    cat2 = DataCategory(name="Necessity Cat 2", code="necessity_cat_2")
    db.add_all([cat1, cat2])
    db.commit()
    db.refresh(cat1)
    db.refresh(cat2)

    create_resp = client.post(
        "/purposes",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "name": "Necessity Purpose", "code": "necessity_purpose",
            "data_category_ids": [cat1.id],
            "data_items": [{"data_category_id": cat1.id, "necessity": True, "description": "needed"}],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    purpose_id = create_resp.json()["id"]

    # Adding a second data_category_id via PATCH without a matching
    # data_items entry must be rejected - the merged post-update state
    # (not just the raw partial payload) is what gets validated.
    bad_update = client.put(
        f"/purposes/{purpose_id}",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"data_category_ids": [cat1.id, cat2.id]},
    )
    assert bad_update.status_code == 422

    good_update = client.put(
        f"/purposes/{purpose_id}",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "data_category_ids": [cat1.id, cat2.id],
            "data_items": [
                {"data_category_id": cat1.id, "necessity": True, "description": "needed"},
                {"data_category_id": cat2.id, "necessity": False, "description": "nice to have"},
            ],
        },
    )
    assert good_update.status_code == 200, good_update.text
