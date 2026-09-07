"""Two enforcement gaps in the consent record, both reproduced live before
they were fixed:

  L-02  `services/decision_engine.py::evaluate_decision` never compared the
        requested data category and processing activity against the lists the
        PurposeVersion itemises, so a purpose that itemised NOTHING still
        returned ALLOW for an unrelated category and an unrelated activity.
        DPDP Act s.6(1) limits consent to the data necessary for the specified
        purpose and Rules 2025 R.3(b)(i) requires the notice to itemise it; an
        engine that ignores the itemisation makes it decorative.

  Q-07  `Sec-GPC: 1` was read server-side and stored durably on
        `consent_evidence.gpc_signal`, and then nothing decided anything from
        it: a request carrying the objection together with `analytics: true`
        still produced an ACTIVE analytics consent, while the browser gate
        suppressed the same tags client-side. Client and server disagreed
        about one request, and the server is the record of truth.

Every test here fails if its enforcement is removed - that is the point of
the file. See app/services/gpc.py and `_purpose_scope_violation` /
`_binding_purpose_version` in the decision engine for the reasoning.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.api_keys import generate_api_key
from app.core.encryption import hmac_digest
from app.models.entities import (
    ApiKey,
    AuditLog,
    Consent,
    ConsentContext,
    ConsentDecisionLog,
    ConsentEvidence,
    CrmCustomer,
    Customer,
    DataCategory,
    Organization,
    Policy,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)


# --------------------------------------------------------------------------- #
#  fixtures
# --------------------------------------------------------------------------- #
def _org(db, code):
    row = db.query(Organization).filter(Organization.code == code).first()
    if not row:
        row = Organization(name=f"Org {code}", code=code)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


_KEYS: dict[str, str] = {}


def _api_key(db, org, scopes):
    if org.code in _KEYS:
        return _KEYS[org.code]
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(tenant_id=org.id, name=f"key-{org.code}", key_prefix=prefix,
                  key_hash=key_hash, scopes=list(scopes)))
    db.commit()
    _KEYS[org.code] = plaintext
    return plaintext


def _customer(db, external_id, source_app):
    org = _org(db, source_app)
    existing = (
        db.query(Customer)
        .filter(Customer.external_id_search == hmac_digest(external_id),
                Customer.source_app == source_app)
        .first()
    )
    if existing:
        return existing
    email = f"{external_id.lower()}@example.test"
    row = Customer(external_id=external_id, external_id_search=hmac_digest(external_id),
                   name=f"Principal {external_id}", email=email, email_search=hmac_digest(email),
                   source_app=source_app, tenant_id=org.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _codes(db, code):
    category = DataCategory(name=f"Category {code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"Activity {code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.commit()
    db.refresh(category)
    db.refresh(activity)
    return category, activity


def _purpose(db, code, *, categories=(), activities=(), legal_basis="CONSENT",
             requires_consent=True):
    purpose = Purpose(name=f"Purpose {code}", code=code, legal_basis=legal_basis,
                      requires_consent=requires_consent, retention_period_days=365)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        legal_basis=legal_basis, requires_consent=requires_consent,
        data_category_ids=[c.id for c in categories],
        processing_activity_ids=[a.id for a in activities],
        consent_text=f"I consent to {code}.", is_current=True, created_by="test",
    ))
    db.commit()
    db.refresh(purpose)
    return purpose


def _new_purpose_version(db, purpose, *, categories=(), activities=()):
    """Publish a further version and make it the current one, exactly as
    routes/purposes.py does - the point being that a consent already pinned to
    the old version must keep the OLD scope."""
    for version in purpose.versions:
        version.is_current = False
    number = max(v.version_number for v in purpose.versions) + 1
    version = PurposeVersion(
        purpose_id=purpose.id, version_number=number, name=purpose.name,
        legal_basis=purpose.legal_basis, requires_consent=purpose.requires_consent,
        data_category_ids=[c.id for c in categories],
        processing_activity_ids=[a.id for a in activities],
        consent_text=purpose.versions[0].consent_text, is_current=True, created_by="test",
    )
    db.add(version)
    purpose.current_version = number
    db.commit()
    db.refresh(purpose)
    return version


def _granted(db, customer, purpose, category, activity, source_app):
    from app.services import consent as consent_service

    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=source_app, exact_source=True)
    return consent_service.grant_consent(db, consent, source_app=source_app)


def _evaluate(db, customer, purpose, category, activity, source_app="SCOPE_TEST"):
    from app.services.decision_engine import evaluate_decision

    return evaluate_decision(db, customer, purpose, category, activity, source_app=source_app)


# =========================================================================== #
#  L-02: the purpose's itemised scope binds the decision
# =========================================================================== #
def test_a_purpose_that_itemises_nothing_allows_nothing(db):
    """The reproduction, exactly: empty `data_category_ids` and empty
    `processing_activity_ids`, an active consent, and an unrelated category and
    activity. It used to answer ALLOW."""
    category, activity = _codes(db, "l02_empty")
    purpose = _purpose(db, "l02_empty")  # itemises neither
    customer = _customer(db, "L02-EMPTY-1", "SCOPE_TEST")
    _granted(db, customer, purpose, category, activity, "SCOPE_TEST")

    decision = _evaluate(db, customer, purpose, category, activity)

    assert decision.decision == "DENY"
    assert decision.allowed is False
    assert category.code in decision.reason
    assert activity.code in decision.reason
    assert "s.6(1)" in decision.reason and "R.3(b)(i)" in decision.reason


def test_an_in_scope_request_still_allows(db):
    """The guard must be additive: a request inside the itemisation is
    untouched."""
    category, activity = _codes(db, "l02_inscope")
    purpose = _purpose(db, "l02_inscope", categories=[category], activities=[activity])
    customer = _customer(db, "L02-IN-1", "SCOPE_TEST")
    _granted(db, customer, purpose, category, activity, "SCOPE_TEST")

    decision = _evaluate(db, customer, purpose, category, activity)

    assert decision.decision == "ALLOW"
    assert decision.allowed is True


def test_an_out_of_scope_data_category_is_denied_and_named(db):
    category, activity = _codes(db, "l02_cat")
    other_category = DataCategory(name="L02 biometric", code="cat_l02_biometric")
    db.add(other_category)
    db.commit()
    purpose = _purpose(db, "l02_cat", categories=[category], activities=[activity])
    customer = _customer(db, "L02-CAT-1", "SCOPE_TEST")
    _granted(db, customer, purpose, other_category, activity, "SCOPE_TEST")

    decision = _evaluate(db, customer, purpose, other_category, activity)

    assert decision.decision == "DENY"
    assert "cat_l02_biometric" in decision.reason
    # The in-scope activity must not be reported as the problem.
    assert "act_l02_cat" not in decision.reason


def test_an_out_of_scope_processing_activity_is_denied_and_named(db):
    category, activity = _codes(db, "l02_act")
    other_activity = ProcessingActivity(name="L02 profiling", code="act_l02_profiling")
    db.add(other_activity)
    db.commit()
    purpose = _purpose(db, "l02_act", categories=[category], activities=[activity])
    customer = _customer(db, "L02-ACT-1", "SCOPE_TEST")
    _granted(db, customer, purpose, category, other_activity, "SCOPE_TEST")

    decision = _evaluate(db, customer, purpose, category, other_activity)

    assert decision.decision == "DENY"
    assert "act_l02_profiling" in decision.reason
    assert "cat_l02_act" not in decision.reason


def test_an_out_of_scope_request_is_deny_not_require_consent(db):
    """With no consent at all the engine used to fall through to
    REQUIRE_CONSENT, which reads as "ask her and you may proceed". Out of scope
    is not a consent that has yet to be collected - collecting it would not
    make the processing lawful."""
    category, activity = _codes(db, "l02_noconsent")
    other_category = DataCategory(name="L02 genetic", code="cat_l02_genetic")
    db.add(other_category)
    db.commit()
    purpose = _purpose(db, "l02_noconsent", categories=[category], activities=[activity])
    customer = _customer(db, "L02-NC-1", "SCOPE_TEST")

    decision = _evaluate(db, customer, purpose, other_category, activity)

    assert decision.decision == "DENY"


def test_scope_binds_even_when_the_purpose_needs_no_consent(db):
    """`purpose.requires_consent is False` is an ALLOW branch further down the
    engine. Purpose limitation is not a consent-only rule, so an s.7 purpose
    cannot process data its own version does not itemise either."""
    category, activity = _codes(db, "l02_s7")
    other_category = DataCategory(name="L02 financial", code="cat_l02_financial")
    db.add(other_category)
    db.commit()
    purpose = _purpose(db, "l02_s7", categories=[category], activities=[activity],
                       legal_basis="S7_A", requires_consent=False)
    customer = _customer(db, "L02-S7-1", "SCOPE_TEST")

    assert _evaluate(db, customer, purpose, category, activity).decision == "ALLOW"
    assert _evaluate(db, customer, purpose, other_category, activity).decision == "DENY"


def test_the_version_the_consent_pinned_binds_not_the_current_one_widening(db):
    """A fiduciary must not be able to widen an existing consent by publishing
    a new purpose version. v1 itemises one category; v2 adds a second; the
    consent is still pinned to v1, so the second category is out of scope."""
    category, activity = _codes(db, "l02_widen")
    added = DataCategory(name="L02 location", code="cat_l02_location")
    db.add(added)
    db.commit()
    purpose = _purpose(db, "l02_widen", categories=[category], activities=[activity])
    customer = _customer(db, "L02-WIDEN-1", "SCOPE_TEST")
    consent = _granted(db, customer, purpose, added, activity, "SCOPE_TEST")
    pinned_version_id = consent.purpose_version_id

    _new_purpose_version(db, purpose, categories=[category, added], activities=[activity])
    db.refresh(consent)
    assert consent.purpose_version_id == pinned_version_id, "the consent must stay pinned to v1"

    decision = _evaluate(db, customer, purpose, added, activity)
    assert decision.decision == "DENY"
    assert "version 1" in decision.reason
    assert "the purpose version this consent was given under" in decision.reason


def test_the_version_the_consent_pinned_binds_not_the_current_one_narrowing(db):
    """The same rule in the other direction, which is what proves it is the
    PINNED version being read rather than the current one: v1 itemised two
    categories, v2 drops one, and the consent given under v1 still covers what
    v1 itemised. (The proper way to shrink an existing consent is R1-09's
    material-change re-consent, not a silent scope change.)"""
    category, activity = _codes(db, "l02_narrow")
    dropped = DataCategory(name="L02 device", code="cat_l02_device")
    db.add(dropped)
    db.commit()
    purpose = _purpose(db, "l02_narrow", categories=[category, dropped], activities=[activity])
    customer = _customer(db, "L02-NARROW-1", "SCOPE_TEST")
    _granted(db, customer, purpose, dropped, activity, "SCOPE_TEST")

    _new_purpose_version(db, purpose, categories=[category], activities=[activity])

    decision = _evaluate(db, customer, purpose, dropped, activity)
    assert decision.decision == "ALLOW", decision.reason


def test_with_no_consent_the_current_version_binds(db):
    """Nothing is pinned when there is no consent, so the itemisation in force
    now is the honest reference - the one a fresh notice would show."""
    category, activity = _codes(db, "l02_current")
    later = DataCategory(name="L02 contact", code="cat_l02_contact")
    db.add(later)
    db.commit()
    purpose = _purpose(db, "l02_current", categories=[category], activities=[activity])
    customer = _customer(db, "L02-CURRENT-1", "SCOPE_TEST")

    assert _evaluate(db, customer, purpose, later, activity).decision == "DENY"

    _new_purpose_version(db, purpose, categories=[category, later], activities=[activity])

    after = _evaluate(db, customer, purpose, later, activity)
    assert after.decision == "REQUIRE_CONSENT", after.reason
    assert "the purpose version currently in force" not in after.reason


def test_an_explicit_policy_deny_still_wins_over_the_scope_check(db):
    """Precedence is documented in docs/ARCHITECTURE.md and the scope check was inserted
    AFTER the explicit policy-rule DENY, not before it. Both answers are DENY;
    the reason recorded in the ledger must be the policy's."""
    category, activity = _codes(db, "l02_policy")
    other_category = DataCategory(name="L02 health", code="cat_l02_health")
    db.add(other_category)
    db.commit()
    purpose = _purpose(db, "l02_policy", categories=[category], activities=[activity])
    customer = _customer(db, "L02-POL-1", "SCOPE_TEST")

    # `get_active_policy` returns the lowest-id ACTIVE policy that has a
    # current version, and this database is shared by the whole session, so
    # stand any other active policy down for the length of this test and put
    # it back afterwards - otherwise which policy the engine consults depends
    # on what ran before.
    incumbents = db.query(Policy).filter(Policy.is_active.is_(True)).all()
    for row in incumbents:
        row.is_active = False
    policy = Policy(name="Scope precedence policy", code="pol_l02_scope",
                    status="ACTIVE", is_active=True, current_version=1)
    db.add(policy)
    db.flush()
    db.add(PolicyVersion(
        policy_id=policy.id, version_number=1, is_current=True,
        rules=[{"purpose_code": purpose.code, "data_category_code": other_category.code,
                "processing_activity_code": activity.code, "decision": "DENY"}],
    ))
    db.commit()
    try:
        decision = _evaluate(db, customer, purpose, other_category, activity)
        assert decision.decision == "DENY"
        assert "pol_l02_scope" in decision.reason
        assert "explicitly denies" in decision.reason
        assert "R.3(b)(i)" not in decision.reason
    finally:
        # Leave no globally-active policy behind: get_active_policy is
        # process-wide and every other test in the session shares this
        # database.
        policy.is_active = False
        policy.status = "RETIRED"
        for row in incumbents:
            row.is_active = True
        db.commit()


