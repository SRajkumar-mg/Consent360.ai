"""Regression tests for the cross-tenant customer-context breach.

`app/services/context.py::create_context_for_customer` used to look up an
existing `Customer` by `email_search` (or by a derived external id)
GLOBALLY, with no tenant/`source_app` filter at all. Neither
`app/api/deps.py::get_customer_from_context` nor
`app/api/routes/portal.py::_resolve_customer_and_context` then checked that
the resolved customer actually belonged to the context's own tenant.

Concretely: any integration API key - VICTIM_TENANT's own key, or (worse)
ANY other tenant's key, whether or not it carried `context.fiduciary_assert`
- calling `POST /consent/customer-context` with a victim's email got back a
context bound to the VICTIM's real `Customer` row, `source_app` stamped as
the CALLING tenant. `GET /portal/overview` with that context returned the
victim's real consent record. With a key that also carried
`SCOPE_FIDUCIARY_ASSERT`, the resulting context was additionally marked
`verification_method="FIDUCIARY_ASSERTED"`, skipping OTP entirely - the
attacker never contacted the victim.

The fix is layered, on the theory that no single control should be
load-bearing:
  1. `create_context_for_customer`'s lookups (by email, by derived id, and
     by an explicit `customer_id`) are now scoped to the calling tenant's
     own `source_app`. A same-email customer in another tenant is
     completely invisible: it is treated as "not found" and a brand-new,
     tenant-owned `Customer` is created instead (see the long docstring
     there for why "create new" was chosen over "reject").
  2. Fiduciary assertion additionally requires `customer.source_app ==
     source_app` as its own explicit check, independent of #1.
  3. `get_customer_from_context` (checks the context token's own signed
     `source_app` claim) and `_resolve_customer_and_context` (checks the
     persisted `ConsentContext.source_app`) each independently refuse a
     customer/context tenant mismatch, so a context minted wrongly by any
     future path still cannot be used.

These tests reproduce the exact attacker/victim setup from the report and
pin all four required guarantees, plus a construction-level test for layer 3
that bypasses `create_context_for_customer` entirely (the way a *future*,
still-buggy minting path might) to prove layer 3 holds on its own.
"""
from datetime import datetime, timedelta, timezone

from app.core.api_keys import SCOPE_FIDUCIARY_ASSERT, SCOPE_INTEGRATION_WRITE, generate_api_key
from app.core.encryption import hmac_digest
from app.core.security import create_context_token
from app.models.entities import ApiKey, Consent, ConsentContext, Customer, Organization


def _make_org_with_key(db, code, scopes):
    org = Organization(name=code.title(), code=code, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    plaintext, prefix, key_hash = generate_api_key(code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=scopes))
    db.commit()
    return org, plaintext


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
    return purpose, category, activity


def _victim_key(db, org_code):
    """Onboard a victim through their own tenant, exactly as docs/ARCHITECTURE.md
    describes any real integration flow doing. Each test uses its own
    ``org_code`` - Organization.code is unique and this database is shared
    (session-scoped) across every test in this file."""
    _org, key = _make_org_with_key(db, org_code, [SCOPE_INTEGRATION_WRITE])
    return key


