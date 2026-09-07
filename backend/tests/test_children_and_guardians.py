"""R1-14 (F-01..F-06): children, guardians and the s.9 prohibitions.

The two headline tests are the task's definition of done, and they are first
in the file on purpose:

  test_dod_1_no_consent_for_a_child_without_verified_parental_consent
  test_dod_2_advertising_and_analytics_are_denied_for_a_child

Everything after them exists because s.9 contains the only *prohibitions* in
this Act that consent cannot discharge, and each of the ways that could be
quietly undone gets its own test: a self-declaration promoted to
verification, a virtual token treated as confirmed when no integration
exists, a Fourth Schedule exemption widened into a blanket switch, a
revocation that does not take effect, an exemption for one purpose leaking
into another, and - the one an implementation is most likely to get wrong - a
verified parental consent being read as permission for targeted advertising.

`app/main.py` does not mount the children router (registering it is the
coordinating agent's change), so the HTTP-level tests mount it onto a local
FastAPI app, exactly as tests/test_reconsent.py does for its own router.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.models.entities import (
    AuditLog,
    ConsentContext,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.models.guardian import GuardianConsent, PrincipalAgeAssurance, TenantChildExemption
from app.services import consent as consent_service
from app.services import guardian as guardian_service
from app.services.decision_engine import evaluate_decision
from app.services.tenancy import resolve_tenant_id

SOURCE_APP = "R114_CHILDREN_TEST"


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def children_client(db):
    from app.api.routes import guardian as guardian_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(guardian_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_purpose(db, code, *, child_restricted=False, requires_consent=True):
    """Get-or-create, because a Purpose code is globally unique and this
    suite shares one database across every test module (see
    tests/test_signature_key_rotation.py::_crm_cookie_purpose, which does the
    same for the cookie-banner codes)."""
    existing = db.query(Purpose).filter(Purpose.code == code).first()
    if existing:
        pv = next(v for v in existing.versions if v.is_current)
        category = db.get(DataCategory, pv.data_category_ids[0])
        activity = db.get(ProcessingActivity, pv.processing_activity_ids[0])
        return existing, category, activity

    category = DataCategory(name=f"cat-{code}", code=f"cat_r114_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_r114_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(
        name=f"Purpose {code}", code=code, legal_basis="CONSENT",
        requires_consent=requires_consent, retention_period_days=365,
        child_restricted=child_restricted,
    )
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        retention_period_days=365,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    db.refresh(purpose)
    return purpose, category, activity


def _make_customer(db, external_id, *, source_app=SOURCE_APP):
    customer = Customer(
        external_id=external_id, name="Test Principal",
        email=f"{external_id.lower()}@example.test",
        source_app=source_app, tenant_id=resolve_tenant_id(db, source_app),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _flag_as_child(db, customer, *, years_old=12, is_pwd=False):
    return guardian_service.record_age_assurance(
        db, customer=customer, assurance_method="IDENTITY_DOCUMENT",
        date_of_birth=date.today() - timedelta(days=365 * years_old + 10),
        is_person_with_disability=is_pwd,
        assurance_reference="PASSPORT-CASE-1", actor_username="test",
        source_app=customer.source_app,
    )


def _flag_as_adult(db, customer, *, is_pwd=False):
    return guardian_service.record_age_assurance(
        db, customer=customer, assurance_method="IDENTITY_DOCUMENT",
        date_of_birth=date.today() - timedelta(days=365 * 40),
        is_person_with_disability=is_pwd,
        assurance_reference="PASSPORT-CASE-2", actor_username="test",
        source_app=customer.source_app,
    )


def _verified_parental_consent(db, child, *, guardian_type="PARENT", **overrides):
    kwargs = dict(
        guardian_type=guardian_type,
        verification_method="VOLUNTARILY_PROVIDED_DETAILS",
        guardian_name="A Parent",
        guardian_email=f"parent-{child.id}@example.test",
        guardian_identity_reference="DL-99887766",
        guardian_date_of_birth=date.today() - timedelta(days=365 * 41),
        actor_username="test",
        source_app=child.source_app,
    )
    if guardian_type == "LAWFUL_GUARDIAN":
        kwargs.update(
            appointment_authority="COURT",
            appointment_reference="CIV/2026/4411",
            appointment_date=date.today() - timedelta(days=400),
        )
    kwargs.update(overrides)
    record = guardian_service.create_guardian_consent(db, customer=child, **kwargs)
    return guardian_service.verify_guardian_consent(db, record, actor_username="test")


def _consent_for(db, customer, purpose, category, activity):
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=customer.source_app,
        exact_source=True,
    )
    return consent


def _audit_events(db, customer_id, event):
    return (
        db.query(AuditLog)
        .filter(AuditLog.customer_id == customer_id, AuditLog.event == event)
        .all()
    )


# --------------------------------------------------------------------------- #
# THE DEFINITION OF DONE
# --------------------------------------------------------------------------- #
def test_dod_1_no_consent_for_a_child_without_verified_parental_consent(db):
    """DoD: 'No consent can be recorded for a child account without a verified
    parental consent record.'

    Enforced in services/consent.py - the choke point every grant path shares
    - not in a route, so there is no door left open.
    """
    purpose, category, activity = _make_purpose(db, "r114_service_purpose")
    child = _make_customer(db, "R114-DOD1-CHILD")
    _flag_as_child(db, child)
    consent = _consent_for(db, child, purpose, category, activity)

    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)

    assert exc.value.status_code == 403
    assert "s.9(1)" in exc.value.detail
    assert "verifiable consent of the parent" in exc.value.detail
    db.rollback()

    # Nothing was recorded - not a granted consent, not even a partial one.
    db.refresh(consent)
    assert consent.status == "NOT_REQUESTED"
    assert consent.granted_at is None
    assert consent.evidence == []

    # And the refusal is in the append-only ledger, countable as K-25.
    blocks = _audit_events(db, child.id, "CHILD_CONSENT_BLOCKED")
    assert len(blocks) == 1
    assert blocks[0].decision == "DENY"

    # With a verified parental consent, the same grant succeeds.
    parental = _verified_parental_consent(db, child)
    granted = consent_service.grant_consent(
        db, consent, actor_username="test", source_app=SOURCE_APP
    )
    assert granted.status == "GRANTED"

    # ...and the consent's own evidence row can answer WHO consented, HOW they
    # were verified and WHEN, without anyone having to join two tables by hand.
    details = granted.evidence[-1].details
    assert details["s9_1_satisfied_by"] == "GUARDIAN_CONSENT"
    assert details["guardian_consent_ref"] == parental.reference_no
    assert details["guardian_verification_method"] == "VOLUNTARILY_PROVIDED_DETAILS"
    assert details["guardian_verified_at"] is not None
    assert details["guardian_evidence_hash"] == parental.evidence_hash


def test_dod_2_advertising_and_analytics_are_denied_for_a_child(db):
    """DoD: 'advertising/analytics purposes are denied for child accounts.'

    s.9(3) is a prohibition, not a preference, so this is checked where the
    platform actually decides whether processing may happen - the decision
    engine - and it outranks a valid, granted, unexpired consent.
    """
    ads, ads_cat, ads_act = _make_purpose(db, "advertising")
    analytics, an_cat, an_act = _make_purpose(db, "analytics")
    child = _make_customer(db, "R114-DOD2-CHILD")

    # Consent is granted while the account is not yet known to be a child,
    # which is the realistic and the harder case: the ALLOW is already there.
    ads_consent = _consent_for(db, child, ads, ads_cat, ads_act)
    consent_service.grant_consent(db, ads_consent, actor_username="test", source_app=SOURCE_APP)
    before = evaluate_decision(db, child, ads, ads_cat, ads_act, source_app=SOURCE_APP)
    assert before.decision == "ALLOW"

    _flag_as_child(db, child)

    after = evaluate_decision(db, child, ads, ads_cat, ads_act, source_app=SOURCE_APP)
    assert after.decision == "DENY", after.reason
    assert after.allowed is False
    assert "s.9(3)" in after.reason

    analytics_decision = evaluate_decision(db, child, analytics, an_cat, an_act, source_app=SOURCE_APP)
    assert analytics_decision.decision == "DENY"
    assert "s.9(3)" in analytics_decision.reason

    # The prohibition survives a perfect parental consent. This is the single
    # most dangerous thing this module could get wrong: a parent has no power
    # to authorise targeted advertising directed at their child, because the
    # Act does not make it authorisable.
    _verified_parental_consent(db, child)
    still_denied = evaluate_decision(db, child, ads, ads_cat, ads_act, source_app=SOURCE_APP)
    assert still_denied.decision == "DENY"
    assert "s.9(3)" in still_denied.reason

    # And it is refused at the recording point too, so the platform cannot
    # even be asked to store the consent that would authorise it.
    fresh_consent = _consent_for(db, child, analytics, an_cat, an_act)
    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, fresh_consent, actor_username="test", source_app=SOURCE_APP)
    assert exc.value.status_code == 403
    assert "s.9(3)" in exc.value.detail
    db.rollback()

    assert _audit_events(db, child.id, "CHILD_PROHIBITED_DECISION_DENIED")
    assert _audit_events(db, child.id, "CHILD_PROHIBITED_PROCESSING_BLOCKED")


# --------------------------------------------------------------------------- #
# The default must not change for anybody who is not a child
# --------------------------------------------------------------------------- #
def test_a_principal_with_no_age_assurance_is_completely_unaffected(db):
    """A missing age-assurance record means "never assessed", not "child".

    Stated as a test because the alternative - refusing consent for every
    pre-existing customer the moment this shipped - is a platform that gets
    switched off within the hour, and because every other test in this suite
    depends on this behaviour being unchanged.
    """
    purpose, category, activity = _make_purpose(db, "r114_unassessed")
    customer = _make_customer(db, "R114-NO-ASSURANCE")
    assert guardian_service.get_age_assurance(db, customer.id) is None

    consent = _consent_for(db, customer, purpose, category, activity)
    granted = consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert granted.status == "GRANTED"
    assert "s9_1_satisfied_by" not in granted.evidence[-1].details

    decision = evaluate_decision(db, customer, purpose, category, activity, source_app=SOURCE_APP)
    assert decision.decision == "ALLOW"


def test_an_assessed_adult_is_unaffected_including_for_advertising(db):
    ads, cat, act = _make_purpose(db, "advertising")
    adult = _make_customer(db, "R114-ADULT")
    _flag_as_adult(db, adult)

    consent = _consent_for(db, adult, ads, cat, act)
    assert consent_service.grant_consent(
        db, consent, actor_username="test", source_app=SOURCE_APP
    ).status == "GRANTED"
    assert evaluate_decision(db, adult, ads, cat, act, source_app=SOURCE_APP).decision == "ALLOW"


# --------------------------------------------------------------------------- #
# F-01: age assurance is derived, and a tick-box is never verification
# --------------------------------------------------------------------------- #
def test_is_child_is_derived_from_the_date_of_birth_not_asserted(db):
    young = _make_customer(db, "R114-DOB-YOUNG")
    old = _make_customer(db, "R114-DOB-OLD")

    a = guardian_service.record_age_assurance(
        db, customer=young, assurance_method="DECLARED_DATE_OF_BIRTH",
        date_of_birth=date.today() - timedelta(days=365 * 10), actor_username="test",
    )
    b = guardian_service.record_age_assurance(
        db, customer=old, assurance_method="DECLARED_DATE_OF_BIRTH",
        date_of_birth=date.today() - timedelta(days=365 * 30), actor_username="test",
    )
    assert a.is_child is True
    assert b.is_child is False


def test_age_is_counted_in_completed_years_so_the_day_before_18_is_still_a_child():
    """s.2(f): "has not completed eighteen years of age". Rounding a
    seventeen-year-and-364-day-old up to 18 would switch off the entire s.9
    regime for them."""
    today = date(2026, 9, 4)
    day_before_18th = date(2008, 9, 5)
    on_18th = date(2008, 9, 4)
    assert guardian_service.age_on(day_before_18th, on=today) == 17
    assert guardian_service.is_child_by_dob(day_before_18th, on=today) is True
    assert guardian_service.age_on(on_18th, on=today) == 18
    assert guardian_service.is_child_by_dob(on_18th, on=today) is False


def test_a_self_declaration_can_be_recorded_but_never_marked_verified(db):
    """The gap F-01 names: the demo sites' pre-ticked "I am 18+" box. R.10(1)
    requires due diligence; a tick-box is not due diligence."""
    customer = _make_customer(db, "R114-SELF-DECLARED")
    record = guardian_service.record_age_assurance(
        db, customer=customer, assurance_method="SELF_DECLARED",
        declared_is_child=False, actor_username="test",
    )
    assert record.is_child is False
    assert record.is_verified is False
    assert record.assured_at is None

    # And the database refuses the claim independently of the service layer.
    record.is_verified = True
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_age_assurances_self_declared_is_not_verified" in str(exc.value)
    db.rollback()


def test_a_self_declared_child_still_blocks_consent(db):
    """An unverified self-declaration is not evidence the principal is an
    adult, but it IS a reason to treat them as a child: the protection runs
    one way only."""
    purpose, category, activity = _make_purpose(db, "r114_selfdecl_child")
    customer = _make_customer(db, "R114-SELF-CHILD")
    guardian_service.record_age_assurance(
        db, customer=customer, assurance_method="SELF_DECLARED",
        declared_is_child=True, actor_username="test",
    )
    consent = _consent_for(db, customer, purpose, category, activity)
    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert exc.value.status_code == 403
    db.rollback()


def test_re_assessing_a_principal_updates_one_row_rather_than_adding_a_second(db):
    customer = _make_customer(db, "R114-REASSESS")
    first = _flag_as_child(db, customer)
    second = _flag_as_adult(db, customer)
    assert first.id == second.id
    assert second.is_child is False
    assert (
        db.query(PrincipalAgeAssurance)
        .filter(PrincipalAgeAssurance.customer_id == customer.id)
        .count()
        == 1
    )


# --------------------------------------------------------------------------- #
# F-02: the three R.10(2) verification routes
# --------------------------------------------------------------------------- #
def test_voluntarily_provided_details_need_both_identity_and_age(db):
    child = _make_customer(db, "R114-VOL-DETAILS")
    _flag_as_child(db, child)

    no_dob = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="VOLUNTARILY_PROVIDED_DETAILS",
        guardian_name="Parent", guardian_identity_reference="DL-1",
        actor_username="test",
    )
    with pytest.raises(HTTPException) as exc:
        guardian_service.verify_guardian_consent(db, no_dob, actor_username="test")
    assert exc.value.status_code == 422
    assert "date of birth" in exc.value.detail

    no_identity = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="VOLUNTARILY_PROVIDED_DETAILS",
        guardian_name="Parent",
        guardian_date_of_birth=date.today() - timedelta(days=365 * 44),
        actor_username="test",
    )
    with pytest.raises(HTTPException) as exc:
        guardian_service.verify_guardian_consent(db, no_identity, actor_username="test")
    assert exc.value.status_code == 422
    assert "identity reference" in exc.value.detail


def test_a_guardian_who_is_themselves_a_child_is_refused(db):
    """R.10(1) requires due diligence that the consenting person is an
    identifiable ADULT."""
    child = _make_customer(db, "R114-CHILD-GUARDIAN")
    _flag_as_child(db, child)
    record = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="VOLUNTARILY_PROVIDED_DETAILS",
        guardian_name="Also A Child", guardian_identity_reference="ID-2",
        guardian_date_of_birth=date.today() - timedelta(days=365 * 15),
        actor_username="test",
    )
    with pytest.raises(HTTPException) as exc:
        guardian_service.verify_guardian_consent(db, record, actor_username="test")
    assert exc.value.status_code == 422
    assert "ADULT" in exc.value.detail


def test_existing_verified_account_requires_a_genuinely_verified_account(db):
    """R.10(2)(a) permits reliance on details already held only where those
    details are reliable. "We have an account for them" is not "we verified
    them"."""
    child = _make_customer(db, "R114-EVA-CHILD")
    parent = _make_customer(db, "R114-EVA-PARENT")
    _flag_as_child(db, child)

    record = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="EXISTING_VERIFIED_ACCOUNT",
        guardian_name="Linked Parent", guardian_customer_id=parent.id,
        actor_username="test",
    )
    # No verified context on the parent account at all.
    with pytest.raises(HTTPException) as exc:
        guardian_service.verify_guardian_consent(db, record, actor_username="test")
    assert exc.value.status_code == 422
    assert "never completed identity verification" in exc.value.detail

    db.add(ConsentContext(
        customer_id=parent.id, token=f"tok-r114-{parent.id}", source_app=SOURCE_APP,
        expires_at=_now() + timedelta(hours=1), verified_at=_now(),
        verification_method="EMAIL_OTP",
    ))
    db.commit()

    # Verified context, but the fiduciary holds no age details for the parent,
    # so it does not in fact hold "reliable details of identity AND age".
    with pytest.raises(HTTPException) as exc:
        guardian_service.verify_guardian_consent(db, record, actor_username="test")
    assert exc.value.status_code == 422
    assert "verified age assurance" in exc.value.detail

    _flag_as_adult(db, parent)
    verified = guardian_service.verify_guardian_consent(db, record, actor_username="test")
    assert verified.status == "VERIFIED"
    assert verified.guardian_is_adult is True
    assert verified.guardian_context_id is not None


