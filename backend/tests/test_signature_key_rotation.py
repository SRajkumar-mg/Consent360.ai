"""HMAC key rotation must not turn compliance evidence into a false alarm.

`app/core/encryption.py::hmac_signature_matches` was written to accept a
signature produced under any key in the HMAC key ring, but nothing called it -
so every verification path still compared against the current primary key
only. The consequence is specific and bad: the moment an operator rotates
HMAC_SEARCH_KEY, every consent receipt, every disclosure log entry and every
consent-evidence row written before the rotation starts reporting itself as
TAMPERED. That is a false integrity alarm on exactly the artefacts a DPDP
audit rests on, and "the evidence says it was tampered with" is not a claim
anyone can walk back with an explanation about key management.

These tests do the rotation for real - write the artefact under one key,
rotate, then verify - rather than asserting the helper is merely imported.
"""
import base64
import os
from datetime import datetime, timezone

import pytest

from app.core import encryption
from app.core.config import get_settings
from app.models.entities import (
    Consent,
    ConsentEvidence,
    ConsentReceipt,
    Customer,
    DataCategory,
    DataSharingEvent,
    ProcessingActivity,
    Processor,
    Purpose,
    PurposeVersion,
)
from app.services import consent as consent_service
from app.services.receipts import verify_receipt
from app.services.tenancy import resolve_tenant_id

SOURCE_APP = "ROTATE_TEST"


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _reload_key_state() -> None:
    """Changing HMAC_SEARCH_KEY mid-process needs BOTH caches cleared -
    get_settings()'s lru_cache and the encryption module's own key ring. See
    tests/test_encryption_hardening.py's module docstring."""
    get_settings.cache_clear()
    encryption._reset_for_tests()


@pytest.fixture()
def initial_hmac_key(monkeypatch):
    key = _key()
    monkeypatch.setenv("HMAC_SEARCH_KEY", key)
    monkeypatch.delenv("HMAC_SEARCH_KEY_PREVIOUS", raising=False)
    _reload_key_state()
    yield key
    # monkeypatch restores the environment; both caches must be rebuilt from
    # it or every later test in the session keeps this test's key ring.
    _reload_key_state()


@pytest.fixture()
def rotate_hmac_key(monkeypatch):
    """Returns a callable that rotates the HMAC key ring: the new key becomes
    primary and the previous one is retained as a fallback, which is exactly
    what an operator following the documented rotation does."""
    def _rotate(previous: str) -> str:
        new = _key()
        monkeypatch.setenv("HMAC_SEARCH_KEY", new)
        monkeypatch.setenv("HMAC_SEARCH_KEY_PREVIOUS", previous)
        _reload_key_state()
        return new

    yield _rotate
    _reload_key_state()


def _make_purpose(db, code):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, legal_basis="CONSENT", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose, category, activity


def _make_customer(db, external_id):
    customer = Customer(
        external_id=external_id, name="Rotation Principal", source_app=SOURCE_APP,
        tenant_id=resolve_tenant_id(db, SOURCE_APP),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


# --------------------------------------------------------------------------- #
# The helper itself
# --------------------------------------------------------------------------- #
def test_hmac_signature_matches_accepts_a_pre_rotation_signature(initial_hmac_key, rotate_hmac_key):
    signature = encryption.hmac_signature("payload-hash")
    assert encryption.hmac_signature_matches(signature, "payload-hash")

    rotate_hmac_key(initial_hmac_key)

    # The naive comparison every call site used to make now fails...
    assert encryption.hmac_signature("payload-hash") != signature
    # ...while the ring-aware check still recognises the old, valid signature.
    assert encryption.hmac_signature_matches(signature, "payload-hash")
    # And a genuinely tampered payload is still rejected under every key.
    assert not encryption.hmac_signature_matches(signature, "different-payload-hash")


# --------------------------------------------------------------------------- #
# Receipts
# --------------------------------------------------------------------------- #
def test_a_receipt_issued_before_a_rotation_still_verifies(db, initial_hmac_key, rotate_hmac_key):
    purpose, category, activity = _make_purpose(db, "rotate_receipt")
    customer = _make_customer(db, "ROT-CUST-RECEIPT-001")
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    )
    consent_service.grant_consent(db, consent, source_app=SOURCE_APP)
    db.commit()

    receipt = db.query(ConsentReceipt).filter(ConsentReceipt.consent_id == consent.id).one()
    assert verify_receipt(receipt) is True

    rotate_hmac_key(initial_hmac_key)

    assert verify_receipt(receipt) is True, (
        "a receipt issued before an HMAC key rotation must not report itself as tampered"
    )

    # Tampering is still detected after rotation - the point is not to make
    # verification permissive, only key-agnostic.
    receipt.payload = {**receipt.payload, "injected": "value"}
    assert verify_receipt(receipt) is False