def test_the_scope_violation_is_recorded_on_the_decision_log(db):
    category, activity = _codes(db, "l02_log")
    other_category = DataCategory(name="L02 passport", code="cat_l02_passport")
    db.add(other_category)
    db.commit()
    purpose = _purpose(db, "l02_log", categories=[category], activities=[activity])
    customer = _customer(db, "L02-LOG-1", "SCOPE_TEST")

    _evaluate(db, customer, purpose, other_category, activity)

    log = (
        db.query(ConsentDecisionLog)
        .filter(ConsentDecisionLog.customer_id == customer.id,
                ConsentDecisionLog.purpose_id == purpose.id)
        .order_by(ConsentDecisionLog.id.desc())
        .first()
    )
    assert log.decision == "DENY"
    assert log.details["purpose_scope_violation"] is True
    assert log.details["out_of_scope_data_category_code"] == "cat_l02_passport"
    assert log.details["out_of_scope_processing_activity_code"] is None
    assert log.details["binding_purpose_version_source"] == "current"


def test_out_of_scope_is_denied_over_http(db, client):
    """The same finding through the published door, POST /decisions/evaluate."""
    org = _org(db, "SCOPE_HTTP")
    key = _api_key(db, org, ["integration.write", "decision.evaluate"])
    category, activity = _codes(db, "l02_http")
    other_category = DataCategory(name="L02 biometric http", code="cat_l02_http_bio")
    db.add(other_category)
    db.commit()
    purpose = _purpose(db, "l02_http", categories=[category], activities=[activity])
    customer = _customer(db, "L02-HTTP-1", "SCOPE_HTTP")
    _granted(db, customer, purpose, other_category, activity, "SCOPE_HTTP")

    resp = client.post("/decisions/evaluate", headers={"X-API-Key": key}, json={
        "customer_id": customer.external_id,
        "purpose_code": purpose.code,
        "data_category_code": other_category.code,
        "processing_activity_code": activity.code,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["allowed"] is False
    assert "cat_l02_http_bio" in body["reason"]

    in_scope = client.post("/decisions/evaluate", headers={"X-API-Key": key}, json={
        "customer_id": customer.external_id,
        "purpose_code": purpose.code,
        "data_category_code": category.code,
        "processing_activity_code": activity.code,
    })
    assert in_scope.json()["decision"] == "REQUIRE_CONSENT", in_scope.text


# =========================================================================== #
#  Q-07: a server-observed GPC objection is acted on
# =========================================================================== #
def _cookie_purpose(db, code, *, legal_basis="CONSENT", requires_consent=True):
    """The CRM banner only touches purposes whose code is one of
    COOKIE_CATEGORY_TO_PURPOSE's values."""
    existing = db.query(Purpose).filter(Purpose.code == code).first()
    if existing:
        return existing
    category, activity = _codes(db, f"gpc_{code}")
    return _purpose(db, code, categories=[category], activities=[activity],
                    legal_basis=legal_basis, requires_consent=requires_consent)


def _crm_customer(db, email):
    row = CrmCustomer(name="GPC principal", email=email, email_search=hmac_digest(email))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _banner(client, crm_customer, categories, *, headers=None, context=None):
    return client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        headers=headers or {},
        json={"lang": "en", "categories": categories,
              "context": context or {"affirmative_action": "CLICK"}},
    )


def _consent_for(db, email, purpose):
    from app.services.tenancy import resolve_customer

    customer = resolve_customer(db, source_app="CRM_PORTAL", email=email)
    assert customer is not None
    return (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.purpose_id == purpose.id)
        .order_by(Consent.id.desc())
        .first()
    )


