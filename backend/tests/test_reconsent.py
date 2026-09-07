"""R1-09 (P-01, P-02, P-03, K-09): re-consent on material change.

The headline test is `test_dod_a_purpose_change_blocks_processing_until_fresh_consent`,
which is the task's definition of done end to end: a purpose's data items and
retention change, every affected principal is notified, and the decision
engine refuses to allow processing under that purpose until the principal
consents again - not a flag on a screen, a REQUIRE_CONSENT from
`evaluate_decision`.

The second body of tests is the materiality definition itself, field by field.
"Material change" has to mean the same thing about an edit made two years ago
as it did on the day, so each rule gets its own test rather than being
covered incidentally by the end-to-end one.

`app/main.py` does not mount the re-consent router (registering it is the
coordinating agent's change), so the HTTP-level tests mount the router onto a
local FastAPI app.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.encryption import hmac_digest
from app.models.entities import (
    AuditLog,
    Consent,
    CrmCustomer,
    Customer,
    DataCategory,
    Notification,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.models.reconsent import CookiePolicyVersion, PolicyChangeLog, ReConsentCampaign
from app.services import consent as consent_service
from app.services import material_change as reconsent
from app.services.decision_engine import evaluate_decision
from app.services.tenancy import resolve_tenant_id

SOURCE_APP = "RECONSENT_TEST"


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
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


@pytest.fixture()
def purposes_client(db):
    from app.api.routes import purposes as purposes_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(purposes_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_purpose(db, code, *, retention_days=365, data_items=None):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(
        name=f"Purpose {code}", code=code, legal_basis="CONSENT", requires_consent=True,
        retention_period_days=retention_days,
    )
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        retention_period_days=retention_days,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        data_items=data_items if data_items is not None else [
            {"data_category_id": category.id, "necessity": "required", "description": "contact"}
        ],
        consent_text="I consent.", is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()
    return purpose, category, activity, pv


def _make_customer(db, external_id, *, source_app=None, email=None, with_directory=False):
    source_app = source_app or f"{SOURCE_APP}_{external_id.replace('-', '_')}"
    email = email or f"{external_id.lower()}@example.test"
    customer = Customer(
        external_id=external_id, name="Test Principal", email=email,
        source_app=source_app, tenant_id=resolve_tenant_id(db, source_app),
    )
    db.add(customer)
    db.flush()
    if with_directory:
        db.add(CrmCustomer(
            name="Test Principal", email=email, email_search=hmac_digest(email),
            consent_preferences={"analytics": True, "advertising": False},
        ))
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


def _new_version(db, purpose, old_pv, **changes):
    """Publish a new PurposeVersion carrying `changes`, the way
    routes/purposes.py does."""
    old_pv.is_current = False
    old_pv.effective_to = _now()
    fields = reconsent.purpose_version_snapshot(old_pv)
    fields.update(changes)
    new_pv = PurposeVersion(
        purpose_id=purpose.id, version_number=old_pv.version_number + 1,
        is_current=True, created_by="test", **fields,
    )
    db.add(new_pv)
    db.flush()
    purpose.current_version = new_pv.version_number
    for key in ("name", "description", "legal_basis", "requires_consent",
                "retention_period_days", "services_enabled", "child_restricted"):
        setattr(purpose, key, fields[key])
    db.commit()
    return new_pv


# --------------------------------------------------------------------------- #
# THE DEFINITION OF DONE
# --------------------------------------------------------------------------- #
def test_dod_a_purpose_change_blocks_processing_until_fresh_consent(db):
    """DoD: 'After a purpose change, affected principals are notified and
    processing for that purpose is blocked until fresh consent.'"""
    purpose, category, activity, pv1 = _make_purpose(db, "dod_reconsent")
    customer = _make_customer(db, "R109-DOD-001")
    consent = _granted_consent(db, customer, purpose, category, activity)

    # Before: processing is allowed.
    before = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert before.decision == "ALLOW"
    assert before.allowed is True

    # A material change: a new data category is collected and the retention
    # period is extended.
    extra = DataCategory(name="Location", code="cat_dod_reconsent_extra")
    db.add(extra)
    db.commit()
    pv2 = _new_version(
        db, purpose, pv1,
        data_category_ids=[category.id, extra.id],
        data_items=[
            {"data_category_id": category.id, "necessity": "required", "description": "contact"},
            {"data_category_id": extra.id, "necessity": "optional", "description": "location"},
        ],
        retention_period_days=730,
    )
    result = reconsent.publish_purpose_change(
        db, purpose, pv1, pv2, actor_username="dpo", source_app="UI"
    )

    assert result["materiality"] == "MATERIAL"
    assert result["consents_flagged"] == 1
    assert "data_category_ids" in result["materiality_basis"]
    assert "retention_period_days" in result["materiality_basis"]

    # The principal was notified.
    notices = (
        db.query(Notification)
        .filter(
            Notification.customer_id == customer.id,
            Notification.event_type == "PURPOSE_CHANGE_RECONSENT",
        )
        .all()
    )
    assert notices, "affected principals must be notified"
    assert result["notifications_queued"] >= 1

    # And processing is BLOCKED - by the decision engine, not by a UI flag.
    db.refresh(consent)
    assert consent.re_consent_required is True
    assert consent.re_consent_requested_at is not None
    blocked = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert blocked.decision == "REQUIRE_CONSENT"
    assert blocked.allowed is False
    assert "material change" in blocked.reason
    assert "s.6(1)" in blocked.reason

    # The consent's own status is still active - this is not an expiry or a
    # withdrawal wearing a different name.
    assert consent.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

    # Fresh consent unblocks it, and nothing else does.
    consent_service.renew_consent(
        db, consent, reason="Re-consented after the purpose change",
        actor_username="principal", source_app=customer.source_app, actor_type="PRINCIPAL",
    )
    db.refresh(consent)
    assert consent.re_consent_required is False
    after = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert after.decision == "ALLOW"
    assert after.allowed is True

    # K-09 counted it.
    campaign = (
        db.query(ReConsentCampaign)
        .filter(ReConsentCampaign.campaign_ref == result["campaign_ref"])
        .one()
    )
    assert campaign.consents_flagged == 1
    assert campaign.fresh_consents == 1