# --------------------------------------------------------------------------- #
# Consent evidence
# --------------------------------------------------------------------------- #
def test_consent_evidence_reports_its_own_integrity_and_survives_rotation(
    db, initial_hmac_key, rotate_hmac_key
):
    purpose, category, activity = _make_purpose(db, "rotate_evidence")
    customer = _make_customer(db, "ROT-CUST-EVIDENCE-001")
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    )
    consent_service.grant_consent(db, consent, source_app=SOURCE_APP)
    db.commit()

    evidence = db.query(ConsentEvidence).filter(ConsentEvidence.consent_id == consent.id).one()
    assert evidence.signature, "grant must sign its evidence"
    assert evidence.signature_valid is True

    rotate_hmac_key(initial_hmac_key)
    assert evidence.signature_valid is True, (
        "evidence written before an HMAC key rotation must not report itself as tampered"
    )

    original_hash = evidence.content_hash
    evidence.content_hash = "0" * 64
    assert evidence.signature_valid is False, "a direct edit of the hash must be detected"
    evidence.content_hash = original_hash


def test_unsigned_evidence_reports_none_not_tampered(db, initial_hmac_key):
    """A legacy row written before the signature column was populated has
    nothing to verify. Reporting it as False would accuse an untouched record
    of tampering, which is worse than reporting nothing."""
    purpose, category, activity = _make_purpose(db, "rotate_unsigned")
    customer = _make_customer(db, "ROT-CUST-UNSIGNED-001")
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app=SOURCE_APP
    )
    consent_service.grant_consent(db, consent, source_app=SOURCE_APP)
    db.commit()

    evidence = db.query(ConsentEvidence).filter(ConsentEvidence.consent_id == consent.id).one()
    evidence.signature = None
    assert evidence.signature_valid is None


# --------------------------------------------------------------------------- #
# Disclosure log
# --------------------------------------------------------------------------- #
def test_a_sharing_event_signed_before_a_rotation_still_verifies(
    db, initial_hmac_key, rotate_hmac_key
):
    from app.api.routes.sharing_events import _sign, _signature_valid

    purpose, _cat, _act = _make_purpose(db, "rotate_sharing")
    customer = _make_customer(db, "ROT-CUST-SHARING-001")
    processor = Processor(name="Rotation Processor", type="ANALYTICS", country="IN", is_active=True)
    db.add(processor)
    db.commit()
    db.refresh(processor)

    event = DataSharingEvent(
        tenant_id=customer.tenant_id, customer_id=customer.id, processor_id=processor.id,
        purpose_id=purpose.id, data_category_ids=[], event_type="SENT",
        legal_basis="CONSENT", actor_username="test", source_app=SOURCE_APP,
        signature="", occurred_at=datetime.now(timezone.utc),
    )
    event.signature = _sign(event)
    db.add(event)
    db.commit()
    db.refresh(event)
    assert _signature_valid(event) is True

    rotate_hmac_key(initial_hmac_key)
    assert _signature_valid(event) is True

    event.event_type = "DENIED"
    assert _signature_valid(event) is False


# --------------------------------------------------------------------------- #
# The console email sender must not put a principal's address or an OTP in a log
# --------------------------------------------------------------------------- #
def test_console_email_sender_masks_the_recipient_and_withholds_the_body(caplog, monkeypatch):
    import logging

    from app.integrations.notifications.email import ConsoleEmailSender

    monkeypatch.delenv("EMAIL_CONSOLE_ECHO_BODY", raising=False)
    caplog.set_level(logging.DEBUG, logger="app.notifications.email")
    logging.getLogger("app.notifications.email").disabled = False

    ConsoleEmailSender().send(
        to="data.principal@example.com", subject="Your verification code",
        body="Your Consent360 verification code is 483920.",
    )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text, "the sender must still record that a message was sent"
    assert "483920" not in text, "an OTP must never reach a log by default"
    assert "data.principal@example.com" not in text
    assert "da***@example.com" in text
    assert "Your verification code" in text