def test_a_grant_carrying_a_gpc_objection_is_recorded_as_denied(db, client):
    """The reproduction: Sec-GPC: 1 together with analytics:true used to
    produce an ACTIVE analytics consent."""
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-enforced@example.com"
    crm = _crm_customer(db, email)

    resp = _banner(client, crm, {"analytics": True}, headers={"Sec-GPC": "1"})
    assert resp.status_code == 200, resp.text

    consent = _consent_for(db, email, purpose)
    assert consent.status == "DENIED"
    assert consent.granted_at is None


def test_the_refusal_is_visible_in_evidence(db, client):
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-evidence@example.com"
    crm = _crm_customer(db, email)

    _banner(client, crm, {"analytics": True}, headers={"Sec-GPC": "1"})

    consent = _consent_for(db, email, purpose)
    evidence = (
        db.query(ConsentEvidence)
        .filter(ConsentEvidence.consent_id == consent.id)
        .order_by(ConsentEvidence.id.desc())
        .first()
    )
    assert evidence is not None, "a refusal that leaves no evidence is not a record"
    assert evidence.gpc_signal is True
    assert evidence.details["gpc_objection_enforced"] is True
    assert evidence.details["refused_action"] == "GRANT"
    assert evidence.details["status_after_refusal"] == "DENIED"