def test_the_virtual_token_route_is_an_honest_placeholder(db):
    """R.10(2)(c). No verifier ships with this build, so the route returns 501,
    the record stays PENDING, and the child's consent stays blocked. A stub
    that returned success would be a fabricated verification for a child."""
    purpose, category, activity = _make_purpose(db, "r114_token_purpose")
    child = _make_customer(db, "R114-TOKEN-CHILD")
    _flag_as_child(db, child)

    assert guardian_service.VIRTUAL_TOKEN_VERIFIERS == {}

    record = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="VIRTUAL_TOKEN", guardian_name="DigiLocker Parent",
        virtual_token_issuer="DIGILOCKER", virtual_token_reference="vt-abc-123",
        actor_username="test",
    )
    with pytest.raises(HTTPException) as exc:
        guardian_service.verify_guardian_consent(db, record, actor_username="test")
    assert exc.value.status_code == 501
    assert "interface in this build with no integration behind it" in exc.value.detail

    db.refresh(record)
    assert record.status == "PENDING"
    assert record.virtual_token_verified_at is None

    consent = _consent_for(db, child, purpose, category, activity)
    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert exc.value.status_code == 403
    db.rollback()

    # And the database will not let anything downstream call it verified.
    record.status = "VERIFIED"
    record.guardian_is_adult = True
    record.verified_at = _now()
    record.verified_by = "someone"
    record.evidence_ref = "GEV-FAKE"
    record.evidence_hash = "0" * 64
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_guardian_consents_virtual_token_verified_has_moment" in str(exc.value)
    db.rollback()


