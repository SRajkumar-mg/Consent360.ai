"""R3-10 (CM-01, CM-02, CM-06, J-04): consent validation API and Consent
Manager artefact API.

The two Definition-of-Done clauses this file has to actually demonstrate:

  1. A fiduciary system can validate consent BEFORE processing.
     `test_a_fiduciary_validates_consent_before_processing` walks the full
     REQUIRE_CONSENT -> ALLOW -> WITHDRAWN sequence through
     `POST /decisions/evaluate` and checks the decision log written for each.

  2. A registered Consent Manager can give, manage, review and withdraw
     consent through the artefact API.
     `test_a_consent_manager_gives_reviews_manages_and_withdraws_consent`
     does exactly those four things in one test, and asserts the effect on
     the underlying `consents` rows each time.

Everything else here is the tenancy boundary. This API is the one place in
the codebase where the calling key's tenant is legitimately not the tenant
whose data is touched, so the cross-tenant tests are not incidental coverage -
they are the reason the authorisation is shaped the way it is.
"""
import pytest

from app.core.api_keys import generate_api_key
from app.core.encryption import hmac_digest
from app.models.entities import (
    ApiKey,
    Consent,
    ConsentDecisionLog,
    Customer,
    DataCategory,
    Organization,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)


# --------------------------------------------------------------------------- #
#  Wiring
#
#  R3-10's two routers are registered in app/main.py by the integrating lane;
#  this fixture mounts them only if that has not happened yet, so the tests
#  pass either way rather than failing with a confusing 404 while the wiring
#  commit is in flight. The tables come from Alembic (revision e3d5b7c91a24)
#  like every other table - nothing here creates schema.
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module", autouse=True)
def _wire_r3_10():
    from app.api.routes import consent_manager as cm_routes
    from app.api.routes import decision_validation
    from app.main import app

    mounted = {route.path for route in app.routes}
    for router in (cm_routes.router, decision_validation.router):
        if not {r.path for r in router.routes} & mounted:
            app.include_router(router)
    yield


# --------------------------------------------------------------------------- #
#  Fixtures / helpers
# --------------------------------------------------------------------------- #