def test_the_refusal_is_visible_in_audit(db, client):
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-audit@example.com"
    crm = _crm_customer(db, email)

    _banner(client, crm, {"analytics": True}, headers={"Sec-GPC": "1"})

    consent = _consent_for(db, email, purpose)
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event == "GPC_OBJECTION_ENFORCED", AuditLog.consent_id == consent.id)
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.decision == "DENY"
    assert "Sec-GPC" in row.reason
    assert row.details["gpc_signal"] is True
    assert row.details["denied_recorded"] is True

    from app.models.entities import AUDIT_EVENTS

    assert "GPC_OBJECTION_ENFORCED" in AUDIT_EVENTS, "the audit filter must be able to offer it"


def test_without_the_header_the_same_request_still_grants(db, client):
    """The guard is tri-state on the server-observed header: absent means
    absent, and every existing caller must be unaffected."""
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-absent@example.com"
    crm = _crm_customer(db, email)

    assert _banner(client, crm, {"analytics": True}).status_code == 200

    consent = _consent_for(db, email, purpose)
    assert consent.status == "ACTIVE"


def test_a_sec_gpc_header_that_is_not_one_still_grants(db, client):
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-zero@example.com"
    crm = _crm_customer(db, email)

    assert _banner(client, crm, {"analytics": True}, headers={"Sec-GPC": "0"}).status_code == 200

    consent = _consent_for(db, email, purpose)
    assert consent.status == "ACTIVE"