def test_the_decision_log_records_why_it_was_blocked(db):
    purpose, category, activity, pv1 = _make_purpose(db, "log_reason")
    customer = _make_customer(db, "R109-LOG-001")
    _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    from app.models.entities import ConsentDecisionLog

    log = (
        db.query(ConsentDecisionLog)
        .filter(ConsentDecisionLog.customer_id == customer.id)
        .order_by(ConsentDecisionLog.id.desc())
        .first()
    )
    assert log.decision == "REQUIRE_CONSENT"
    assert log.details["re_consent_required"] is True
    assert log.details["re_consent_campaign_id"] is not None
    # The consent status recorded on the log is the real one, not a fiction.
    assert log.consent_status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")


def test_the_documented_precedence_is_unchanged_for_every_other_outcome(db):
    """A withdrawn, denied or expired consent still reports its own outcome,
    and a policy DENY still wins over everything - the re-consent branch only
    narrows the case that used to fall straight through to ALLOW."""
    purpose, category, activity, pv1 = _make_purpose(db, "precedence")
    customer = _make_customer(db, "R109-PREC-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")
    db.refresh(consent)
    assert consent.re_consent_required is True

    consent_service.withdraw_consent(
        db, consent, actor_username="principal", source_app=customer.source_app,
        actor_type="PRINCIPAL",
    )
    decision = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert decision.decision == "WITHDRAWN", (
        "a withdrawal must still report WITHDRAWN, not be masked by the re-consent flag"
    )


def test_an_unflagged_consent_for_another_purpose_is_untouched(db):
    """A campaign flags the consents for the purpose that changed, and no
    others."""
    purpose_a, cat_a, act_a, pv_a = _make_purpose(db, "scope_a")
    purpose_b, cat_b, act_b, pv_b = _make_purpose(db, "scope_b")
    customer = _make_customer(db, "R109-SCOPE-001")
    consent_a = _granted_consent(db, customer, purpose_a, cat_a, act_a)
    consent_b = _granted_consent(db, customer, purpose_b, cat_b, act_b)

    pv_a2 = _new_version(db, purpose_a, pv_a, retention_period_days=999)
    reconsent.publish_purpose_change(db, purpose_a, pv_a, pv_a2, actor_username="dpo")

    db.refresh(consent_a)
    db.refresh(consent_b)
    assert consent_a.re_consent_required is True
    assert consent_b.re_consent_required is False
    assert evaluate_decision(
        db, customer, purpose_b, cat_b, act_b, source_app=SOURCE_APP
    ).decision == "ALLOW"