def test_a_registered_verifier_completes_the_virtual_token_route(db):
    """The interface is real even though no integration ships: a deployment
    that registers its authorised entity's adapter gets the R.10(2)(c) route
    working, and nothing else about the flow changes."""
    child = _make_customer(db, "R114-TOKEN-OK")
    _flag_as_child(db, child)
    record = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="VIRTUAL_TOKEN", guardian_name="DigiLocker Parent",
        virtual_token_issuer="TEST_LOCKER", virtual_token_reference="vt-good",
        actor_username="test",
    )
    calls = []

    def _adapter(*, issuer, token):
        calls.append((issuer, token))
        return token == "vt-good"

    guardian_service.register_virtual_token_verifier("TEST_LOCKER", _adapter)
    try:
        verified = guardian_service.verify_guardian_consent(db, record, actor_username="test")
    finally:
        guardian_service.VIRTUAL_TOKEN_VERIFIERS.pop("TEST_LOCKER", None)

    assert calls == [("TEST_LOCKER", "vt-good")]
    assert verified.status == "VERIFIED"
    assert verified.virtual_token_verified_at is not None


# --------------------------------------------------------------------------- #
# F-03: the R.11 lawful-guardian variant
# --------------------------------------------------------------------------- #
def test_lawful_guardian_needs_an_appointment_and_then_unlocks_consent(db):
    """R.11: a lawful guardian is lawful because a court, a designated
    authority or a local level committee appointed them."""
    purpose, category, activity = _make_purpose(db, "r114_pwd_purpose")
    principal = _make_customer(db, "R114-PWD")
    guardian_service.record_age_assurance(
        db, customer=principal, assurance_method="IDENTITY_DOCUMENT",
        date_of_birth=date.today() - timedelta(days=365 * 30),
        is_person_with_disability=True, actor_username="test",
        source_app=principal.source_app,
    )
    consent = _consent_for(db, principal, purpose, category, activity)

    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert exc.value.status_code == 403
    assert "lawful guardian" in exc.value.detail
    db.rollback()

    # An unappointed "guardian" cannot even be stored.
    with pytest.raises(IntegrityError) as exc:
        db.add(GuardianConsent(
            tenant_id=principal.tenant_id, reference_no="GRD-BAD-0001",
            customer_id=principal.id, guardian_type="LAWFUL_GUARDIAN",
            verification_method="VOLUNTARILY_PROVIDED_DETAILS",
            guardian_name="Unappointed", status="PENDING",
        ))
        db.commit()
    assert "ck_guardian_consents_lawful_guardian_is_appointed" in str(exc.value)
    db.rollback()

    _verified_parental_consent(db, principal, guardian_type="LAWFUL_GUARDIAN")
    granted = consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert granted.status == "GRANTED"
    assert granted.evidence[-1].details["guardian_type"] == "LAWFUL_GUARDIAN"