def test_attacker_with_fiduciary_scope_cannot_bind_to_victims_customer(db, client):
    """The exact exploit from the report: an attacker tenant's key, holding
    context.fiduciary_assert, supplies the victim's email. It must not
    obtain a context bound to the victim, must not see the victim's real
    name, and no ConsentContext row may point at the victim's customer_id."""
    victim_key = _victim_key(db, "VICTIM_TENANT")
    victim_email = "shared-victim@example.com"
    victim_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": victim_key},
        json={"name": "Victim Real Name", "email": victim_email},
    )
    assert victim_resp.status_code == 200
    victim = db.query(Customer).filter(
        Customer.email_search == hmac_digest(victim_email), Customer.source_app == "VICTIM_TENANT",
    ).first()
    assert victim is not None
    victim_context_count_before = db.query(ConsentContext).filter(ConsentContext.customer_id == victim.id).count()

    _attacker_org, attacker_key = _make_org_with_key(
        db, "ATTACKER_TENANT", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT]
    )
    attack_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": attacker_key},
        json={"name": "Attacker Controlled Name", "email": victim_email},
    )
    assert attack_resp.status_code == 200
    body = attack_resp.json()

    # Refusal of access to the VICTIM: the attacker gets a context, but it is
    # for a brand-new customer owned by ATTACKER_TENANT, never the victim's.
    assert body["customer_id"] != victim.external_id

    # No context row was created pointing at the victim's customer_id.
    victim_context_count_after = db.query(ConsentContext).filter(ConsentContext.customer_id == victim.id).count()
    assert victim_context_count_after == victim_context_count_before

    # The victim's own row is untouched (no PII overwrite either).
    db.refresh(victim)
    assert victim.name == "Victim Real Name"
    assert victim.source_app == "VICTIM_TENANT"

    # The attacker's own context resolves to the attacker's own (new, empty)
    # customer - never the victim's real profile/consents.
    overview = client.get("/portal/overview", headers={"X-Context-Token": body["context_token"]})
    assert overview.status_code == 200
    assert overview.json()["customer"]["name"] == "Attacker Controlled Name"

    attacker_customer = db.query(Customer).filter(Customer.source_app == "ATTACKER_TENANT").first()
    assert attacker_customer is not None
    assert attacker_customer.id != victim.id


def test_attacker_without_fiduciary_scope_also_cannot_bind_to_victims_customer(db, client):
    """Even a key with NO fiduciary scope must not resolve to the victim's
    customer - the cross-tenant lookup gap is independent of that feature
    and would be live the moment PORTAL_REQUIRE_VERIFICATION is ever false."""
    victim_key = _victim_key(db, "VICTIM_TENANT_2")
    victim_email = "shared-victim-2@example.com"
    victim_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": victim_key},
        json={"name": "Victim Two", "email": victim_email},
    )
    assert victim_resp.status_code == 200
    victim = db.query(Customer).filter(
        Customer.email_search == hmac_digest(victim_email), Customer.source_app == "VICTIM_TENANT_2",
    ).first()
    victim_context_count_before = db.query(ConsentContext).filter(ConsentContext.customer_id == victim.id).count()

    _attacker_org, attacker_key = _make_org_with_key(db, "ATTACKER_TENANT_NO_SCOPE", [SCOPE_INTEGRATION_WRITE])
    attack_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": attacker_key},
        json={"name": "Attacker No Scope", "email": victim_email},
    )
    assert attack_resp.status_code == 200
    body = attack_resp.json()
    assert body["customer_id"] != victim.external_id
    # Never fiduciary-asserted for a key that never had the scope.
    context = db.query(ConsentContext).filter(ConsentContext.token == body["context_token"]).first()
    assert context.verification_method is None

    victim_context_count_after = db.query(ConsentContext).filter(ConsentContext.customer_id == victim.id).count()
    assert victim_context_count_after == victim_context_count_before


def test_tenant_asserting_for_its_own_existing_customer_still_works(db, client):
    """The legitimate case must be unaffected: a tenant can still identify
    (and fiduciary-assert) its OWN, previously-created customer - the exact
    within-tenant match the scoped lookup must still allow. The existing
    customer is seeded directly (rather than via a first HTTP call) so this
    test exercises only the "match an existing customer" path, not
    create_context_token's own (unrelated) same-second token-uniqueness
    behavior."""
    org_code = "LEGIT_TENANT"
    _org, key = _make_org_with_key(db, org_code, [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT])
    email = "legit-own-customer@example.com"
    existing = Customer(
        external_id="CUST-LEGIT-EXISTING", name="Legit Customer", email=email,
        email_search=hmac_digest(email), source_app=org_code,
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": key},
        json={"name": "Legit Customer", "email": email},
    )
    assert resp.status_code == 200
    # Same tenant, same email -> the SAME existing customer is matched, not duplicated.
    assert resp.json()["customer_id"] == existing.external_id

    context = db.query(ConsentContext).filter(ConsentContext.token == resp.json()["context_token"]).first()
    assert context.verified_at is not None
    assert context.verification_method == "FIDUCIARY_ASSERTED"

    overview = client.get("/portal/overview", headers={"X-Context-Token": resp.json()["context_token"]})
    assert overview.status_code == 200