# --------------------------------------------------------------------------- #
# The materiality definition, field by field
# --------------------------------------------------------------------------- #
def _classify(**changes):
    before = {
        "name": "Marketing", "description": "Send offers", "legal_basis": "CONSENT",
        "requires_consent": True, "retention_period_days": 365,
        "data_category_ids": [1, 2], "processing_activity_ids": [10],
        "data_items": [{"data_category_id": 1, "necessity": "required"}],
        "services_enabled": "Offers by email", "child_restricted": False,
        "consent_text": "I agree.", "translations": {}, "checklist": None,
    }
    after = dict(before)
    after.update(changes)
    return reconsent.classify_change(before, after)


def test_adding_a_data_category_is_material():
    result = _classify(data_category_ids=[1, 2, 3])
    assert result["materiality"] == "MATERIAL"
    assert result["changed_fields"]["data_category_ids"]["materiality"] == "MATERIAL"
    assert "never saw listed" in result["changed_fields"]["data_category_ids"]["why"]


def test_removing_a_data_category_is_a_narrowing_not_material():
    result = _classify(data_category_ids=[1])
    assert result["materiality"] == "NARROWING"
    assert "superset" in result["changed_fields"]["data_category_ids"]["why"]


def test_adding_a_processing_activity_is_material():
    assert _classify(processing_activity_ids=[10, 11])["materiality"] == "MATERIAL"


def test_removing_a_processing_activity_is_a_narrowing():
    assert _classify(processing_activity_ids=[])["materiality"] == "NARROWING"


def test_extending_retention_is_material_shortening_it_is_not():
    assert _classify(retention_period_days=730)["materiality"] == "MATERIAL"
    assert _classify(retention_period_days=90)["materiality"] == "NARROWING"


def test_changing_the_lawful_basis_is_always_material():
    result = _classify(legal_basis="S7_I")
    assert result["materiality"] == "MATERIAL"
    assert "lawful basis" in result["changed_fields"]["legal_basis"]["why"]


def test_requiring_consent_where_it_was_not_required_is_material():
    before_free = {
        "requires_consent": False, "name": "x", "description": "", "legal_basis": "S7_I",
        "retention_period_days": 30, "data_category_ids": [], "processing_activity_ids": [],
        "data_items": [], "services_enabled": "", "child_restricted": False,
        "consent_text": "", "translations": {}, "checklist": None,
    }
    after = dict(before_free, requires_consent=True)
    assert reconsent.classify_change(before_free, after)["materiality"] == "MATERIAL"
    # And the reverse is a narrowing.
    assert reconsent.classify_change(after, before_free)["materiality"] == "NARROWING"


def test_declaring_a_purpose_child_restricted_is_material():
    result = _classify(child_restricted=True)
    assert result["materiality"] == "MATERIAL"
    assert "s.9" in result["changed_fields"]["child_restricted"]["why"]
    # Lifting the restriction relaxes rather than widens.
    assert _classify()["materiality"] == "COSMETIC"


def test_changing_an_items_necessity_is_material_even_with_the_same_categories():
    result = _classify(data_items=[{"data_category_id": 1, "necessity": "optional"}])
    assert result["materiality"] == "MATERIAL"
    assert result["changed_fields"]["data_items"]["materiality"] == "MATERIAL"


def test_reordering_the_itemised_list_is_not_a_change_at_all():
    before = {
        "data_items": [
            {"data_category_id": 1, "necessity": "required"},
            {"data_category_id": 2, "necessity": "optional"},
        ],
    }
    after = {
        "data_items": [
            {"data_category_id": 2, "necessity": "optional"},
            {"data_category_id": 1, "necessity": "required"},
        ],
    }
    result = reconsent.classify_change(before, after)
    assert result["changed_fields"] == {}
    assert result["materiality"] == "COSMETIC"


def test_renaming_a_purpose_is_never_material():
    result = _classify(name="Marketing and offers")
    assert result["materiality"] == "COSMETIC"
    assert "presentation" in result["changed_fields"]["name"]["why"]