def test_a_client_claimed_gpc_signal_cannot_force_a_refusal(db, client):
    """`ClientContext.gpc_signal` is whatever the request BODY asserts. Only the
    server-observed header decides; the claim is kept as a secondary signal.
    If a claim could force an outcome, a client could manufacture denials for a
    principal - the mirror image of the bug being fixed."""
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-claimed@example.com"
    crm = _crm_customer(db, email)

    resp = _banner(client, crm, {"analytics": True},
                   context={"affirmative_action": "CLICK", "gpc_signal": True})
    assert resp.status_code == 200, resp.text

    consent = _consent_for(db, email, purpose)
    assert consent.status == "ACTIVE", "a body-claimed GPC signal must not decide anything"
    evidence = (
        db.query(ConsentEvidence)
        .filter(ConsentEvidence.consent_id == consent.id)
        .order_by(ConsentEvidence.id.desc())
        .first()
    )
    assert evidence.details["claimed_gpc_signal"] is True
    assert evidence.gpc_signal is None
    assert "gpc_objection_enforced" not in evidence.details


def test_a_strictly_necessary_purpose_is_unaffected_by_gpc(db, client):
    """The seeded `strictly_necessary` purpose is s.7(a) with
    requires_consent=False - login, session security and keeping a record of
    the principal's own consent choices. There is no consent for a GPC
    objection to withhold, and blocking it would break the service rather than
    protect anybody."""
    necessary = _cookie_purpose(db, "strictly_necessary",
                                legal_basis="S7_A", requires_consent=False)
    optional = _cookie_purpose(db, "advertising")
    email = "gpc-necessary@example.com"
    crm = _crm_customer(db, email)

    resp = _banner(client, crm, {"necessary": True, "advertising": True},
                   headers={"Sec-GPC": "1"})
    assert resp.status_code == 200, resp.text

    assert _consent_for(db, email, necessary).status == "ACTIVE"
    assert _consent_for(db, email, optional).status == "DENIED"