# --------------------------------------------------------------------------- #
# The evidence record
# --------------------------------------------------------------------------- #
def test_a_verified_record_carries_who_how_and_when_and_the_hash_recomputes(db):
    child = _make_customer(db, "R114-EVIDENCE")
    _flag_as_child(db, child)
    record = _verified_parental_consent(db, child)

    assert record.verified_by == "test"          # who did the verifying
    assert record.verification_method            # how
    assert record.verified_at is not None        # when
    assert record.guardian_is_adult is True
    assert record.evidence_ref and record.evidence_hash

    # The same hash is in the append-only, hash-chained ledger, so an edit to
    # guardian_consents afterwards is detectable by recomputing.
    entry = (
        db.query(AuditLog)
        .filter(AuditLog.customer_id == child.id, AuditLog.event == "GUARDIAN_CONSENT_VERIFIED")
        .one()
    )
    assert entry.details["evidence_hash"] == record.evidence_hash
    assert guardian_service.compute_guardian_evidence_hash(record) == record.evidence_hash


def test_a_verified_record_without_its_evidence_is_not_storable(db):
    child = _make_customer(db, "R114-EVIDENCE-CHECK")
    _flag_as_child(db, child)
    record = guardian_service.create_guardian_consent(
        db, customer=child, guardian_type="PARENT",
        verification_method="VOLUNTARILY_PROVIDED_DETAILS", guardian_name="Parent",
        guardian_identity_reference="ID-9",
        guardian_date_of_birth=date.today() - timedelta(days=365 * 40),
        actor_username="test",
    )
    record.status = "VERIFIED"
    record.verified_at = _now()
    record.verified_by = "test"
    # ...but no evidence_ref / evidence_hash / guardian_is_adult.
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_guardian_consents_verified_has_evidence" in str(exc.value)
    db.rollback()