def test_two_tenants_with_the_same_email_each_see_only_their_own(db, client):
    """Two unrelated tenants may each have a customer sharing an email
    (a common, unrelated coincidence) - each must get its own independent
    Customer row and its own independent consent data."""
    # Both keys carry the fiduciary scope purely so their contexts are
    # pre-verified and /portal/grant + /portal/overview don't 403 on OTP -
    # unrelated to what this test is actually pinning (tenant isolation of
    # the resulting customer and consent data).
    _org_a, key_a = _make_org_with_key(db, "TENANT_A_SAME_EMAIL", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT])
    _org_b, key_b = _make_org_with_key(db, "TENANT_B_SAME_EMAIL", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT])
    shared_email = "coincidence@example.com"
    purpose, _category, _activity = _make_purpose(db, "cross_tenant_shared_email")

    resp_a = client.post(
        "/consent/customer-context", headers={"X-API-Key": key_a},
        json={"name": "Person At Tenant A", "email": shared_email},
    )
    resp_b = client.post(
        "/consent/customer-context", headers={"X-API-Key": key_b},
        json={"name": "Person At Tenant B", "email": shared_email},
    )
    assert resp_a.status_code == 200 and resp_b.status_code == 200
    assert resp_a.json()["customer_id"] != resp_b.json()["customer_id"]

    customer_a = db.query(Customer).filter(Customer.source_app == "TENANT_A_SAME_EMAIL").first()
    customer_b = db.query(Customer).filter(Customer.source_app == "TENANT_B_SAME_EMAIL").first()
    assert customer_a.id != customer_b.id
    assert customer_a.name == "Person At Tenant A"
    assert customer_b.name == "Person At Tenant B"

    # Tenant A grants a purpose; Tenant B's own view of "the same person"
    # must not show it.
    grant = client.post(
        "/portal/grant", headers={"X-Context-Token": resp_a.json()["context_token"]},
        json={"purpose_code": purpose.code},
    )
    assert grant.status_code == 200

    overview_b = client.get("/portal/overview", headers={"X-Context-Token": resp_b.json()["context_token"]})
    assert overview_b.status_code == 200
    purpose_b = next(p for p in overview_b.json()["purposes"] if p["code"] == purpose.code)
    assert purpose_b["status"] == "NOT_GRANTED"

    overview_a = client.get("/portal/overview", headers={"X-Context-Token": resp_a.json()["context_token"]})
    purpose_a = next(p for p in overview_a.json()["purposes"] if p["code"] == purpose.code)
    assert purpose_a["status"] == "GRANTED"


def test_explicit_customer_id_claimed_by_another_tenant_is_rejected_not_merged(db, client):
    """An explicit customer_id (not derived from email) is a literal
    identity assertion. If another tenant already owns that literal id, the
    caller must get a clean conflict - never silently resolve to the other
    tenant's customer, and never crash on the table's global external_id
    uniqueness constraint."""
    _org_a, key_a = _make_org_with_key(db, "ID_OWNER_TENANT", [SCOPE_INTEGRATION_WRITE])
    owned_id = "CUST-EXPLICIT-COLLISION-001"
    first = client.post(
        "/consent/customer-context", headers={"X-API-Key": key_a},
        json={"name": "Owner", "email": "id-owner@example.com", "customer_id": owned_id},
    )
    assert first.status_code == 200
    assert first.json()["customer_id"] == owned_id

    _org_b, key_b = _make_org_with_key(db, "ID_CLAIMANT_TENANT", [SCOPE_INTEGRATION_WRITE])
    second = client.post(
        "/consent/customer-context", headers={"X-API-Key": key_b},
        json={"name": "Claimant", "email": "id-claimant@example.com", "customer_id": owned_id},
    )
    assert second.status_code == 409

    owners = db.query(Customer).filter(Customer.external_id_search == hmac_digest(owned_id)).all()
    assert len(owners) == 1
    assert owners[0].source_app == "ID_OWNER_TENANT"