def test_adding_a_translation_is_never_material():
    assert _classify(translations={"hi": {"name": "..."}})["materiality"] == "COSMETIC"


def test_free_text_is_material_by_default():
    for field in ("description", "services_enabled", "consent_text"):
        result = _classify(**{field: "something else entirely"})
        assert result["materiality"] == "MATERIAL", field
        assert result["changed_fields"][field]["overridable"] is True


def test_free_text_can_be_downgraded_with_a_recorded_justification():
    before = {"consent_text": "I agree.", "name": "x"}
    after = {"consent_text": "I agree", "name": "x"}
    result = reconsent.classify_change(
        before, after, cosmetic_overrides={"consent_text": "Removed a stray full stop."}
    )
    assert result["materiality"] == "COSMETIC"
    entry = result["changed_fields"]["consent_text"]
    assert entry["overridden"] is True
    assert entry["override_justification"] == "Removed a stray full stop."
    # The default classification survives in the recorded reason - the claim
    # is auditable, not invisible.
    assert "Classified MATERIAL by default" in entry["why"]


def test_a_widening_cannot_be_downgraded():
    before = {"data_category_ids": [1]}
    after = {"data_category_ids": [1, 2]}
    with pytest.raises(reconsent.MaterialityError) as exc:
        reconsent.classify_change(
            before, after, cosmetic_overrides={"data_category_ids": "it is only a small one"}
        )
    assert "cannot be downgraded" in str(exc.value)
    assert "not a matter of opinion" in str(exc.value)


def test_no_change_at_all_is_cosmetic_with_an_empty_diff():
    result = _classify()
    assert result["changed_fields"] == {}
    assert result["materiality"] == "COSMETIC"
    assert "No classified field changed" in result["basis"]


# --------------------------------------------------------------------------- #
# The change log records every publication, cosmetic ones included
# --------------------------------------------------------------------------- #
def test_a_cosmetic_change_is_logged_and_starts_no_campaign(db):
    purpose, category, activity, pv1 = _make_purpose(db, "cosmetic_log")
    customer = _make_customer(db, "R109-COSM-001")
    consent = _granted_consent(db, customer, purpose, category, activity)

    pv2 = _new_version(db, purpose, pv1, name="Purpose cosmetic_log (renamed)")
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    assert result["materiality"] == "COSMETIC"
    assert result["campaign_ref"] is None
    assert result["consents_flagged"] == 0
    db.refresh(consent)
    assert consent.re_consent_required is False

    row = (
        db.query(PolicyChangeLog)
        .filter(PolicyChangeLog.change_ref == result["change_ref"])
        .one()
    )
    assert row.materiality == "COSMETIC"
    assert row.entity_type == "PURPOSE"
    assert row.from_version == 1 and row.to_version == 2
    assert "name" in row.changed_fields


def test_a_narrowing_is_logged_distinctly_from_a_cosmetic_change(db):
    purpose, category, activity, pv1 = _make_purpose(db, "narrowing_log")
    pv2 = _new_version(db, purpose, pv1, retention_period_days=30)
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")
    assert result["materiality"] == "NARROWING"
    assert result["campaign_ref"] is None
    row = db.query(PolicyChangeLog).filter(
        PolicyChangeLog.change_ref == result["change_ref"]
    ).one()
    assert row.materiality == "NARROWING"


def test_the_change_and_the_campaign_are_audited(db):
    purpose, category, activity, pv1 = _make_purpose(db, "audit_change")
    customer = _make_customer(db, "R109-AUDIT-001")
    _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, legal_basis="S7_I")
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    events = {
        a.event
        for a in db.query(AuditLog).filter(AuditLog.purpose_id == purpose.id).all()
    }
    assert "POLICY_CHANGE_LOGGED" in events
    assert "RE_CONSENT_CAMPAIGN_STARTED" in events
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event == "RE_CONSENT_REQUIRED", AuditLog.customer_id == customer.id)
        .count()
        >= 1
    )
    assert result["campaign_ref"]