def test_the_decision_engine_refuses_after_a_gpc_refused_grant(db, client):
    """End to end: the refusal is not just a status, it changes the answer the
    engine gives about processing."""
    purpose = _cookie_purpose(db, "analytics")
    email = "gpc-decision@example.com"
    crm = _crm_customer(db, email)

    _banner(client, crm, {"analytics": True}, headers={"Sec-GPC": "1"})

    consent = _consent_for(db, email, purpose)
    from app.services.decision_engine import evaluate_decision

    decision = evaluate_decision(
        db, consent.customer, consent.purpose, consent.data_category,
        consent.processing_activity, source_app=consent.source_app,
    )
    assert decision.decision == "DENY"
    assert decision.allowed is False


def test_the_portal_reports_the_refusal_instead_of_claiming_a_grant(db, client):
    """The portal used to count every consent it touched as granted
    (`activate_consent` always returns the row, so `if activated:` was always
    true). Under GPC that would tell the principal her consent was recorded
    while the server denied it - the same disagreement, one layer up."""
    from app.core.security import create_context_token

    category, activity = _codes(db, "gpc_portal")
    purpose = _purpose(db, "gpc_portal_purpose", categories=[category], activities=[activity])
    customer = _customer(db, "GPC-PORTAL-1", "GPC_PORTAL")
    token = create_context_token(customer.id, "GPC_PORTAL")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="GPC_PORTAL",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token, "Sec-GPC": "1"},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["affected"] == 0
    assert "Global Privacy Control" in body["message"]
    # `action` was hard-coded to "granted", so this response used to say
    # `action="granted", affected=0` with a message explaining the refusal -
    # the machine-readable field contradicting both the prose and the row the
    # server actually wrote. A caller reading the field rather than the message
    # would conclude consent was given. The rows are written DENIED, which is
    # the same word /portal/deny returns for the same recorded outcome.
    assert body["action"] == "denied"

    consent = (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.purpose_id == purpose.id)
        .first()
    )
    assert consent.status == "DENIED"


def test_the_portal_still_reports_a_real_grant_as_granted(db, client):
    """The control for the assertion above: `action` reports what the ledger
    records, so an ordinary grant must still come back "granted". Reporting
    every outcome as a refusal would be the same defect mirrored."""
    from app.core.security import create_context_token

    category, activity = _codes(db, "grant_label")
    purpose = _purpose(db, "grant_label_purpose", categories=[category], activities=[activity])
    customer = _customer(db, "GRANT-LABEL-1", "GRANT_LABEL")
    token = create_context_token(customer.id, "GRANT_LABEL")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="GRANT_LABEL",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "granted"
    assert body["affected"] >= 1
    assert "Global Privacy Control" not in body["message"]