def test_revoking_a_guardian_consent_takes_effect_immediately(db):
    purpose, category, activity = _make_purpose(db, "r114_revoke_purpose")
    child = _make_customer(db, "R114-REVOKE")
    _flag_as_child(db, child)
    record = _verified_parental_consent(db, child)

    consent = _consent_for(db, child, purpose, category, activity)
    consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)

    guardian_service.revoke_guardian_consent(db, record, reason="Parent withdrew", actor_username="test")

    # The already-granted consent can no longer be relied on...
    decision = evaluate_decision(db, child, purpose, category, activity, source_app=SOURCE_APP)
    assert decision.decision == "REQUIRE_CONSENT"
    assert "no verified parental or lawful-guardian consent record exists" in decision.reason

    # ...and it cannot be renewed either.
    with pytest.raises(HTTPException) as exc:
        consent_service.renew_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert exc.value.status_code == 403
    db.rollback()


def test_withdrawal_is_never_blocked_for_a_child(db):
    """The gate runs one way. A guard that blocked withdrawal would trap a
    child in a consent nobody could get them out of."""
    purpose, category, activity = _make_purpose(db, "r114_withdraw_purpose")
    child = _make_customer(db, "R114-WITHDRAW")
    _flag_as_child(db, child)
    record = _verified_parental_consent(db, child)
    consent = _consent_for(db, child, purpose, category, activity)
    consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)

    guardian_service.revoke_guardian_consent(db, record, actor_username="test")
    withdrawn = consent_service.withdraw_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    assert withdrawn.status == "WITHDRAWN"


# --------------------------------------------------------------------------- #
# F-05: Fourth Schedule exemptions are narrow, and cannot become a switch
# --------------------------------------------------------------------------- #
def _exemption(db, tenant_id, *, purpose_codes, obligations, active=True, **kw):
    row = TenantChildExemption(
        tenant_id=tenant_id,
        exemption_class=kw.pop("exemption_class", "EDUCATIONAL_INSTITUTION"),
        schedule_reference=kw.pop("schedule_reference", "Fourth Schedule, Part A, entry 3"),
        purpose_codes=list(purpose_codes),
        exempted_obligations=list(obligations),
        conditions="To the extent necessary for the stated purpose.",
        is_active=active,
        authorised_by="dpo" if active else None,
        authorised_at=_now() if active else None,
        created_by="test",
        **kw,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_an_exemption_with_no_purposes_cannot_be_stored(db):
    """The blanket switch this design exists to prevent. An exemption that
    names no purposes would apply to all of them."""
    tenant_id = resolve_tenant_id(db, SOURCE_APP)
    with pytest.raises(IntegrityError) as exc:
        _exemption(db, tenant_id, purpose_codes=[], obligations=["VERIFIABLE_PARENTAL_CONSENT"])
    assert "ck_child_exemptions_purposes_not_empty" in str(exc.value)
    db.rollback()

    with pytest.raises(IntegrityError) as exc:
        _exemption(db, tenant_id, purpose_codes=["anything"], obligations=[])
    assert "ck_child_exemptions_obligations_not_empty" in str(exc.value)
    db.rollback()


def test_an_active_exemption_must_name_who_authorised_it(db):
    tenant_id = resolve_tenant_id(db, SOURCE_APP)
    with pytest.raises(IntegrityError) as exc:
        row = TenantChildExemption(
            tenant_id=tenant_id, exemption_class="HEALTHCARE_PROVIDER",
            schedule_reference="Fourth Schedule, Part A, entry 1",
            purpose_codes=["x"], exempted_obligations=["VERIFIABLE_PARENTAL_CONSENT"],
            is_active=True,
        )
        db.add(row)
        db.commit()
    assert "ck_child_exemptions_active_is_authorised" in str(exc.value)
    db.rollback()


def test_an_exemption_applies_only_to_the_purposes_it_names(db):
    covered, c_cat, c_act = _make_purpose(db, "r114_exempt_covered")
    other, o_cat, o_act = _make_purpose(db, "r114_exempt_other")
    source_app = "R114_EXEMPT_TENANT"
    child = _make_customer(db, "R114-EXEMPT-CHILD", source_app=source_app)
    _flag_as_child(db, child)

    _exemption(
        db, resolve_tenant_id(db, source_app),
        purpose_codes=[covered.code], obligations=["VERIFIABLE_PARENTAL_CONSENT"],
    )

    covered_consent = _consent_for(db, child, covered, c_cat, c_act)
    granted = consent_service.grant_consent(
        db, covered_consent, actor_username="test", source_app=source_app
    )
    assert granted.status == "GRANTED"
    assert granted.evidence[-1].details["s9_1_satisfied_by"] == "FOURTH_SCHEDULE_EXEMPTION"

    other_consent = _consent_for(db, child, other, o_cat, o_act)
    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, other_consent, actor_username="test", source_app=source_app)
    assert exc.value.status_code == 403
    db.rollback()