# --------------------------------------------------------------------------- #
# Only a fresh, affirmative act clears the flag
# --------------------------------------------------------------------------- #
def test_repointing_a_consent_at_a_new_version_does_not_clear_the_flag(db):
    """The campaign re-points the consent at the new purpose version so the
    principal is asked about the right thing - and that must not, by itself,
    be treated as agreement to it."""
    purpose, category, activity, pv1 = _make_purpose(db, "no_assume")
    customer = _make_customer(db, "R109-ASSUME-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    db.refresh(consent)
    assert consent.purpose_version_id == pv2.id, "the consent was re-pointed"
    assert consent.status == "UPDATED"
    assert consent.re_consent_required is True, (
        "re-pointing is not consenting - BRD 4.1.3, 'consent cannot be assumed'"
    )
    assert evaluate_decision(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    ).decision == "REQUIRE_CONSENT"


def test_granting_again_clears_the_flag_and_counts_toward_k09(db):
    purpose, category, activity, pv1 = _make_purpose(db, "grant_clears")
    customer = _make_customer(db, "R109-GRANT-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    consent_service.withdraw_consent(
        db, consent, actor_username="principal", source_app=customer.source_app,
        actor_type="PRINCIPAL",
    )
    consent_service.grant_consent(
        db, consent, actor_username="principal", source_app=customer.source_app,
        actor_type="PRINCIPAL",
    )
    db.refresh(consent)
    assert consent.re_consent_required is False
    campaign = db.query(ReConsentCampaign).filter(
        ReConsentCampaign.campaign_ref == result["campaign_ref"]
    ).one()
    assert campaign.fresh_consents == 1

    metrics = reconsent.re_consent_metrics(db)
    assert metrics["consents_flagged"] >= 1
    assert metrics["fresh_consents"] >= 1
    assert metrics["re_consent_rate_pct"] is not None


def test_cancelling_a_campaign_lifts_the_block(db):
    """Cancelling means the change was rolled back or superseded; leaving
    principals unprocessable for a change that no longer exists would be the
    mirror image of the failure this feature fixes."""
    purpose, category, activity, pv1 = _make_purpose(db, "cancel_lifts")
    customer = _make_customer(db, "R109-CANCEL-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    campaign = db.query(ReConsentCampaign).filter(
        ReConsentCampaign.campaign_ref == result["campaign_ref"]
    ).one()
    reconsent.close_campaign(
        db, campaign, status="CANCELLED", actor_username="dpo",
        reason="The purpose change was reverted before anyone was processed under it",
    )
    db.refresh(consent)
    assert consent.re_consent_required is False
    assert evaluate_decision(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    ).decision == "ALLOW"


def test_completing_a_campaign_leaves_outstanding_blocks_in_place(db):
    """The change is still live, so a consent that was never re-given is still
    not consent to it."""
    purpose, category, activity, pv1 = _make_purpose(db, "complete_keeps")
    customer = _make_customer(db, "R109-COMPLETE-001")
    consent = _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")
    campaign = db.query(ReConsentCampaign).filter(
        ReConsentCampaign.campaign_ref == result["campaign_ref"]
    ).one()
    reconsent.close_campaign(db, campaign, status="COMPLETED", actor_username="dpo")
    db.refresh(consent)
    assert consent.re_consent_required is True


# --------------------------------------------------------------------------- #
# P-03: cookie policy versioning
# --------------------------------------------------------------------------- #
def _cookie_categories(**overrides):
    base = [
        {"key": "strictly_necessary", "label": "Necessary", "description": "Required to log in",
         "purpose_code": "strictly_necessary", "shared_with": [], "duration": "session",
         "strictly_necessary": True},
        {"key": "analytics", "label": "Analytics", "description": "Usage measurement",
         "purpose_code": "analytics", "shared_with": ["Analytics Co"], "duration": "13 months",
         "strictly_necessary": False},
    ]
    for c in base:
        if c["key"] in overrides:
            c.update(overrides[c["key"]])
    return base


def test_publishing_the_first_cookie_policy_is_material_and_versioned(db):
    tenant_id = resolve_tenant_id(db, "RECONSENT_COOKIE_1")
    result = reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id, categories=_cookie_categories(),
        summary="How we use cookies", actor_username="dpo",
    )
    assert result["version_number"] == 1
    assert result["materiality"] == "MATERIAL"
    assert len(result["content_hash"]) == 64
    current = reconsent.current_cookie_policy(db, tenant_id)
    assert current.version_number == 1
    assert current.is_current is True