def test_console_email_body_echo_is_an_explicit_opt_in(caplog, monkeypatch):
    import logging

    from app.integrations.notifications.email import ConsoleEmailSender

    monkeypatch.setenv("EMAIL_CONSOLE_ECHO_BODY", "true")
    caplog.set_level(logging.DEBUG, logger="app.notifications.email")
    logging.getLogger("app.notifications.email").disabled = False

    ConsoleEmailSender().send(to="demo@example.com", subject="Code", body="code is 111222")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "111222" in text, "the local-demo escape hatch must still work when asked for"
    assert "demo@example.com" not in text, "even then the recipient stays masked"


def test_console_email_body_echo_is_refused_in_production(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.notifications.email import _echo_body_enabled

    monkeypatch.setenv("EMAIL_CONSOLE_ECHO_BODY", "true")
    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert _echo_body_enabled() is False


# --------------------------------------------------------------------------- #
# alembic/env.py must not disable every logger created before it runs
# --------------------------------------------------------------------------- #
def test_alembic_env_does_not_disable_existing_loggers():
    """conftest.py runs `alembic upgrade head` in process at session start.
    With fileConfig's default (disable_existing_loggers=True) that permanently
    kills every logger created during the import of app.*, so a test asserting
    on log output - including the access log's PII redaction - would pass
    against an empty record list. A false green on a security control is worse
    than a broken test."""
    from pathlib import Path

    env_py = (Path(__file__).resolve().parents[1] / "alembic" / "env.py").read_text()
    assert "fileConfig(config.config_file_name, disable_existing_loggers=False)" in env_py

    # And the effect: a logger created before the migrations ran is still live.
    import logging

    assert not logging.getLogger("app.notifications.email").disabled
    assert not logging.getLogger("app.processors").disabled


# --------------------------------------------------------------------------- #
# users.token_version
# --------------------------------------------------------------------------- #
def test_users_carry_a_durable_token_version(db, staff_token):
    """The column half of durable token revocation. Minting the claim and
    checking it per request live in app/core/security.py and app/api/deps.py,
    which this lane does not own - so this asserts the storage exists and
    defaults to a well-defined generation, not that revocation works yet.

    `staff_token` is requested only for the staff user it creates."""
    from app.models.entities import User

    user = db.query(User).order_by(User.id.asc()).first()
    assert user is not None
    assert user.token_version == 0

    user.token_version = user.token_version + 1
    db.commit()
    db.refresh(user)
    assert user.token_version == 1
    user.token_version = 0
    db.commit()


def test_organization_login_response_can_carry_a_refresh_token():
    """Schema half of the org-refresh gap: organization access tokens expire
    in 60 minutes with no refresh route, forcing an hourly re-login. The route
    lives in app/api/routes/organizations.py, another workstream's file."""
    from app.schemas.schemas import OrganizationLoginResponse, OrganizationRefreshRequest

    assert "refresh_token" in OrganizationLoginResponse.model_fields
    assert OrganizationLoginResponse.model_fields["refresh_token"].default is None
    assert "refresh_token" in OrganizationRefreshRequest.model_fields


# --------------------------------------------------------------------------- #
# Sec-GPC on the CRM cookie-banner path
#
# CRM, Codex and SkillLearn all record cookie-banner choices through
# PUT /crm/customers/{id}/consent-preferences, never through /portal/grant.
# Only portal.py read the Sec-GPC header, so for three of the four demo sites
# ConsentEvidence.gpc_signal stayed permanently NULL and only the
# client-claimed value was ever recorded - the weaker of the two signals that
# column separation exists to distinguish.
# --------------------------------------------------------------------------- #
def _crm_cookie_purpose(db, code="analytics"):
    """The cookie-banner path only touches purposes whose code is one of
    COOKIE_CATEGORY_TO_PURPOSE's values."""
    from app.models.entities import DataCategory, ProcessingActivity

    existing = db.query(Purpose).filter(Purpose.code == code).first()
    if existing:
        return existing
    category = DataCategory(name=f"cat-{code}", code=f"cat_gpc_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_gpc_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Cookie {code}", code=code, legal_basis="CONSENT", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent to analytics cookies.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose


def _crm_customer(db, email):
    from app.core.encryption import hmac_digest
    from app.models.entities import CrmCustomer

    row = CrmCustomer(name="GPC CRM Customer", email=email, email_search=hmac_digest(email))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _evidence_for(db, email, purpose_code="analytics"):
    from app.services.tenancy import resolve_customer

    # CRM_PORTAL is the source_app _link_customer stamps for a CRM directory
    # record with no other origin - see app/api/routes/crm.py::CRM_SOURCE_APP.
    customer = resolve_customer(db, source_app="CRM_PORTAL", email=email)
    assert customer is not None, "the CRM path must have linked a platform Customer"
    # Scope to the purpose the banner actually toggled. The CRM sync writes a
    # consent row per purpose in the tenant's matrix, so "highest id for this
    # customer" is whichever purpose sorted last, not the one under test.
    #
    # "highest id that carries evidence" used to be a good enough proxy,
    # because the untoggled purposes were left NOT_REQUESTED with no evidence
    # at all. That is exactly the B-01/B-02 defect: a category the banner sends
    # as false (or omits, which `categories.get(key, False)` reads the same
    # way) is now RECORDED as a refusal, with its own evidence row - so every
    # purpose in the matrix carries evidence and the proxy stopped selecting
    # the toggled one. Naming the purpose is what this always meant.
    consent = (
        db.query(Consent)
        .join(Purpose, Purpose.id == Consent.purpose_id)
        .filter(Consent.customer_id == customer.id, Purpose.code == purpose_code)
        .join(ConsentEvidence, ConsentEvidence.consent_id == Consent.id)
        .order_by(Consent.id.desc())
        .first()
    )
    assert consent is not None, "the CRM path must have written a consent with evidence"
    return consent.evidence[-1]


def test_crm_consent_preferences_records_the_server_observed_gpc_header(db, client):
    _crm_cookie_purpose(db)
    email = "gpc-crm-observed@example.com"
    crm_customer = _crm_customer(db, email)

    resp = client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        headers={"Sec-GPC": "1"},
        json={"lang": "en", "categories": {"analytics": True},
              "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200
    assert _evidence_for(db, email).gpc_signal is True


def test_crm_consent_preferences_records_gpc_false_for_a_non_1_header(db, client):
    _crm_cookie_purpose(db)
    email = "gpc-crm-zero@example.com"
    crm_customer = _crm_customer(db, email)

    resp = client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        headers={"Sec-GPC": "0"},
        json={"lang": "en", "categories": {"analytics": True},
              "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200
    assert _evidence_for(db, email).gpc_signal is False


def test_crm_consent_preferences_leaves_gpc_null_when_the_header_is_absent(db, client):
    _crm_cookie_purpose(db)
    email = "gpc-crm-absent@example.com"
    crm_customer = _crm_customer(db, email)

    resp = client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        json={"lang": "en", "categories": {"analytics": True},
              "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200
    assert _evidence_for(db, email).gpc_signal is None


def test_crm_withdrawal_via_the_banner_also_records_gpc(db, client):
    """The withdraw branch matters more than the grant one: a GPC signal is an
    objection to processing, so the toggle-off path is where it is most likely
    to be asserted."""
    _crm_cookie_purpose(db)
    email = "gpc-crm-withdraw@example.com"
    crm_customer = _crm_customer(db, email)

    assert client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        json={"lang": "en", "categories": {"analytics": True},
              "context": {"affirmative_action": "CLICK"}},
    ).status_code == 200

    resp = client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        headers={"Sec-GPC": "1"},
        json={"lang": "en", "categories": {"analytics": False},
              "context": {"affirmative_action": "CLICK"}},
    )
    assert resp.status_code == 200
    evidence = _evidence_for(db, email)
    assert evidence.gpc_signal is True

    consent = db.get(Consent, evidence.consent_id)
    assert consent.status == "WITHDRAWN"