def test_an_inactive_or_expired_exemption_exempts_nothing(db):
    purpose, cat, act = _make_purpose(db, "r114_exempt_inactive")
    source_app = "R114_EXEMPT_INACTIVE"
    child = _make_customer(db, "R114-EXEMPT-INACTIVE", source_app=source_app)
    _flag_as_child(db, child)
    tenant_id = resolve_tenant_id(db, source_app)

    row = _exemption(
        db, tenant_id, purpose_codes=[purpose.code],
        obligations=["VERIFIABLE_PARENTAL_CONSENT"], active=False,
    )
    consent = _consent_for(db, child, purpose, cat, act)
    with pytest.raises(HTTPException):
        consent_service.grant_consent(db, consent, actor_username="test", source_app=source_app)
    db.rollback()

    row.is_active = True
    row.authorised_by = "dpo"
    row.authorised_at = _now()
    row.effective_to = _now() - timedelta(days=1)
    row.effective_from = _now() - timedelta(days=30)
    db.commit()
    with pytest.raises(HTTPException):
        consent_service.grant_consent(db, consent, actor_username="test", source_app=source_app)
    db.rollback()


def test_a_parental_consent_exemption_does_not_lift_the_s9_3_prohibition(db):
    """The two obligations are relaxed independently. An educational
    institution exempt from obtaining parental consent is not thereby licensed
    to serve targeted advertising to its pupils."""
    ads, cat, act = _make_purpose(db, "advertising")
    source_app = "R114_EXEMPT_ADS"
    child = _make_customer(db, "R114-EXEMPT-ADS", source_app=source_app)
    _flag_as_child(db, child)
    _exemption(
        db, resolve_tenant_id(db, source_app),
        purpose_codes=[ads.code], obligations=["VERIFIABLE_PARENTAL_CONSENT"],
    )

    decision = evaluate_decision(db, child, ads, cat, act, source_app=source_app)
    assert decision.decision == "DENY"
    assert "s.9(3)" in decision.reason

    consent = _consent_for(db, child, ads, cat, act)
    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, consent, actor_username="test", source_app=source_app)
    assert "s.9(3)" in exc.value.detail
    db.rollback()


def test_an_exemption_that_names_the_tracking_obligation_does_lift_it(db):
    """R.12 exists because some processing that looks like tracking is the
    reason the child is there at all - a school's safety and transport
    tracking. Configured, authorised, and scoped to that purpose, it applies."""
    tracking, cat, act = _make_purpose(db, "r114_school_transport")
    source_app = "R114_SCHOOL"
    child = _make_customer(db, "R114-SCHOOL-CHILD", source_app=source_app)
    _flag_as_child(db, child)

    # Prohibited to begin with, because the purpose is flagged child-restricted.
    tracking.child_restricted = True
    db.commit()
    assert evaluate_decision(db, child, tracking, cat, act, source_app=source_app).decision == "DENY"

    _exemption(
        db, resolve_tenant_id(db, source_app),
        purpose_codes=[tracking.code],
        obligations=["TRACKING_AND_ADVERTISING_PROHIBITION", "VERIFIABLE_PARENTAL_CONSENT"],
        exemption_class="CHILD_TRANSPORT",
        schedule_reference="Fourth Schedule, Part A, entry 4",
    )
    consent = _consent_for(db, child, tracking, cat, act)
    consent_service.grant_consent(db, consent, actor_username="test", source_app=source_app)
    assert evaluate_decision(db, child, tracking, cat, act, source_app=source_app).decision == "ALLOW"


def test_an_exemption_belongs_to_exactly_one_tenant(db):
    purpose, cat, act = _make_purpose(db, "r114_exempt_tenant_scope")
    exempt_app = "R114_EXEMPT_OWNER"
    other_app = "R114_EXEMPT_NEIGHBOUR"
    _exemption(
        db, resolve_tenant_id(db, exempt_app),
        purpose_codes=[purpose.code], obligations=["VERIFIABLE_PARENTAL_CONSENT"],
    )
    neighbour_child = _make_customer(db, "R114-NEIGHBOUR-CHILD", source_app=other_app)
    _flag_as_child(db, neighbour_child)
    consent = _consent_for(db, neighbour_child, purpose, cat, act)
    with pytest.raises(HTTPException) as exc:
        consent_service.grant_consent(db, consent, actor_username="test", source_app=other_app)
    assert exc.value.status_code == 403
    db.rollback()