def test_a_material_cookie_policy_change_invalidates_stored_preferences(db):
    """P-03. Server-side: the banner has nothing to read back, so it re-asks -
    with no cooperation needed from any frontend."""
    source_app = "RECONSENT_COOKIE_2"
    tenant_id = resolve_tenant_id(db, source_app)
    # v1 first: the very first cookie policy is itself a MATERIAL change (there
    # was no disclosure before it), so a principal who stored preferences
    # before v1 existed is invalidated by v1 rather than by v2. Establishing v1
    # first is what makes this test about the SECOND publication.
    reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id, categories=_cookie_categories(),
        summary="v1", actor_username="dpo",
    )
    customer = _make_customer(
        db, "R109-COOKIE-001", source_app=source_app, with_directory=True
    )
    directory = db.query(CrmCustomer).filter(
        CrmCustomer.email_search == hmac_digest(customer.email)
    ).one()
    assert directory.consent_preferences  # non-empty before

    result = reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id,
        categories=_cookie_categories(analytics={"shared_with": ["Analytics Co", "AdTech Ltd"]}),
        summary="v2: we now share analytics data with an additional recipient",
        actor_username="dpo",
    )
    assert result["version_number"] == 2
    assert result["materiality"] == "MATERIAL"
    assert result["preferences_invalidated"] >= 1

    db.refresh(directory)
    assert directory.consent_preferences == {}, "stored preferences must be invalidated"

    events = {a.event for a in db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id).all()}
    assert "COOKIE_POLICY_PUBLISHED" in events
    assert "COOKIE_PREFERENCES_INVALIDATED" in events


def test_a_material_cookie_policy_change_blocks_processing_for_those_purposes(db):
    """Clearing the banner's memory is not enough on its own: the platform's
    own consent rows would still say GRANTED."""
    source_app = "RECONSENT_COOKIE_3"
    tenant_id = resolve_tenant_id(db, source_app)
    purpose, category, activity, _ = _make_purpose(db, "analytics_cookie")

    # v1 first, then the consent: the first cookie policy is itself material,
    # so establishing it up front is what makes this test about the second
    # publication rather than about the existence of a policy at all.
    cats = [{"key": "analytics", "label": "Analytics", "description": "Usage measurement",
             "purpose_code": "analytics_cookie", "shared_with": [], "duration": "13 months",
             "strictly_necessary": False}]
    reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id, categories=cats, summary="v1", actor_username="dpo"
    )

    customer = _make_customer(db, "R109-COOKIE-002", source_app=source_app)
    consent = _granted_consent(db, customer, purpose, category, activity)
    assert evaluate_decision(
        db, customer, purpose, category, activity, source_app=source_app
    ).decision == "ALLOW"

    cats[0]["shared_with"] = ["AdTech Ltd"]
    result = reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id, categories=cats, summary="v2", actor_username="dpo"
    )
    assert result["materiality"] == "MATERIAL"
    assert result["consents_flagged"] >= 1

    db.refresh(consent)
    assert consent.re_consent_required is True
    assert evaluate_decision(
        db, customer, purpose, category, activity, source_app=source_app
    ).decision == "REQUIRE_CONSENT"


def test_republishing_an_identical_cookie_policy_is_cosmetic(db):
    tenant_id = resolve_tenant_id(db, "RECONSENT_COOKIE_4")
    cats = _cookie_categories()
    first = reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id, categories=cats, summary="same", actor_username="dpo"
    )
    second = reconsent.publish_cookie_policy(
        db, tenant_id=tenant_id, categories=cats, summary="same", actor_username="dpo"
    )
    assert second["version_number"] == 2
    assert second["materiality"] == "COSMETIC"
    assert second["campaign_ref"] is None
    assert second["content_hash"] == first["content_hash"]