def test_resolution_side_refuses_a_context_bound_to_the_wrong_tenants_customer(db, client):
    """Layer 3 on its own: construct a Customer + ConsentContext pair
    exactly the way a hypothetical future minting bug might - a context
    whose OWN source_app does not match the customer it points at - without
    going anywhere near create_context_for_customer. get_customer_from_context
    and _resolve_customer_and_context must both refuse it independently of
    whatever produced it."""
    customer = Customer(
        external_id="CUST-WRONG-TENANT-BINDING", name="Real Owner Tenant Customer",
        source_app="REAL_OWNER_TENANT",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    # The token's own signed claim already disagrees with the customer's
    # real source_app - exactly what get_customer_from_context checks.
    mismatched_token = create_context_token(customer.id, "INTRUDING_TENANT")
    db.add(ConsentContext(
        customer_id=customer.id, token=mismatched_token, source_app="INTRUDING_TENANT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    ))
    db.commit()

    resp = client.get("/portal/overview", headers={"X-Context-Token": mismatched_token})
    assert resp.status_code == 404

    grant_resp = client.post(
        "/portal/grant", headers={"X-Context-Token": mismatched_token}, json={"purpose_code": "does-not-matter"},
    )
    assert grant_resp.status_code == 404


def test_resolution_side_refuses_when_only_the_persisted_context_row_disagrees(db, client):
    """Layer 3b in isolation: the token's own claim agrees with the
    customer (so get_customer_from_context's check passes), but the
    PERSISTED ConsentContext.source_app row disagrees - proving
    _resolve_customer_and_context's own, separate check against the
    database row is not merely a duplicate of the JWT-claim check."""
    customer = Customer(
        external_id="CUST-WRONG-TENANT-BINDING-2", name="Another Real Owner",
        source_app="REAL_OWNER_TENANT_2",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    # Token claims the CORRECT (matching) source_app...
    token = create_context_token(customer.id, "REAL_OWNER_TENANT_2")
    # ...but the persisted context row itself was written with a different
    # one (the only way this could happen today is a bug elsewhere; the
    # test constructs it directly to isolate this one check).
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="SOME_OTHER_TENANT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    ))
    db.commit()

    resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Round 2: a third consumer of a raw ConsentContext, and the legacy key path.
# ---------------------------------------------------------------------------
#
# An adversarial re-review found two more instances of the same class:
#
# 1. app/api/routes/integration.py::consume_context (GET
#    /consent/context/consume/{token}) resolves `db.get(Customer,
#    context.customer_id)` and trusts `context.source_app` exactly like the
#    two call sites above, but had neither of their independent checks. A
#    context minted with a mismatched source_app (again, only reachable
#    today via direct construction - create_context_for_customer can no
#    longer produce one) returned the resolved customer's real profile,
#    consents, history and audit rows at 200.
# 2. app/api/routes/integration.py::create_customer_context, when called
#    with the shared, unbound legacy key (ALLOW_LEGACY_INTEGRATION_KEY),
#    lets the caller name ANY source_app - there is no tenant_code to
#    scope by. Naming a real tenant's own source_app then satisfied layer
#    1's scoped lookup (Customer.source_app == source_app) exactly as if
#    the caller genuinely were that tenant, matching (and, via the update
#    branch, overwriting) that tenant's real customer.


def test_consume_context_refuses_a_mismatched_context_and_withholds_victim_data(db, client):
    """consume_context is the third consumer of a raw ConsentContext -
    alongside get_customer_from_context and _resolve_customer_and_context -
    and must refuse a customer/context tenant mismatch exactly like they do,
    disclosing none of the resolved customer's data in the process."""
    victim = Customer(
        external_id="CUST-CONSUME-VICTIM", name="Consume Victim Real Name",
        email="consume-victim@example.com", source_app="CONSUME_VICTIM_TENANT",
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)

    mismatched_token = create_context_token(victim.id, "CONSUME_INTRUDER_TENANT")
    db.add(ConsentContext(
        customer_id=victim.id, token=mismatched_token, source_app="CONSUME_INTRUDER_TENANT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    ))
    db.commit()

    resp = client.get(f"/consent/context/consume/{mismatched_token}")
    assert resp.status_code == 404
    assert "Consume Victim Real Name" not in resp.text
    assert "CUST-CONSUME-VICTIM" not in resp.text

    # Refused before being "consumed" - a legitimate retry (once the real
    # bug that produced such a row is fixed) must not find it already spent.
    context = db.query(ConsentContext).filter(ConsentContext.token == mismatched_token).first()
    assert context.is_active is True
    assert context.consumed_at is None


def test_legacy_key_cannot_bind_to_a_tenant_with_its_own_api_key(db, client):
    """The legacy, unbound INTEGRATION_API_KEY carries no tenant identity of
    its own. Naming a REAL tenant's own source_app must not let it match
    (or overwrite) that tenant's actual customer - once any tenant has
    proven ownership by issuing itself a real key, the shared legacy key is
    locked out of that source_app."""
    _org, real_key = _make_org_with_key(db, "LEGACY_ATTACK_VICTIM_TENANT", [SCOPE_INTEGRATION_WRITE])
    victim_email = "legacy-attack-victim@example.com"
    onboard = client.post(
        "/consent/customer-context", headers={"X-API-Key": real_key},
        json={"name": "Legacy Victim Real Name", "email": victim_email, "phone": "+910000000001"},
    )
    assert onboard.status_code == 200
    victim = db.query(Customer).filter(
        Customer.email_search == hmac_digest(victim_email), Customer.source_app == "LEGACY_ATTACK_VICTIM_TENANT",
    ).first()
    assert victim is not None
    victim_context_count_before = db.query(ConsentContext).filter(ConsentContext.customer_id == victim.id).count()

    attack = client.post(
        "/consent/customer-context", headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={
            "name": "Legacy Attacker Controlled Name", "email": victim_email, "phone": "+919999999999",
            "source_app": "LEGACY_ATTACK_VICTIM_TENANT",
        },
    )
    assert attack.status_code == 403
    # The rejection itself must not leak the victim's data either.
    assert "Legacy Victim Real Name" not in attack.text
    assert victim.external_id not in attack.text

    db.refresh(victim)
    assert victim.name == "Legacy Victim Real Name"
    assert victim.phone == "+910000000001"
    victim_context_count_after = db.query(ConsentContext).filter(ConsentContext.customer_id == victim.id).count()
    assert victim_context_count_after == victim_context_count_before


def test_legacy_key_still_works_for_a_source_app_nobody_owns(db, client):
    """Control: the legacy key must still work for its actual, intended use
    - a fresh source_app with no real tenant/API key behind it yet (see
    test_api_keys.py::test_legacy_key_still_works_by_default, which this
    mirrors) - proving the new guard is scoped to owned tenants only, not a
    blanket lockout of the legacy key."""
    resp = client.post(
        "/consent/customer-context", headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={"name": "Fresh Legacy Caller", "email": "fresh-legacy-caller@example.com",
              "source_app": "FRESH_UNOWNED_LEGACY_APP"},
    )
    assert resp.status_code == 200
    assert resp.json()["source_app"] == "FRESH_UNOWNED_LEGACY_APP"
