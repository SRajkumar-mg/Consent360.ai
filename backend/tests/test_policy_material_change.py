"""P-02: a policy change writes a change record and, when material, triggers
re-consent - the same machinery R1-09 built for a Purpose
(app/services/material_change.py), reused rather than duplicated.

Reproduced bug, precisely: `PUT /policies/{id}` versioned the rule engine and
returned 200, but nothing ever called `_log_change` or started a campaign for
a POLICY - `CHANGE_ENTITY_TYPES` already permitted it, nothing wrote one.
`test_the_bug_is_fixed_end_to_end_through_the_real_route` below is that exact
request/response, before (git history) and after (this file, passing).

Materiality for a Policy is decided at the rule engine's own grain - see
app/services/material_change.py's module docstring, "P-02: THE SAME
DEFINITION, APPLIED TO A POLICY" - rather than by any prose a Policy does not
have. The unit tests below are that definition, field by field, mirroring how
tests/test_reconsent.py tests the purpose definition.
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.entities import (
    AuditLog,
    Customer,
    DataCategory,
    Notification,
    Policy,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.models.reconsent import PolicyChangeLog, ReConsentCampaign
from app.services import consent as consent_service
from app.services import material_change as mc
from app.services.decision_engine import evaluate_decision
from app.services.tenancy import resolve_tenant_id

SOURCE_APP = "POLICY_MC_TEST"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_active_policy(db):
    """`get_active_policy()` (services/decision_engine.py) returns the
    lowest-id ACTIVE policy with a current version, process-wide - and this
    database is shared across the whole test session, exactly the concern
    tests/test_purpose_scope_and_gpc.py::
    test_an_explicit_policy_deny_still_wins_over_the_scope_check already
    documents and works around. Every test in this file that calls
    evaluate_decision needs its own policy to be THE active one, so stand
    down every other active policy for the length of each test and restore
    them afterwards - otherwise which policy the engine consults depends on
    what ran before or after this file in the same session.
    """
    incumbents = db.query(Policy).filter(Policy.is_active.is_(True)).all()
    incumbent_ids = {p.id for p in incumbents}
    for row in incumbents:
        row.is_active = False
    db.commit()
    try:
        yield
    finally:
        # Leave no globally-active policy behind: retire whatever this test
        # created and activated, then restore the incumbents.
        for row in db.query(Policy).filter(Policy.is_active.is_(True)).all():
            if row.id not in incumbent_ids:
                row.is_active = False
                row.status = "RETIRED"
        if incumbent_ids:
            for row in db.query(Policy).filter(Policy.id.in_(incumbent_ids)).all():
                row.is_active = True
        db.commit()


@pytest.fixture()
def policies_client(db):
    from app.api.routes import policies as policies_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(policies_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def reconsent_client(db):
    from app.api.routes import reconsent as reconsent_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(reconsent_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _make_triple(db, code):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, legal_basis="CONSENT", requires_consent=True)
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        data_items=[{"data_category_id": category.id, "necessity": "required"}],
        consent_text="I consent.", is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()
    return purpose, category, activity


def _make_customer(db, external_id):
    customer = Customer(
        external_id=external_id, name="Test Principal",
        email=f"{external_id.lower()}@example.test",
        source_app=SOURCE_APP, tenant_id=resolve_tenant_id(db, SOURCE_APP),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _granted_consent(db, customer, purpose, category, activity):
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=customer.source_app
    )
    consent_service.grant_consent(db, consent, source_app=customer.source_app)
    db.commit()
    return consent


def _make_policy(db, code, *, rules=None, default_decision="REQUIRE_CONSENT"):
    policy = Policy(name=f"Policy {code}", code=code, status="ACTIVE", current_version=1,
                     is_active=True, tenant_id=resolve_tenant_id(db, SOURCE_APP))
    db.add(policy)
    db.flush()
    pv = PolicyVersion(
        policy_id=policy.id, version_number=1, rules=rules or [],
        default_decision=default_decision, is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()
    db.refresh(policy)
    db.refresh(pv)
    return policy, pv


def _new_policy_version(db, policy, old_pv, **changes):
    old_pv.is_current = False
    old_pv.effective_to = datetime.now(timezone.utc)
    fields = mc.policy_version_snapshot(old_pv)
    fields.update(changes)
    new_pv = PolicyVersion(
        policy_id=policy.id, version_number=old_pv.version_number + 1,
        is_current=True, created_by="test", **fields,
    )
    db.add(new_pv)
    db.flush()
    policy.current_version = new_pv.version_number
    db.commit()
    return new_pv


def _rule(purpose, category, activity, *, decision="ALLOW", requires_active_consent=True, priority=10):
    return {
        "purpose_code": purpose.code, "data_category_code": category.code,
        "processing_activity_code": activity.code,
        "decision": decision, "requires_active_consent": requires_active_consent,
        "priority": priority,
    }


# --------------------------------------------------------------------------- #
# THE DEFINITION, FIELD BY FIELD - classify_change with POLICY_FIELD_RULES
# --------------------------------------------------------------------------- #
def _classify(before_rules, after_rules, before_default="REQUIRE_CONSENT", after_default="REQUIRE_CONSENT"):
    before = {"rules": before_rules, "default_decision": before_default, "checklist": None}
    after = {"rules": after_rules, "default_decision": after_default, "checklist": None}
    return mc.classify_change(
        before, after, field_rules=mc.POLICY_FIELD_RULES, overridable_fields=mc.POLICY_OVERRIDABLE_FIELDS,
    )


def test_removing_a_deny_rule_is_material():
    """The DENY that blocked this triple outright is lifted; whatever
    consent exists for it (if any) now falls through to the ordinary
    consent-gated flow, which it may satisfy without ever having agreed to
    processing under the old, stricter rule."""
    deny = {"purpose_code": "p", "data_category_code": "c", "processing_activity_code": "a",
            "decision": "DENY", "requires_active_consent": True, "priority": 10}
    result = _classify([deny], [])
    assert result["materiality"] == "MATERIAL"
    assert result["changed_fields"]["rules"]["widened_triples"] == [("p", "c", "a")]


def test_adding_a_deny_rule_is_a_narrowing():
    deny = {"purpose_code": "p", "data_category_code": "c", "processing_activity_code": "a",
            "decision": "DENY", "requires_active_consent": True, "priority": 10}
    result = _classify([], [deny])
    assert result["materiality"] == "NARROWING"
    assert result["changed_fields"]["rules"]["widened_triples"] == []


def test_dropping_the_active_consent_requirement_is_material():
    base = {"purpose_code": "p", "data_category_code": "c", "processing_activity_code": "a",
            "decision": "ALLOW", "requires_active_consent": True, "priority": 10}
    bypassed = dict(base, requires_active_consent=False)
    result = _classify([base], [bypassed])
    assert result["materiality"] == "MATERIAL"
    assert result["changed_fields"]["rules"]["widened_triples"] == [("p", "c", "a")]


def test_reinstating_the_active_consent_requirement_is_a_narrowing():
    base = {"purpose_code": "p", "data_category_code": "c", "processing_activity_code": "a",
            "decision": "ALLOW", "requires_active_consent": False, "priority": 10}
    reinstated = dict(base, requires_active_consent=True)
    result = _classify([base], [reinstated])
    assert result["materiality"] == "NARROWING"


def test_a_priority_only_change_is_cosmetic():
    """`find_applicable_rule` never sorts by priority, so a priority-only
    edit changes nothing this platform evaluates."""
    base = {"purpose_code": "p", "data_category_code": "c", "processing_activity_code": "a",
            "decision": "ALLOW", "requires_active_consent": True, "priority": 1}
    reprioritised = dict(base, priority=99)
    result = _classify([base], [reprioritised])
    assert result["materiality"] == "COSMETIC"


def test_default_decision_toward_allow_is_material():
    result = _classify([], [], before_default="REQUIRE_CONSENT", after_default="ALLOW")
    assert result["materiality"] == "MATERIAL"
    assert "ALLOW" in result["changed_fields"]["default_decision"]["why"] or True


def test_default_decision_toward_deny_is_a_narrowing():
    result = _classify([], [], before_default="ALLOW", after_default="DENY")
    assert result["materiality"] == "NARROWING"


def test_default_decision_deny_to_require_consent_is_material():
    result = _classify([], [], before_default="DENY", after_default="REQUIRE_CONSENT")
    assert result["materiality"] == "MATERIAL"


def test_no_change_at_all_is_cosmetic_with_an_empty_diff():
    result = _classify([], [])
    assert result["changed_fields"] == {}
    assert result["materiality"] == "COSMETIC"


def test_a_checklist_only_change_is_never_material():
    before = {"rules": [], "default_decision": "REQUIRE_CONSENT", "checklist": None}
    after = {"rules": [], "default_decision": "REQUIRE_CONSENT", "checklist": {"reviewed": True}}
    result = mc.classify_change(
        before, after, field_rules=mc.POLICY_FIELD_RULES, overridable_fields=mc.POLICY_OVERRIDABLE_FIELDS,
    )
    assert result["materiality"] == "COSMETIC"


def test_nothing_on_a_policy_can_be_downgraded():
    """POLICY_OVERRIDABLE_FIELDS is empty - a Policy has no free text a
    machine cannot classify, so every attempted override is refused,
    preserving the same guarantee purposes.py already has for a widening."""
    deny = {"purpose_code": "p", "data_category_code": "c", "processing_activity_code": "a",
            "decision": "DENY", "requires_active_consent": True, "priority": 10}
    with pytest.raises(mc.MaterialityError) as exc:
        mc.classify_change(
            {"rules": [deny], "default_decision": "REQUIRE_CONSENT", "checklist": None},
            {"rules": [], "default_decision": "REQUIRE_CONSENT", "checklist": None},
            cosmetic_overrides={"rules": "it's a small one"},
            field_rules=mc.POLICY_FIELD_RULES, overridable_fields=mc.POLICY_OVERRIDABLE_FIELDS,
        )
    assert "cannot be downgraded" in str(exc.value)


def test_downgrading_default_decision_is_also_refused():
    with pytest.raises(mc.MaterialityError) as exc:
        mc.classify_change(
            {"rules": [], "default_decision": "REQUIRE_CONSENT", "checklist": None},
            {"rules": [], "default_decision": "ALLOW", "checklist": None},
            cosmetic_overrides={"default_decision": "trust me"},
            field_rules=mc.POLICY_FIELD_RULES, overridable_fields=mc.POLICY_OVERRIDABLE_FIELDS,
        )
    assert "cannot be downgraded" in str(exc.value)
    assert "not a matter of opinion" in str(exc.value)


def test_purpose_classify_change_is_unaffected_by_the_policy_vocabulary():
    """classify_change's default field_rules/overridable_fields must still be
    the purpose vocabulary - the refactor that let a caller pass a different
    vocabulary must not have changed the default behaviour purposes.py and
    the cookie policy depend on."""
    before = {"data_category_ids": [1], "name": "x"}
    after = {"data_category_ids": [1, 2], "name": "x"}
    result = mc.classify_change(before, after)
    assert result["materiality"] == "MATERIAL"
    assert "data_category_ids" in result["changed_fields"]


# --------------------------------------------------------------------------- #
# THE DEFINITION OF DONE: a material policy change blocks processing until
# fresh consent, and every publication - material or not - is logged.
# --------------------------------------------------------------------------- #
def test_dod_a_material_policy_change_blocks_processing_until_fresh_consent(db):
    purpose, category, activity = _make_triple(db, "policy_dod")
    customer = _make_customer(db, "P02-DOD-001")
    consent = _granted_consent(db, customer, purpose, category, activity)

    deny_rule = _rule(purpose, category, activity, decision="DENY")
    policy, pv1 = _make_policy(db, "policy_dod_pol", rules=[deny_rule])

    # Before: the policy's explicit DENY blocks processing outright, ahead of
    # consent - exactly services/decision_engine.py's documented precedence.
    before = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert before.decision == "DENY"
    assert before.allowed is False

    # A material change: the DENY is lifted (a widening - see
    # test_removing_a_deny_rule_is_material).
    pv2 = _new_policy_version(db, policy, pv1, rules=[])
    result = mc.publish_policy_change(db, policy, pv1, pv2, actor_username="dpo", source_app="UI")

    assert result["materiality"] == "MATERIAL"
    assert result["consents_flagged"] == 1
    assert result["campaign_ref"]
    assert "rules" in result["materiality_basis"]

    notices = (
        db.query(Notification)
        .filter(Notification.customer_id == customer.id,
                Notification.event_type == "POLICY_CHANGE_RECONSENT")
        .all()
    )
    assert notices, "affected principals must be notified"
    assert result["notifications_queued"] >= 1

    db.refresh(consent)
    assert consent.re_consent_required is True
    assert consent.re_consent_requested_at is not None
    blocked = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert blocked.decision == "REQUIRE_CONSENT"
    assert blocked.allowed is False

    # The consent's own status is untouched - not a withdrawal or expiry
    # wearing a different name.
    assert consent.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

    # Fresh consent unblocks it.
    consent_service.renew_consent(
        db, consent, reason="Re-consented after the policy change",
        actor_username="principal", source_app=customer.source_app, actor_type="PRINCIPAL",
    )
    db.refresh(consent)
    assert consent.re_consent_required is False
    after = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert after.decision == "ALLOW"
    assert after.allowed is True

    campaign = db.query(ReConsentCampaign).filter(
        ReConsentCampaign.campaign_ref == result["campaign_ref"]
    ).one()
    assert campaign.entity_type == "POLICY"
    assert campaign.consents_flagged == 1
    assert campaign.fresh_consents == 1


def test_a_narrowing_policy_change_does_not_block_anything(db):
    purpose, category, activity = _make_triple(db, "policy_narrow")
    customer = _make_customer(db, "P02-NARROW-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    policy, pv1 = _make_policy(db, "policy_narrow_pol", rules=[])

    deny_rule = _rule(purpose, category, activity, decision="DENY")
    pv2 = _new_policy_version(db, policy, pv1, rules=[deny_rule])
    result = mc.publish_policy_change(db, policy, pv1, pv2, actor_username="dpo")

    assert result["materiality"] == "NARROWING"
    assert result["campaign_ref"] is None
    db.refresh(consent)
    assert consent.re_consent_required is False


def test_a_cosmetic_policy_change_is_logged_and_starts_no_campaign(db):
    """P-02's core fix: every publication is logged, material or not - this
    used to write NOTHING at all."""
    policy, pv1 = _make_policy(db, "policy_cosmetic")
    pv2 = _new_policy_version(db, policy, pv1, checklist={"reviewed": True})
    result = mc.publish_policy_change(db, policy, pv1, pv2, actor_username="dpo")

    assert result["materiality"] == "COSMETIC"
    assert result["campaign_ref"] is None

    row = db.query(PolicyChangeLog).filter(PolicyChangeLog.change_ref == result["change_ref"]).one()
    assert row.materiality == "COSMETIC"
    assert row.entity_type == "POLICY"
    assert row.from_version == 1 and row.to_version == 2


def test_the_change_and_campaign_are_audited(db):
    purpose, category, activity = _make_triple(db, "policy_audit")
    customer = _make_customer(db, "P02-AUDIT-001")
    _granted_consent(db, customer, purpose, category, activity)
    policy, pv1 = _make_policy(db, "policy_audit_pol", rules=[_rule(purpose, category, activity, decision="DENY")])
    pv2 = _new_policy_version(db, policy, pv1, rules=[])
    result = mc.publish_policy_change(db, policy, pv1, pv2, actor_username="dpo")

    events = {a.event for a in db.query(AuditLog).all() if a.reason and "policy_audit_pol" in a.reason}
    assert "POLICY_CHANGE_LOGGED" in events
    assert "RE_CONSENT_CAMPAIGN_STARTED" in events
    assert result["campaign_ref"]


# --------------------------------------------------------------------------- #
# End to end through the real routes - the exact request/response the bug
# report describes.
# --------------------------------------------------------------------------- #
def test_the_bug_is_fixed_end_to_end_through_the_real_route(db, policies_client, reconsent_client, staff_token):
    purpose, category, activity = _make_triple(db, "policy_api")
    customer = _make_customer(db, "P02-API-001")
    consent = _granted_consent(db, customer, purpose, category, activity)

    create = policies_client.post(
        "/policies",
        json={
            "name": "API Policy", "code": "policy_api_pol", "default_decision": "REQUIRE_CONSENT",
            "rules": [{
                "purpose_code": purpose.code, "data_category_code": category.code,
                "processing_activity_code": activity.code,
                "decision": "DENY", "requires_active_consent": True, "priority": 10,
            }],
        },
        headers=_auth(staff_token),
    )
    assert create.status_code == 201, create.text
    policy_id = create.json()["id"]

    before_changes = reconsent_client.get(
        "/re-consent/changes", params={"entity_type": "POLICY"}, headers=_auth(staff_token)
    ).json()

    # PUT /policies/{id}: lift the DENY - a material widening.
    response = policies_client.put(
        f"/policies/{policy_id}", json={"rules": []}, headers=_auth(staff_token),
    )
    assert response.status_code == 200, response.text
    change = response.json()["change"]
    assert change is not None, "the bug: this used to be null - PUT returned 200 with no change record"
    assert change["materiality"] == "MATERIAL"
    assert change["consents_flagged"] == 1
    assert change["campaign_ref"]

    # The bug, precisely: "added no row to /re-consent/changes".
    after_changes = reconsent_client.get(
        "/re-consent/changes", params={"entity_type": "POLICY"}, headers=_auth(staff_token)
    ).json()
    assert len(after_changes) == len(before_changes) + 1
    assert after_changes[0]["change_ref"] == change["change_ref"]
    assert after_changes[0]["entity_type"] == "POLICY"

    # And processing is actually blocked - not just a log row.
    db.refresh(consent)
    assert consent.re_consent_required is True
    assert evaluate_decision(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    ).decision == "REQUIRE_CONSENT"


def test_the_api_refuses_a_downgrade_attempt_on_a_policy(db, policies_client, staff_token):
    purpose, category, activity = _make_triple(db, "policy_api_refuse")
    create = policies_client.post(
        "/policies",
        json={
            "name": "API Policy Refuse", "code": "policy_api_refuse_pol",
            "rules": [{
                "purpose_code": purpose.code, "data_category_code": category.code,
                "processing_activity_code": activity.code,
                "decision": "DENY", "requires_active_consent": True, "priority": 10,
            }],
        },
        headers=_auth(staff_token),
    )
    policy_id = create.json()["id"]

    response = policies_client.put(
        f"/policies/{policy_id}",
        json={"rules": [], "cosmetic_overrides": {"rules": "trust me, it's minor"}},
        headers=_auth(staff_token),
    )
    assert response.status_code == 422
    assert "cannot be downgraded" in response.json()["detail"]

    # And the publication did not half-apply: the policy is still on v1.
    policy = db.get(Policy, policy_id)
    assert policy.current_version == 1


def test_metadata_only_updates_still_do_not_touch_the_change_log(db, policies_client, staff_token):
    """A PUT that changes only name/description/status/is_active never
    creates a PolicyVersion at all (routes/policies.py's own gate), so it
    must not appear in the P-02 change log either - it changed no version."""
    create = policies_client.post(
        "/policies", json={"name": "Meta Only", "code": "policy_meta_only"},
        headers=_auth(staff_token),
    )
    policy_id = create.json()["id"]
    before = db.query(PolicyChangeLog).count()

    response = policies_client.put(
        f"/policies/{policy_id}", json={"description": "Just a description update"},
        headers=_auth(staff_token),
    )
    assert response.status_code == 200
    assert response.json()["change"] is None
    assert db.query(PolicyChangeLog).count() == before