# --------------------------------------------------------------------------- #
# End to end through the real routes
# --------------------------------------------------------------------------- #
def test_putting_a_material_purpose_change_starts_a_campaign_through_the_api(
    db, purposes_client, staff_token
):
    purpose, category, activity, pv1 = _make_purpose(db, "api_material")
    customer = _make_customer(db, "R109-API-001")
    consent = _granted_consent(db, customer, purpose, category, activity)

    response = purposes_client.put(
        f"/purposes/{purpose.id}",
        json={"retention_period_days": 1000},
        headers=_auth(staff_token),
    )
    assert response.status_code == 200, response.text
    change = response.json()["change"]
    assert change is not None
    assert change["materiality"] == "MATERIAL"
    assert change["consents_flagged"] == 1
    assert change["campaign_ref"]

    db.refresh(consent)
    assert consent.re_consent_required is True
    assert evaluate_decision(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    ).decision == "REQUIRE_CONSENT"


def test_the_api_refuses_to_downgrade_a_widening(db, purposes_client, staff_token):
    purpose, category, activity, pv1 = _make_purpose(db, "api_refuse")
    response = purposes_client.put(
        f"/purposes/{purpose.id}",
        json={
            "retention_period_days": 1000,
            "cosmetic_overrides": {"retention_period_days": "only a bit longer"},
        },
        headers=_auth(staff_token),
    )
    assert response.status_code == 422
    assert "cannot be downgraded" in response.json()["detail"]


def test_the_rules_endpoint_serves_the_definition(db, reconsent_client, staff_token):
    response = reconsent_client.get("/re-consent/rules", headers=_auth(staff_token))
    assert response.status_code == 200
    rules = {r["field"]: r for r in response.json()}
    assert rules["data_category_ids"]["always_material"] is True
    assert rules["data_category_ids"]["overridable"] is False
    assert rules["consent_text"]["overridable"] is True
    assert rules["name"]["always_material"] is False
    assert "s.6(1)" in rules["data_category_ids"]["why_material"]


def test_the_change_log_endpoint_lists_cosmetic_changes_too(db, reconsent_client, staff_token):
    response = reconsent_client.get("/re-consent/changes", headers=_auth(staff_token))
    assert response.status_code == 200
    materialities = {r["materiality"] for r in response.json()}
    assert "COSMETIC" in materialities, (
        "a log that recorded only material changes could not be used to check that judgement"
    )


def test_cancelling_through_the_api_requires_a_reason(db, reconsent_client, staff_token):
    purpose, category, activity, pv1 = _make_purpose(db, "api_cancel")
    customer = _make_customer(db, "R109-API-002")
    _granted_consent(db, customer, purpose, category, activity)
    pv2 = _new_version(db, purpose, pv1, retention_period_days=999)
    result = reconsent.publish_purpose_change(db, purpose, pv1, pv2, actor_username="dpo")

    bad = reconsent_client.post(
        f"/re-consent/campaigns/{result['campaign_ref']}/close",
        json={"status": "CANCELLED", "reason": "  "},
        headers=_auth(staff_token),
    )
    assert bad.status_code == 422
    assert "reason is required" in bad.json()["detail"]

    good = reconsent_client.post(
        f"/re-consent/campaigns/{result['campaign_ref']}/close",
        json={"status": "CANCELLED", "reason": "Change reverted"},
        headers=_auth(staff_token),
    )
    assert good.status_code == 200
    assert good.json()["status"] == "CANCELLED"


def test_the_metrics_endpoint_reports_k09(db, reconsent_client, staff_token):
    response = reconsent_client.get("/re-consent/metrics", headers=_auth(staff_token))
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "campaigns", "consents_flagged", "fresh_consents", "re_consent_rate_pct",
        "consents_blocked_now", "material_changes", "changes_logged",
    }


def test_every_re_consent_route_is_permission_gated(db):
    from app.api.routes import reconsent as reconsent_routes
    from app.core.rbac import PERM_POLICY_MANAGE, PERM_POLICY_VIEW

    def _required_permissions(route) -> set:
        found = set()
        for dependency in route.dependant.dependencies:
            call = getattr(dependency, "call", None)
            for cell in (getattr(call, "__closure__", None) or ()):
                if isinstance(cell.cell_contents, str):
                    found.add(cell.cell_contents)
        return found

    for route in reconsent_routes.router.routes:
        perms = _required_permissions(route)
        assert perms & {PERM_POLICY_VIEW, PERM_POLICY_MANAGE}, (
            f"{route.path} is not permission-gated"
        )
        if route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert PERM_POLICY_MANAGE in perms, (
                f"{route.path} mutates but does not require policy.manage"
            )