# --------------------------------------------------------------------------- #
# The tenant settings hook cannot be used to switch the prohibition off
# --------------------------------------------------------------------------- #
def test_tenant_settings_can_only_widen_the_prohibited_set(db):
    from app.models.entities import Organization

    source_app = "R114_SETTINGS"
    tenant_id = resolve_tenant_id(db, source_app)
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    # A tenant trying to remove "advertising" AND add one of its own.
    org.settings = {"child_prohibited_purpose_codes": ["house_ads"]}
    db.commit()

    purposes, _activities = guardian_service._prohibited_codes_for_tenant(db, tenant_id)
    assert "house_ads" in purposes          # the addition took effect
    assert "advertising" in purposes        # and the statutory default survived
    assert "analytics" in purposes


def test_the_shipped_defaults_cover_the_purposes_the_dod_names(db):
    assert "advertising" in guardian_service.DEFAULT_CHILD_PROHIBITED_PURPOSE_CODES
    assert "analytics" in guardian_service.DEFAULT_CHILD_PROHIBITED_PURPOSE_CODES
    assert "personalize_offers" in guardian_service.DEFAULT_CHILD_PROHIBITED_ACTIVITY_CODES


def test_a_child_restricted_purpose_is_prohibited_whatever_its_code(db):
    purpose, cat, act = _make_purpose(db, "r114_child_restricted", child_restricted=True)
    child = _make_customer(db, "R114-CHILD-RESTRICTED")
    _flag_as_child(db, child)
    decision = evaluate_decision(db, child, purpose, cat, act, source_app=SOURCE_APP)
    assert decision.decision == "DENY"
    assert "child_restricted" in decision.reason


# --------------------------------------------------------------------------- #
# The decision engine's placement
# --------------------------------------------------------------------------- #
def test_the_prohibition_outranks_a_policy_rule_and_reports_the_statutory_ground(db):
    """The new step sits at the head, ahead of the explicit policy-rule DENY,
    so the ledger records the statutory prohibition rather than an incidental
    rule that could be edited away tomorrow."""
    ads, cat, act = _make_purpose(db, "advertising")
    child = _make_customer(db, "R114-PRECEDENCE")
    _flag_as_child(db, child)
    decision = evaluate_decision(db, child, ads, cat, act, source_app=SOURCE_APP)
    assert decision.decision == "DENY"
    assert "s.9(3)" in decision.reason
    assert "explicitly denies" not in decision.reason


def test_a_withdrawn_consent_still_reports_withdrawn_for_a_child(db):
    """The s.9(1) branch sits INSIDE the consent-status step, immediately
    before "active -> ALLOW", so a withdrawn/denied/expired consent still
    reports its own, more specific outcome."""
    purpose, cat, act = _make_purpose(db, "r114_withdrawn_reporting")
    child = _make_customer(db, "R114-WITHDRAWN-REPORT")
    _flag_as_child(db, child)
    record = _verified_parental_consent(db, child)
    consent = _consent_for(db, child, purpose, cat, act)
    consent_service.grant_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    consent_service.withdraw_consent(db, consent, actor_username="test", source_app=SOURCE_APP)
    guardian_service.revoke_guardian_consent(db, record, actor_username="test")

    decision = evaluate_decision(db, child, purpose, cat, act, source_app=SOURCE_APP)
    assert decision.decision == "WITHDRAWN"