def test_gpc_never_rewrites_a_status_it_may_not_reach(db):
    """DENIED is not a legal transition out of WITHDRAWN. A GPC objection must
    block the grant without overwriting the principal's own withdrawal with a
    system denial - and it must still evidence and audit the refusal."""
    from app.services import consent as consent_service

    category, activity = _codes(db, "gpc_withdrawn")
    purpose = _purpose(db, "gpc_withdrawn_purpose", categories=[category], activities=[activity])
    customer = _customer(db, "GPC-WD-1", "GPC_WD")
    consent = _granted(db, customer, purpose, category, activity, "GPC_WD")
    consent_service.withdraw_consent(db, consent, source_app="GPC_WD")
    assert consent.status == "WITHDRAWN"

    before = db.query(ConsentEvidence).filter(ConsentEvidence.consent_id == consent.id).count()
    consent_service.grant_consent(db, consent, source_app="GPC_WD", gpc_signal=True)

    assert consent.status == "WITHDRAWN", "the principal's own withdrawal must stand"
    # Ordered explicitly. This used to be a bare `.all()`, which returns rows
    # in whatever order Postgres happens to produce them - fine while
    # `consent_evidence` was small, wrong as soon as the table grew enough for
    # the plan to change, and it started picking an earlier row with
    # gpc_signal=None. The id sequence is the only thing that actually orders
    # these.
    after = (
        db.query(ConsentEvidence)
        .filter(ConsentEvidence.consent_id == consent.id)
        .order_by(ConsentEvidence.id)
        .all()
    )
    assert len(after) == before + 1
    assert after[-1].gpc_signal is True
    assert after[-1].details["gpc_objection_enforced"] is True
    assert after[-1].details["status_after_refusal"] == "WITHDRAWN"
    assert db.query(AuditLog).filter(
        AuditLog.event == "GPC_OBJECTION_ENFORCED", AuditLog.consent_id == consent.id
    ).count() == 1


def test_a_renewal_carrying_a_gpc_objection_is_refused_too(db):
    """A renewal is itself a fresh affirmative act - it is the door the portal
    uses to re-affirm a consent flagged by a material change - so an objection
    carried by the renewing request blocks it exactly as it blocks a grant, and
    `re_consent_required` must NOT be cleared."""
    from app.services import consent as consent_service

    category, activity = _codes(db, "gpc_renew")
    purpose = _purpose(db, "gpc_renew_purpose", categories=[category], activities=[activity])
    customer = _customer(db, "GPC-RENEW-1", "GPC_RENEW")
    consent = _granted(db, customer, purpose, category, activity, "GPC_RENEW")
    consent.re_consent_required = True
    consent.status = "UPDATED"
    db.commit()
    version_before = consent.consent_version

    consent_service.renew_consent(db, consent, source_app="GPC_RENEW", gpc_signal=True)

    assert consent.status == "UPDATED", "the renewal must not have happened"
    assert consent.consent_version == version_before
    assert consent.re_consent_required is True, "only a real re-consent lifts the block"

    evidence = (
        db.query(ConsentEvidence)
        .filter(ConsentEvidence.consent_id == consent.id)
        .order_by(ConsentEvidence.id.desc())
        .first()
    )
    assert evidence.gpc_signal is True
    assert evidence.details["refused_action"] == "RENEW"


@pytest.mark.parametrize("signal", [None, False])
def test_no_objection_means_no_gpc_row_anywhere(db, signal):
    """Nothing about the guard fires for the callers that send no header - the
    property that makes it safe to put at a choke point every grant path
    shares."""
    from app.services import consent as consent_service

    category, activity = _codes(db, f"gpc_none_{signal}")
    purpose = _purpose(db, f"gpc_none_purpose_{signal}",
                       categories=[category], activities=[activity])
    customer = _customer(db, f"GPC-NONE-{signal}", "GPC_NONE")
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="GPC_NONE", exact_source=True)
    consent_service.grant_consent(db, consent, source_app="GPC_NONE", gpc_signal=signal)

    assert consent.status == "GRANTED"
    assert db.query(AuditLog).filter(
        AuditLog.event == "GPC_OBJECTION_ENFORCED", AuditLog.consent_id == consent.id
    ).count() == 0