def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _org(db, code, name=None):
    org = db.query(Organization).filter(Organization.code == code).first()
    if not org:
        org = Organization(name=name or code.replace("_", " ").title(), code=code, is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


_KEY_CACHE: dict[tuple[str, tuple[str, ...]], str] = {}


def _api_key(db, org, scopes):
    """Issue (once per org+scope-set per session) a tenant-bound API key. Cached
    because the fixtures below run per test against a session-lifetime database,
    and a fresh key per test would pile up unused `api_keys` rows."""
    cache_key = (org.code, tuple(sorted(scopes)))
    if cache_key in _KEY_CACHE:
        return _KEY_CACHE[cache_key]
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(
        ApiKey(
            tenant_id=org.id,
            name=f"test-{org.code}",
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=list(scopes),
        )
    )
    db.commit()
    _KEY_CACHE[cache_key] = plaintext
    return plaintext


def _customer(db, external_id, source_app, email=None):
    org = _org(db, source_app)
    existing = db.query(Customer).filter(Customer.source_app == source_app).all()
    for row in existing:
        if row.external_id == external_id:
            return row
    email = email or f"{external_id.lower()}@example.test"
    customer = Customer(
        external_id=external_id,
        external_id_search=hmac_digest(external_id),
        name=f"Principal {external_id}",
        email=email,
        email_search=hmac_digest(email),
        source_app=source_app,
        tenant_id=org.id,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _purpose(db, code):
    existing = db.query(Purpose).filter(Purpose.code == code).first()
    if existing:
        return (
            existing,
            db.query(DataCategory).filter(DataCategory.code == f"cat_{code}").first(),
            db.query(ProcessingActivity).filter(ProcessingActivity.code == f"act_{code}").first(),
        )
    category = DataCategory(name=f"Category {code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"Activity {code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(
        name=f"Purpose {code}",
        code=code,
        legal_basis="CONSENT",
        requires_consent=True,
        retention_period_days=365,
    )
    db.add(purpose)
    db.flush()
    db.add(
        PurposeVersion(
            purpose_id=purpose.id,
            version_number=1,
            name=purpose.name,
            data_category_ids=[category.id],
            processing_activity_ids=[activity.id],
            consent_text=f"I consent to {code}.",
            is_current=True,
            created_by="test",
        )
    )
    db.commit()
    db.refresh(purpose)
    return purpose, category, activity


def _register_cm(client, staff_token, *, tenant_code, name="Acme Consent Manager", status="REGISTERED", disclosures=None):
    resp = client.post(
        "/consent-manager/registrations",
        headers=_auth(staff_token),
        json={
            "name": name,
            "tenant_code": tenant_code,
            "board_registration_number": "DPB/CM/2026/0001",
            "registration_status": status,
            "contact_email": "grievance@acme-cm.test",
            "website_url": "https://acme-cm.test",
            "disclosures": disclosures
            or {
                "promoters": [{"name": "Acme Holdings Pvt Ltd", "type": "BODY_CORPORATE"}],
                "directors": [{"name": "A. Director", "din": "00000001"}],
                "key_managerial_personnel": [{"name": "K. Officer", "role": "CEO"}],
                "shareholders_above_two_percent": [{"name": "Acme Holdings Pvt Ltd", "percentage": 74.0}],
            },
        },
    )
    if resp.status_code == 409:
        # Idempotent: these fixtures run once per test against a database that
        # persists across the session, so re-register means "find the existing
        # one and put it back in a known state".
        listed = client.get("/consent-manager/registrations", headers=_auth(staff_token))
        assert listed.status_code == 200, listed.text
        cm = next(row for row in listed.json() if row["tenant_code"] == tenant_code)
        reset = client.patch(
            f"/consent-manager/registrations/{cm['cm_ref']}",
            headers=_auth(staff_token),
            json={"registration_status": status, "is_active": True},
        )
        assert reset.status_code == 200, reset.text
        return reset.json()
    assert resp.status_code == 200, resp.text
    return resp.json()


def _onboard(client, staff_token, cm_ref, source_app, *, status="ACTIVE", allowed=None):
    resp = client.post(
        f"/consent-manager/registrations/{cm_ref}/fiduciaries",
        headers=_auth(staff_token),
        json={
            "source_app": source_app,
            "status": status,
            "allowed_purpose_codes": allowed or [],
        },
    )
    if resp.status_code == 409:
        resp = client.patch(
            f"/consent-manager/registrations/{cm_ref}/fiduciaries/{source_app}",
            headers=_auth(staff_token),
            json={"status": status, "allowed_purpose_codes": allowed or []},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


# =========================================================================== #
#  DoD 1: a fiduciary system validates consent before processing
# =========================================================================== #

def test_a_fiduciary_validates_consent_before_processing(client, db):
    """REQUIRE_CONSENT before a grant, ALLOW after it, WITHDRAWN after a
    withdrawal - each decision persisted to `consent_decision_logs`."""
    org = _org(db, "DEC_FIDUCIARY")
    key = _api_key(db, org, ["integration.write", "decision.evaluate"])
    purpose, category, activity = _purpose(db, "dec_marketing")
    customer = _customer(db, "DEC-CUST-001", "DEC_FIDUCIARY")

    body = {
        "customer_id": customer.external_id,
        "purpose_code": purpose.code,
        "data_category_code": category.code,
        "processing_activity_code": activity.code,
    }

    before = client.post("/decisions/evaluate", headers={"X-API-Key": key}, json=body)
    assert before.status_code == 200, before.text
    assert before.json()["decision"] == "REQUIRE_CONSENT"
    assert before.json()["allowed"] is False
    assert before.json()["source_app"] == "DEC_FIDUCIARY"
    assert before.json()["decision_log_id"] is not None

    # The principal now grants consent through the ordinary lifecycle.
    from app.services import consent as consent_service

    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="DEC_FIDUCIARY", exact_source=True
    )
    consent_service.grant_consent(db, consent, source_app="DEC_FIDUCIARY")

    allowed = client.post("/decisions/evaluate", headers={"X-API-Key": key}, json=body)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["decision"] == "ALLOW"
    assert allowed.json()["allowed"] is True
    assert allowed.json()["consent_status"] in ("GRANTED", "ACTIVE")
    assert allowed.json()["consent_version"] == 1

    consent_service.withdraw_consent(db, consent, source_app="DEC_FIDUCIARY")

    after = client.post("/decisions/evaluate", headers={"X-API-Key": key}, json=body)
    assert after.status_code == 200, after.text
    assert after.json()["decision"] == "WITHDRAWN"
    assert after.json()["allowed"] is False

    logged = (
        db.query(ConsentDecisionLog)
        .filter(
            ConsentDecisionLog.customer_id == customer.id,
            ConsentDecisionLog.source_app == "DEC_FIDUCIARY",
        )
        .order_by(ConsentDecisionLog.id)
        .all()
    )
    assert [row.decision for row in logged] == ["REQUIRE_CONSENT", "ALLOW", "WITHDRAWN"]


def test_decision_endpoint_requires_its_own_scope(client, db):
    org = _org(db, "DEC_NOSCOPE")
    key = _api_key(db, org, ["integration.write"])  # deliberately not decision.evaluate
    purpose, category, activity = _purpose(db, "dec_noscope")
    customer = _customer(db, "DEC-NOSCOPE-001", "DEC_NOSCOPE")

    resp = client.post(
        "/decisions/evaluate",
        headers={"X-API-Key": key},
        json={
            "customer_id": customer.external_id,
            "purpose_code": purpose.code,
            "data_category_code": category.code,
            "processing_activity_code": activity.code,
        },
    )
    assert resp.status_code == 403
    assert "decision.evaluate" in resp.json()["detail"]


def test_decision_endpoint_refuses_a_legacy_unbound_key(client, db):
    purpose, category, activity = _purpose(db, "dec_legacy")
    _customer(db, "DEC-LEGACY-001", "DEC_LEGACY")

    resp = client.post(
        "/decisions/evaluate",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={
            "customer_id": "DEC-LEGACY-001",
            "purpose_code": purpose.code,
            "data_category_code": category.code,
            "processing_activity_code": activity.code,
        },
    )
    assert resp.status_code == 403
    assert "tenant-bound" in resp.json()["detail"]


def test_decision_endpoint_rejects_a_source_app_the_key_does_not_own(client, db):
    org = _org(db, "DEC_OWNER")
    key = _api_key(db, org, ["integration.write", "decision.evaluate"])
    purpose, category, activity = _purpose(db, "dec_mismatch")
    customer = _customer(db, "DEC-MISMATCH-001", "DEC_OWNER")

    resp = client.post(
        "/decisions/evaluate",
        headers={"X-API-Key": key},
        json={
            "customer_id": customer.external_id,
            "purpose_code": purpose.code,
            "data_category_code": category.code,
            "processing_activity_code": activity.code,
            "source_app": "SOMEONE_ELSE",
        },
    )
    assert resp.status_code == 403
    assert "does not match the tenant bound to this API key" in resp.json()["detail"]


def test_decision_endpoint_cannot_read_another_tenants_principal(client, db):
    """One tenant's key must not be able to learn anything about another
    tenant's principal - not even that they exist - and must leave no trace on
    the other tenant's records."""
    attacker_org = _org(db, "DEC_ATTACKER")
    victim_org = _org(db, "DEC_VICTIM")
    attacker_key = _api_key(db, attacker_org, ["integration.write", "decision.evaluate"])
    purpose, category, activity = _purpose(db, "dec_crosstenant")
    victim = _customer(db, "DEC-VICTIM-001", "DEC_VICTIM")

    from app.services import consent as consent_service

    victim_consent, _ = consent_service.get_or_create_consent(
        db, victim, purpose, category, activity, source_app="DEC_VICTIM", exact_source=True
    )
    consent_service.grant_consent(db, victim_consent, source_app="DEC_VICTIM")
    victim_consent_id = victim_consent.id
    victim_status_before = victim_consent.status
    victim_version_before = victim_consent.consent_version

    resp = client.post(
        "/decisions/evaluate",
        headers={"X-API-Key": attacker_key},
        json={
            "customer_id": victim.external_id,
            "purpose_code": purpose.code,
            "data_category_code": category.code,
            "processing_activity_code": activity.code,
        },
    )
    assert resp.status_code == 404
    # A cross-tenant principal is indistinguishable from one that does not exist.
    assert resp.json()["detail"] == "No such principal at this tenant"

    # The victim's own record is untouched...
    db.expire_all()
    still = db.get(Consent, victim_consent_id)
    assert still.status == victim_status_before
    assert still.consent_version == victim_version_before
    # ...and no decision was logged against them by the attacker.
    assert (
        db.query(ConsentDecisionLog)
        .filter(
            ConsentDecisionLog.customer_id == victim.id,
            ConsentDecisionLog.source_app == "DEC_ATTACKER",
        )
        .count()
        == 0
    )


# =========================================================================== #
#  DoD 2: a registered Consent Manager gives, manages, reviews and withdraws
# =========================================================================== #

@pytest.fixture()
def cm_world(client, db, staff_token):
    """A registered Consent Manager, one onboarded fiduciary with a principal
    and two purposes, and a second fiduciary the CM is NOT onboarded to."""
    cm_org = _org(db, "ACME_CM")
    fiduciary = _org(db, "CM_BANK")
    other = _org(db, "CM_OTHER")

    cm = _register_cm(client, staff_token, tenant_code=cm_org.code)
    _onboard(client, staff_token, cm["cm_ref"], fiduciary.code)

    cm_key = _api_key(db, cm_org, ["artefact.read", "artefact.write"])
    fiduciary_key = _api_key(db, fiduciary, ["integration.write", "artefact.read", "artefact.write", "decision.evaluate"])

    p1, c1, a1 = _purpose(db, "cm_statements")
    p2, c2, a2 = _purpose(db, "cm_offers")
    principal = _customer(db, "CM-PRINCIPAL-001", fiduciary.code)
    other_principal = _customer(db, "CM-OTHER-001", other.code)

    return {
        "cm": cm,
        "cm_org": cm_org,
        "cm_key": cm_key,
        "fiduciary": fiduciary,
        "fiduciary_key": fiduciary_key,
        "other": other,
        "other_principal": other_principal,
        "principal": principal,
        "purposes": {"p1": (p1, c1, a1), "p2": (p2, c2, a2)},
    }


def test_a_consent_manager_gives_reviews_manages_and_withdraws_consent(client, db, cm_world):
    key = cm_world["cm_key"]
    fiduciary = cm_world["fiduciary"].code
    p1 = cm_world["purposes"]["p1"][0]
    p2 = cm_world["purposes"]["p2"][0]
    principal = cm_world["principal"]
    headers = {"X-API-Key": key}

    # --- GIVE ---------------------------------------------------------- #
    created = client.post(
        "/consent-manager/artefacts",
        headers=headers,
        json={
            "source_app": fiduciary,
            "customer_id": principal.external_id,
            "purpose_codes": [p1.code],
            "principal_action_reference": "acme-cm-session-9f21/checkbox-1",
        },
    )
    assert created.status_code == 200, created.text
    artefact = created.json()
    ref = artefact["artefact_ref"]
    assert artefact["status"] == "ACTIVE"
    assert artefact["artefact_version"] == 1
    assert artefact["consent_manager_ref"] == cm_world["cm"]["cm_ref"]
    assert [p["purpose_code"] for p in artefact["purposes"]] == [p1.code]

    granted = (
        db.query(Consent)
        .filter(Consent.customer_id == principal.id, Consent.purpose_id == p1.id)
        .all()
    )
    assert granted and all(c.status == "GRANTED" for c in granted)
    assert all(c.source_app == fiduciary for c in granted)
    assert all(c.collection_method == "CONSENT_MANAGER" for c in granted)

    # The CM's attestation reference is on the evidence for every record.
    evidence = granted[0].evidence[-1]
    assert "acme-cm-session-9f21/checkbox-1" in evidence.details["affirmative_reference"]
    assert evidence.details["affirmative_reference"].startswith(f"cm:{cm_world['cm']['cm_ref']}")

    # --- REVIEW -------------------------------------------------------- #
    listed = client.get(
        "/consent-manager/artefacts", headers=headers, params={"source_app": fiduciary}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["artefacts"][0]["artefact_ref"] == ref

    read = client.get(
        f"/consent-manager/artefacts/{ref}", headers=headers, params={"source_app": fiduciary}
    )
    assert read.status_code == 200
    payload = read.json()["payload"]
    # TS 27560 Table 1 record-header fields, by their ISO names.
    assert payload["record_id"] == ref
    assert payload["schema_version"] == "urn:consent360:artefact-schema:1.0"
    assert payload["pii_principal_id"] == read.json()["principal_ref"]
    # Party identification section: the fiduciary is the PII controller and
    # every purpose references it by party_id rather than inlining it.
    controller = next(p for p in payload["parties"] if p["party_role"] == "PII_CONTROLLER")
    assert controller["party_id"] == fiduciary
    assert controller["x_dpdp_role"] == "DATA_FIDUCIARY"
    cm_party = next(p for p in payload["parties"] if p["party_id"] == cm_world["cm"]["cm_ref"])
    assert cm_party["party_role"] == "x_dpdp_CONSENT_MANAGER"
    purposes = payload["pii_processing"]["purposes"]
    assert {entry["x_consent360"]["purpose_code"] for entry in purposes} == {p1.code}
    assert purposes[0]["lawful_basis"] == "CONSENT"
    assert purposes[0]["pii_controllers"] == [fiduciary]
    assert payload["x_dpdp"]["record_retention_years"] == 7

    # --- MANAGE (add a purpose) ---------------------------------------- #
    managed = client.post(
        f"/consent-manager/artefacts/{ref}/update",
        headers=headers,
        params={"source_app": fiduciary},
        json={
            "purpose_codes": [p1.code, p2.code],
            "principal_action_reference": "acme-cm-session-9f21/checkbox-2",
        },
    )
    assert managed.status_code == 200, managed.text
    assert managed.json()["artefact_version"] == 2
    assert {p["purpose_code"] for p in managed.json()["purposes"]} == {p1.code, p2.code}
    p2_consents = (
        db.query(Consent)
        .filter(Consent.customer_id == principal.id, Consent.purpose_id == p2.id)
        .all()
    )
    assert p2_consents and all(c.status == "GRANTED" for c in p2_consents)

    # --- MANAGE (drop a purpose) --------------------------------------- #
    dropped = client.post(
        f"/consent-manager/artefacts/{ref}/update",
        headers=headers,
        params={"source_app": fiduciary},
        json={
            "purpose_codes": [p1.code],
            "principal_action_reference": "acme-cm-session-9f21/checkbox-3",
        },
    )
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["artefact_version"] == 3
    assert dropped.json()["status"] == "PARTIAL"
    db.expire_all()
    assert all(
        c.status == "WITHDRAWN"
        for c in db.query(Consent).filter(
            Consent.customer_id == principal.id, Consent.purpose_id == p2.id
        )
    )

    # --- WITHDRAW ------------------------------------------------------ #
    withdrawn = client.post(
        f"/consent-manager/artefacts/{ref}/withdraw",
        headers=headers,
        params={"source_app": fiduciary},
        json={"principal_action_reference": "acme-cm-session-9f21/withdraw"},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "WITHDRAWN"
    assert withdrawn.json()["withdrawn_at"] is not None
    assert withdrawn.json()["artefact_version"] == 4
    db.expire_all()
    assert all(
        c.status == "WITHDRAWN"
        for c in db.query(Consent).filter(Consent.customer_id == principal.id)
    )

    # Four versions, each its own signed event, in order.
    events = withdrawn.json()["events"]
    assert [e["event_type"] for e in events] == ["CREATED", "UPDATED", "UPDATED", "WITHDRAWN"]
    assert [e["artefact_version"] for e in events] == [1, 2, 3, 4]
    assert all(e["signature_valid"] for e in events)


def test_artefact_events_are_signed_and_tamper_evident(client, db, cm_world):
    from app.models.artefacts import ConsentArtefactEvent

    headers = {"X-API-Key": cm_world["cm_key"]}
    fiduciary = cm_world["fiduciary"].code
    created = client.post(
        "/consent-manager/artefacts",
        headers=headers,
        json={
            "source_app": fiduciary,
            "customer_id": cm_world["principal"].external_id,
            "purpose_codes": [cm_world["purposes"]["p1"][0].code],
            "principal_action_reference": "tamper-test",
        },
    )
    assert created.status_code == 200, created.text
    ref = created.json()["artefact_ref"]
    assert created.json()["events"][0]["signature_alg"] == "HMAC-SHA256"
    assert created.json()["events"][0]["signature_valid"] is True

    from app.models.artefacts import ConsentArtefact

    artefact = db.query(ConsentArtefact).filter(ConsentArtefact.artefact_ref == ref).first()
    event = (
        db.query(ConsentArtefactEvent)
        .filter(ConsentArtefactEvent.artefact_id == artefact.id)
        .first()
    )
    tampered = dict(event.payload)
    tampered["jurisdiction"] = "XX"
    event.payload = tampered
    db.commit()

    read = client.get(
        f"/consent-manager/artefacts/{ref}", headers=headers, params={"source_app": fiduciary}
    )
    assert read.status_code == 200
    assert read.json()["events"][0]["signature_valid"] is False


def test_consent_manager_payload_is_data_blind(client, db, cm_world):
    """CM-02 / First Schedule Part B 2. The Consent Manager gets consent
    metadata and a pseudonymous principal reference - never the principal's
    name, email, phone or the fiduciary's own id for them; and never any
    personal-data value. The fiduciary reading its OWN tenant is not a CM and
    does see its own external id."""
    import json

    principal = cm_world["principal"]
    fiduciary = cm_world["fiduciary"].code
    cm_headers = {"X-API-Key": cm_world["cm_key"]}

    created = client.post(
        "/consent-manager/artefacts",
        headers=cm_headers,
        json={
            "source_app": fiduciary,
            "customer_id": principal.external_id,
            "purpose_codes": [cm_world["purposes"]["p1"][0].code],
            "principal_action_reference": "blind-test",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    blob = json.dumps(body)

    assert body["principal_ref"].startswith("pid_")
    assert body["principal_external_id"] is None
    assert body["payload"]["pii_principal_id"] == body["principal_ref"]
    assert "pii_principal_external_id" not in body["payload"]["x_consent360"]
    assert "pii_principal_email_masked" not in body["payload"]["x_consent360"]
    assert body["payload"]["x_consent360"]["data_blind"] is True
    for secret in (principal.external_id, principal.email, principal.name):
        assert secret not in blob, f"data-blind violation: {secret!r} reached the Consent Manager"

    # The same principal is a DIFFERENT reference to a different CM, so two
    # Consent Managers cannot correlate their subject populations.
    from app.services.consent_manager import principal_pseudonym

    assert principal_pseudonym("CM-OTHER-REF", principal.id) != body["principal_ref"]
    assert principal_pseudonym(None, principal.id) != body["principal_ref"]

    # A CM may not filter by the fiduciary's own customer id at all.
    refused = client.get(
        "/consent-manager/artefacts",
        headers=cm_headers,
        params={"source_app": fiduciary, "customer_id": principal.external_id},
    )
    assert refused.status_code == 403
    assert "data-blind" in refused.json()["detail"]

    # The fiduciary reading its own tenant is not treated as data-blind.
    own = client.get(
        "/consent-manager/artefacts",
        headers={"X-API-Key": cm_world["fiduciary_key"]},
        params={"customer_id": principal.external_id},
    )
    assert own.status_code == 200, own.text
    # It sees its own external id back; it sees no CM-brokered artefact under
    # its own direct-caller principal_ref namespace, but the artefact rows
    # themselves belong to its tenant, so they are listed.
    assert own.json()["total"] >= 1
    assert own.json()["artefacts"][0]["principal_external_id"] == principal.external_id


# =========================================================================== #
#  Tenancy boundary
# =========================================================================== #

def test_consent_manager_cannot_act_for_a_fiduciary_it_is_not_onboarded_to(client, db, cm_world):
    """The cross-tenant attempt, and proof the other tenant is untouched."""
    from app.models.artefacts import ConsentArtefact

    headers = {"X-API-Key": cm_world["cm_key"]}
    other = cm_world["other"].code
    other_principal = cm_world["other_principal"]
    purpose = cm_world["purposes"]["p1"][0]

    consents_before = db.query(Consent).filter(Consent.source_app == other).count()
    artefacts_before = db.query(ConsentArtefact).filter(ConsentArtefact.source_app == other).count()

    attempts = [
        client.post(
            "/consent-manager/artefacts",
            headers=headers,
            json={
                "source_app": other,
                "customer_id": other_principal.external_id,
                "purpose_codes": [purpose.code],
                "principal_action_reference": "should-not-work",
            },
        ),
        client.get("/consent-manager/artefacts", headers=headers, params={"source_app": other}),
        client.post(
            "/consent-manager/artefacts/CA-DOESNOTEXIST/withdraw",
            headers=headers,
            params={"source_app": other},
            json={},
        ),
    ]
    for resp in attempts:
        assert resp.status_code == 403, resp.text
        assert "not onboarded to act for" in resp.json()["detail"]

    # Nothing was created, granted or changed on the other tenant.
    assert db.query(Consent).filter(Consent.source_app == other).count() == consents_before
    assert (
        db.query(ConsentArtefact).filter(ConsentArtefact.source_app == other).count()
        == artefacts_before
    )
    db.expire_all()
    refreshed = db.query(Customer).filter(Customer.id == other_principal.id).first()
    assert refreshed.source_app == other


def test_a_consent_manager_must_name_a_fiduciary_and_may_not_broker_for_itself(client, cm_world):
    headers = {"X-API-Key": cm_world["cm_key"]}

    missing = client.get("/consent-manager/artefacts", headers=headers)
    assert missing.status_code == 422
    assert "source_app is required" in missing.json()["detail"]

    itself = client.get(
        "/consent-manager/artefacts", headers=headers, params={"source_app": cm_world["cm_org"].code}
    )
    assert itself.status_code == 403
    assert "cannot broker consent for itself" in itself.json()["detail"]


def test_an_unregistered_or_suspended_consent_manager_is_refused(client, db, staff_token, cm_world):
    """s.6(9)/R.4: only a Board-registered Consent Manager may act. Suspending
    the registration stops it immediately, without touching the onboarding."""
    headers = {"X-API-Key": cm_world["cm_key"]}
    fiduciary = cm_world["fiduciary"].code
    cm_ref = cm_world["cm"]["cm_ref"]

    ok = client.get("/consent-manager/artefacts", headers=headers, params={"source_app": fiduciary})
    assert ok.status_code == 200

    suspended = client.patch(
        f"/consent-manager/registrations/{cm_ref}",
        headers=_auth(staff_token),
        json={"registration_status": "SUSPENDED"},
    )
    assert suspended.status_code == 200, suspended.text

    blocked = client.get(
        "/consent-manager/artefacts", headers=headers, params={"source_app": fiduciary}
    )
    assert blocked.status_code == 403
    assert "not currently registered" in blocked.json()["detail"]

    client.patch(
        f"/consent-manager/registrations/{cm_ref}",
        headers=_auth(staff_token),
        json={"registration_status": "REGISTERED"},
    )


def test_terminating_an_onboarding_stops_the_consent_manager(client, db, staff_token, cm_world):
    headers = {"X-API-Key": cm_world["cm_key"]}
    fiduciary = cm_world["fiduciary"].code
    cm_ref = cm_world["cm"]["cm_ref"]

    terminated = client.patch(
        f"/consent-manager/registrations/{cm_ref}/fiduciaries/{fiduciary}",
        headers=_auth(staff_token),
        json={"status": "TERMINATED"},
    )
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["terminated_at"] is not None

    blocked = client.get(
        "/consent-manager/artefacts", headers=headers, params={"source_app": fiduciary}
    )
    assert blocked.status_code == 403

    client.patch(
        f"/consent-manager/registrations/{cm_ref}/fiduciaries/{fiduciary}",
        headers=_auth(staff_token),
        json={"status": "ACTIVE"},
    )


def test_onboarding_can_bound_which_purposes_a_consent_manager_may_broker(client, db, staff_token, cm_world):
    headers = {"X-API-Key": cm_world["cm_key"]}
    fiduciary = cm_world["fiduciary"].code
    cm_ref = cm_world["cm"]["cm_ref"]
    p1 = cm_world["purposes"]["p1"][0]
    p2 = cm_world["purposes"]["p2"][0]

    client.patch(
        f"/consent-manager/registrations/{cm_ref}/fiduciaries/{fiduciary}",
        headers=_auth(staff_token),
        json={"allowed_purpose_codes": [p1.code]},
    )
    refused = client.post(
        "/consent-manager/artefacts",
        headers=headers,
        json={
            "source_app": fiduciary,
            "customer_id": cm_world["principal"].external_id,
            "purpose_codes": [p2.code],
            "principal_action_reference": "bounded",
        },
    )
    assert refused.status_code == 403
    assert "outside the purposes" in refused.json()["detail"]

    client.patch(
        f"/consent-manager/registrations/{cm_ref}/fiduciaries/{fiduciary}",
        headers=_auth(staff_token),
        json={"allowed_purpose_codes": []},
    )


def test_one_consent_manager_cannot_read_another_consent_managers_artefact(client, db, staff_token, cm_world):
    fiduciary = cm_world["fiduciary"].code
    first_headers = {"X-API-Key": cm_world["cm_key"]}

    created = client.post(
        "/consent-manager/artefacts",
        headers=first_headers,
        json={
            "source_app": fiduciary,
            "customer_id": cm_world["principal"].external_id,
            "purpose_codes": [cm_world["purposes"]["p1"][0].code],
            "principal_action_reference": "first-cm",
        },
    )
    assert created.status_code == 200, created.text
    ref = created.json()["artefact_ref"]

    # A second, equally-registered and equally-onboarded Consent Manager.
    rival_org = _org(db, "RIVAL_CM")
    rival = _register_cm(client, staff_token, tenant_code=rival_org.code, name="Rival Consent Manager")
    _onboard(client, staff_token, rival["cm_ref"], fiduciary)
    rival_key = _api_key(db, rival_org, ["artefact.read", "artefact.write"])

    peek = client.get(
        f"/consent-manager/artefacts/{ref}",
        headers={"X-API-Key": rival_key},
        params={"source_app": fiduciary},
    )
    assert peek.status_code == 404
    listed = client.get(
        "/consent-manager/artefacts",
        headers={"X-API-Key": rival_key},
        params={"source_app": fiduciary},
    )
    assert listed.status_code == 200
    assert ref not in [a["artefact_ref"] for a in listed.json()["artefacts"]]


def test_the_legacy_unbound_key_cannot_reach_the_artefact_api(client, db, cm_world):
    resp = client.get(
        "/consent-manager/artefacts",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        params={"source_app": cm_world["fiduciary"].code},
    )
    # require_scope refuses the legacy key every scope but integration.write.
    assert resp.status_code == 403
    assert "artefact.read" in resp.json()["detail"]


def test_artefact_write_requires_the_write_scope(client, db, cm_world):
    read_only_key = _api_key(db, cm_world["cm_org"], ["artefact.read"])
    resp = client.post(
        "/consent-manager/artefacts",
        headers={"X-API-Key": read_only_key},
        json={
            "source_app": cm_world["fiduciary"].code,
            "customer_id": cm_world["principal"].external_id,
            "purpose_codes": [cm_world["purposes"]["p1"][0].code],
            "principal_action_reference": "no-write-scope",
        },
    )
    assert resp.status_code == 403
    assert "artefact.write" in resp.json()["detail"]


def test_a_consent_manager_cannot_create_a_principal(client, cm_world):
    """A CM brokers consent for people who already have a relationship with the
    fiduciary; it has no write primitive over a tenant's customer base."""
    resp = client.post(
        "/consent-manager/artefacts",
        headers={"X-API-Key": cm_world["cm_key"]},
        json={
            "source_app": cm_world["fiduciary"].code,
            "email": "nobody-here@example.test",
            "purpose_codes": [cm_world["purposes"]["p1"][0].code],
            "principal_action_reference": "should-404",
        },
    )
    assert resp.status_code == 404
    assert "No such principal" in resp.json()["detail"]


def test_a_consent_manager_must_attest_the_principals_action(client, cm_world):
    resp = client.post(
        "/consent-manager/artefacts",
        headers={"X-API-Key": cm_world["cm_key"]},
        json={
            "source_app": cm_world["fiduciary"].code,
            "customer_id": cm_world["principal"].external_id,
            "purpose_codes": [cm_world["purposes"]["p1"][0].code],
        },
    )
    assert resp.status_code == 422
    assert "principal_action_reference is required" in resp.json()["detail"]


# =========================================================================== #
#  Disclosures, schema descriptor, metrics
# =========================================================================== #

def test_disclosures_are_public_and_list_only_registered_managers(client, db, staff_token, cm_world):
    """J-04 / First Schedule Part B 11."""
    applicant_org = _org(db, "APPLICANT_CM")
    _register_cm(
        client, staff_token, tenant_code=applicant_org.code, name="Applicant CM", status="PENDING"
    )

    resp = client.get("/consent-manager/disclosures")  # no credential at all
    assert resp.status_code == 200, resp.text
    by_name = {row["name"]: row for row in resp.json()}
    assert "Applicant CM" not in by_name
    acme = by_name["Acme Consent Manager"]
    assert acme["board_registration_number"] == "DPB/CM/2026/0001"
    assert acme["directors"] == [{"name": "A. Director", "din": "00000001"}]
    assert acme["shareholders_above_two_percent"][0]["percentage"] == 74.0
    assert acme["data_blind"] is True
    assert cm_world["fiduciary"].code in acme["onboarded_fiduciaries"]


def test_artefact_schema_descriptor_is_honest_about_conformance(client):
    resp = client.get("/consent-manager/artefact-schema")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "urn:consent360:artefact-schema:1.0"
    assert "ISO/IEC TS 27560" in body["modelled_on"]
    # The conformance statement must say plainly that this is modelled on, not
    # certified against, the standard, and that TS 27560 defines no normative
    # JSON encoding. A silently-softened claim here is the exact failure mode
    # these assertions exist to catch.
    assert "MODELLED ON ISO/IEC TS 27560:2023, NOT CERTIFIED AGAINST IT" in body["conformance_statement"]
    assert "information model, not a JSON encoding" in body["conformance_statement"]
    assert body["normative_json_encoding"] is False
    # Clause 6.3.2.1 obliges us to publish the schema we use; this endpoint is
    # that publication, and every payload's schema_version points at it.
    assert body["schema_reference"] == "/consent-manager/artefact-schema"
    assert "90.92" in body["standard_status"]
    for iso_field in ("schema_version", "record_id", "pii_principal_id"):
        assert iso_field in body["top_level_fields"]
        assert "verified against the ISO text" in body["field_provenance"][iso_field]
    # ...and the fields taken from the DPV guide are labelled as unverified.
    assert "NOT verified" in body["field_provenance"]["parties[]"]
    assert "NOT verified" in body["field_provenance"]["event"]


def test_metrics_report_availability_and_latency(client, db, staff_token, cm_world):
    headers = {"X-API-Key": cm_world["cm_key"]}
    fiduciary = cm_world["fiduciary"].code

    for _ in range(3):
        assert (
            client.get(
                "/consent-manager/artefacts", headers=headers, params={"source_app": fiduciary}
            ).status_code
            == 200
        )
    # One deliberate client error, to prove the two rates are distinguished.
    assert (
        client.get(
            "/consent-manager/artefacts", headers=headers, params={"source_app": "NOT_ONBOARDED"}
        ).status_code
        == 403
    )

    resp = client.get("/consent-manager/metrics", headers=_auth(staff_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_calls"] >= 4
    # A 4xx is the API answering correctly, so availability stays at 100%...
    assert body["availability_pct"] == 100.0
    # ...while the error rate still reports it.
    assert body["error_rate_pct"] > 0
    assert body["latency_ms_p95"] is not None
    assert body["record_retrieval_ms_p95"] is not None
    assert body["registered_consent_manager_count"] >= 1
    assert body["onboarded_fiduciary_count"] >= 1
    endpoints = {row["endpoint"] for row in body["per_endpoint"]}
    assert "GET /consent-manager/artefacts" in endpoints


def test_a_consent_manager_can_list_the_fiduciaries_it_is_onboarded_to(client, cm_world):
    resp = client.get("/consent-manager/fiduciaries", headers={"X-API-Key": cm_world["cm_key"]})
    assert resp.status_code == 200, resp.text
    assert [row["source_app"] for row in resp.json()] == [cm_world["fiduciary"].code]


def test_registering_a_consent_manager_requires_policy_management_permission(client, db):
    from app.core.rbac import PERM_DASHBOARD
    from app.core.security import create_access_token, hash_password
    from app.models.entities import Role, User

    role = db.query(Role).filter(Role.name == "cm-test-viewer").first()
    if not role:
        role = Role(name="cm-test-viewer", description="", permissions=[PERM_DASHBOARD])
        db.add(role)
        db.flush()
    user = db.query(User).filter(User.username == "cm-test-viewer").first()
    if not user:
        user = User(
            username="cm-test-viewer",
            full_name="CM Test Viewer",
            email="cm-test-viewer@example.test",
            email_search=hmac_digest("cm-test-viewer@example.test"),
            password_hash=hash_password("Test@1234"),
            role_id=role.id,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(user.id, user.username, role.name)

    resp = client.post(
        "/consent-manager/registrations",
        headers=_auth(token),
        json={"name": "Sneaky CM", "tenant_code": "ACME_CM"},
    )
    assert resp.status_code == 403