def test_a_dry_run_evaluation_writes_no_audit_row(db):
    ads, cat, act = _make_purpose(db, "advertising")
    child = _make_customer(db, "R114-DRYRUN")
    _flag_as_child(db, child)
    before = db.query(AuditLog).filter(AuditLog.customer_id == child.id).count()
    decision = evaluate_decision(db, child, ads, cat, act, source_app=SOURCE_APP, persist=False)
    assert decision.decision == "DENY"
    assert db.query(AuditLog).filter(AuditLog.customer_id == child.id).count() == before


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #
def test_age_assurance_endpoint_derives_the_child_flag(db, children_client, staff_token):
    customer = _make_customer(db, "R114-API-CHILD")
    resp = children_client.post(
        "/children/age-assurance",
        json={
            "customer_external_id": "R114-API-CHILD",
            "assurance_method": "IDENTITY_DOCUMENT",
            "date_of_birth": (date.today() - timedelta(days=365 * 9)).isoformat(),
            "assurance_reference": "KYC-77",
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_child"] is True
    assert body["is_verified"] is True
    # A child's date of birth is never echoed back over the API.
    assert "date_of_birth" not in body
    assert body["customer_id"] == customer.id


def test_the_api_will_not_let_a_caller_assert_a_child_is_an_adult(db, children_client, staff_token):
    _make_customer(db, "R114-API-ASSERT")
    resp = children_client.post(
        "/children/age-assurance",
        json={
            "customer_external_id": "R114-API-ASSERT",
            "assurance_method": "IDENTITY_DOCUMENT",
            "date_of_birth": (date.today() - timedelta(days=365 * 9)).isoformat(),
            "is_child": False,
        },
        headers=_auth(staff_token),
    )
    # `is_child` is not an input field anywhere, and extra="forbid" makes that
    # a 422 rather than a silently ignored key.
    assert resp.status_code == 422


def test_self_declaration_with_a_date_of_birth_is_refused_by_the_api(db, children_client, staff_token):
    _make_customer(db, "R114-API-SELFDECL")
    resp = children_client.post(
        "/children/age-assurance",
        json={
            "customer_external_id": "R114-API-SELFDECL",
            "assurance_method": "SELF_DECLARED",
            "declared_is_child": False,
            "date_of_birth": "2000-01-01",
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 422
    assert "DECLARED_DATE_OF_BIRTH" in resp.text


def test_guardian_consent_endpoints_create_verify_and_mask(db, children_client, staff_token):
    child = _make_customer(db, "R114-API-GUARDIAN")
    _flag_as_child(db, child)

    created = children_client.post(
        "/children/guardian-consents",
        json={
            "customer_external_id": "R114-API-GUARDIAN",
            "guardian_type": "PARENT",
            "verification_method": "VOLUNTARILY_PROVIDED_DETAILS",
            "guardian_name": "API Parent",
            "guardian_email": "api.parent@example.test",
            "guardian_identity_reference": "PAN-ABCDE1234F",
            "guardian_date_of_birth": (date.today() - timedelta(days=365 * 45)).isoformat(),
        },
        headers=_auth(staff_token),
    )
    assert created.status_code == 201, created.text
    ref = created.json()["reference_no"]
    assert created.json()["status"] == "PENDING"
    # The guardian's address is masked, and the raw identity material the
    # verification consumed is never returned.
    assert created.json()["guardian_email"] != "api.parent@example.test"
    assert "guardian_identity_reference" not in created.json()
    assert "virtual_token_reference" not in created.json()

    verified = children_client.post(
        f"/children/guardian-consents/{ref}/verify",
        json={"verification_note": "PAN checked against the register"},
        headers=_auth(staff_token),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"
    assert verified.json()["guardian_is_adult"] is True
    assert verified.json()["evidence_hash"]


def test_a_lawful_guardian_without_an_appointment_is_a_422(db, children_client, staff_token):
    _make_customer(db, "R114-API-LG")
    resp = children_client.post(
        "/children/guardian-consents",
        json={
            "customer_external_id": "R114-API-LG",
            "guardian_type": "LAWFUL_GUARDIAN",
            "verification_method": "VOLUNTARILY_PROVIDED_DETAILS",
            "guardian_name": "Unappointed",
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 422
    assert "R.11" in resp.text


def test_exemptions_are_created_inactive_and_need_a_separate_authorisation(
    db, children_client, staff_token
):
    resp = children_client.post(
        "/children/exemptions",
        json={
            "exemption_class": "EDUCATIONAL_INSTITUTION",
            "schedule_reference": "Fourth Schedule, Part A, entry 3",
            "purpose_codes": ["r114_api_exempt"],
            "exempted_obligations": ["VERIFIABLE_PARENTAL_CONSENT"],
            "conditions": "To the extent necessary for educational activities.",
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["is_active"] is False
    assert resp.json()["authorised_by"] is None
    exemption_id = resp.json()["id"]

    activated = children_client.post(
        f"/children/exemptions/{exemption_id}/activate",
        json={"justification": "The tenant is a recognised school under the RTE Act."},
        headers=_auth(staff_token),
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True
    assert activated.json()["authorised_by"] == "test-admin"


def test_an_exemption_with_an_empty_purpose_list_is_refused_by_the_api(
    db, children_client, staff_token
):
    resp = children_client.post(
        "/children/exemptions",
        json={
            "exemption_class": "SPECIFIED_PURPOSE",
            "schedule_reference": "Fourth Schedule, Part B",
            "purpose_codes": [],
            "exempted_obligations": ["VERIFIABLE_PARENTAL_CONSENT"],
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 422


def test_s9_2_is_not_offered_as_an_exemptible_obligation(db, children_client, staff_token):
    """R.12 does not offer a route to processing that harms a child, so the
    platform does not offer a switch for it."""
    from app.models.guardian import EXEMPTIBLE_OBLIGATIONS

    assert "DETRIMENTAL_EFFECT_PROHIBITION" not in EXEMPTIBLE_OBLIGATIONS
    assert len(EXEMPTIBLE_OBLIGATIONS) == 2

    resp = children_client.post(
        "/children/exemptions",
        json={
            "exemption_class": "SPECIFIED_PURPOSE",
            "schedule_reference": "Fourth Schedule, Part B",
            "purpose_codes": ["anything"],
            "exempted_obligations": ["DETRIMENTAL_EFFECT_PROHIBITION"],
        },
        headers=_auth(staff_token),
    )
    assert resp.status_code == 422


def test_metrics_report_k24_and_k25_without_inventing_ratios(db, children_client, staff_token):
    resp = children_client.get("/children/metrics", headers=_auth(staff_token))
    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    assert metrics["K-24"]["accounts"] >= 1
    assert "coverage_pct" in metrics["K-24"]
    assert metrics["K-25"]["child_accounts"] >= 1
    assert metrics["K-25"]["child_consent_blocked_no_parental_record"] >= 1


def test_metrics_report_none_rather_than_a_percentage_for_an_empty_set(db):
    """A tenant with no child accounts has not achieved 100% parental-consent
    completion - it has nothing to report."""
    empty_tenant = resolve_tenant_id(db, "R114_EMPTY_TENANT")
    metrics = guardian_service.children_metrics(db, tenant_id=empty_tenant)
    assert metrics["K-25"]["child_accounts"] == 0
    assert metrics["K-25"]["completion_pct"] is None
    assert metrics["K-24"]["coverage_pct"] is None
