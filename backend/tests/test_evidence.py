from app.models.entities import Customer, DataCategory, Notice, NoticeVersion, ProcessingActivity, Purpose, PurposeVersion
from app.services import consent as consent_service
from app.services.kpi import _is_complete, evidence_completeness
from app.services.tenancy import platform_tenant_id


def _make_purpose(db, code):
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


def _publish_notice(db, purpose) -> NoticeVersion:
    """R1-04: a published NoticeVersion for `purpose`, so evidence created
    against it gets a real (non-null) notice_version_id/notice_hash instead
    of the pre-R1-04 placeholder this test file used to assert on."""
    notice = Notice(tenant_id=platform_tenant_id(db), purpose_id=purpose.id, status="ACTIVE", current_version=1)
    db.add(notice)
    db.flush()
    nv = NoticeVersion(
        notice_id=notice.id, version_number=1, title=f"Notice for {purpose.name}",
        body="This is the published notice body.", content_hash="test-content-hash",
        is_current=True, created_by="test",
    )
    db.add(nv)
    db.commit()
    db.refresh(nv)
    return nv


def test_portal_grant_records_full_client_context(db, client):
    purpose, category, activity = _make_purpose(db, "evidence_portal")
    notice_version = _publish_notice(db, purpose)
    from datetime import datetime, timedelta, timezone

    from app.core.security import create_context_token
    from app.models.entities import ConsentContext

    customer = Customer(external_id="CUST-EVID-001", name="Evidence Customer", source_app="EVID_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, "EVID_TEST")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="EVID_TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token},
        json={
            "purpose_code": purpose.code,
            "context": {
                "language": "hi", "banner_version": "v1", "ui_control_id": "grant-btn",
                "session_id": "sess-123", "affirmative_action": "TOGGLE", "interaction_step": 1,
            },
        },
    )
    assert resp.status_code == 200
    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    evidence = consent.evidence[-1]
    assert evidence.language == "hi"
    assert evidence.banner_version == "v1"
    assert evidence.ui_control_id == "grant-btn"
    assert evidence.affirmative_action == "TOGGLE"
    # R1-04: notice_version_id now points at the real, published NoticeVersion
    # for this purpose (never the pre-R1-04 purpose_version_id placeholder),
    # and the consent itself pins the same notice version (A-07/D-04).
    assert evidence.notice_version_id == notice_version.id
    assert evidence.notice_hash == notice_version.content_hash
    assert consent.notice_version_id == notice_version.id
    assert evidence.content_hash is not None


def test_document_action_without_reference_is_rejected(db, client, staff_token):
    purpose, category, activity = _make_purpose(db, "evidence_doc")
    customer = Customer(external_id="CUST-EVID-002", name="Doc Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    db.commit()

    resp = client.post(
        f"/consents/{consent.id}/grant",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"context": {"affirmative_action": "DOCUMENT"}},
    )
    assert resp.status_code == 422


def test_otp_action_without_reference_is_rejected(db, client, staff_token):
    """R1-03 finding: affirmative_action=OTP previously required no proof at
    all (only DOCUMENT/CALL were enforced) - a staff-driven grant could claim
    an OTP-verified consent with no reference and no OTP."""
    purpose, category, activity = _make_purpose(db, "evidence_otp_staff")
    customer = Customer(external_id="CUST-EVID-OTP-STAFF", name="OTP Staff Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    db.commit()

    resp = client.post(
        f"/consents/{consent.id}/grant",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"context": {"affirmative_action": "OTP"}},
    )
    assert resp.status_code == 422
    # The rejected call already flushed (but never committed) a status
    # mutation and history/audit rows onto this shared session; roll back so
    # the next call sees the consent exactly as a fresh request would (a real
    # request gets its own session per call - this fixture's session is
    # reused across calls within one test).
    db.rollback()

    # Supplying a reference is accepted, same as DOCUMENT/CALL.
    resp = client.post(
        f"/consents/{consent.id}/grant",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"context": {"affirmative_action": "OTP", "affirmative_reference": "OTP-SESSION-123"}},
    )
    assert resp.status_code == 200


def test_portal_otp_claim_requires_and_derives_from_a_verified_context(db, client):
    """R1-03 finding: where the collection path (the self-service portal) has
    an actual verified ConsentContext in hand, an affirmative_action=OTP
    claim must be backed by that real verification, and its reference is
    derived server-side rather than trusted from the client - a client
    cannot fabricate an OTP reference string of its own choosing."""
    from datetime import datetime, timedelta, timezone

    from app.core.security import create_context_token
    from app.models.entities import ConsentContext

    purpose, category, activity = _make_purpose(db, "evidence_otp_portal")

    # Case 1: OTP claimed on a context that was never actually OTP-verified -> rejected.
    customer_unverified = Customer(external_id="CUST-EVID-OTP-UNVERIFIED", name="Unverified", source_app="EVID_TEST")
    db.add(customer_unverified)
    db.commit()
    db.refresh(customer_unverified)
    token_unverified = create_context_token(customer_unverified.id, "EVID_TEST")
    db.add(ConsentContext(
        customer_id=customer_unverified.id, token=token_unverified, source_app="EVID_TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method=None,
    ))
    db.commit()
    resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token_unverified},
        json={"purpose_code": purpose.code, "context": {"affirmative_action": "OTP"}},
    )
    assert resp.status_code == 422
    # See the equivalent rollback in test_otp_action_without_reference_is_rejected:
    # this fixture's session is shared across calls within one test, so the
    # rejected call's flushed-but-uncommitted state must be discarded before
    # the next (successful) call, or that call's own commit would sweep it
    # in too - something a real per-request session never does.
    db.rollback()

    # Case 2: a genuinely OTP-verified context - the claim is accepted, and
    # the stored reference is the server's own record, not anything the
    # client supplied (a fabricated reference is ignored, not trusted).
    customer = Customer(external_id="CUST-EVID-OTP-VERIFIED", name="Verified", source_app="EVID_TEST")
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, "EVID_TEST")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="EVID_TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/grant",
        headers={"X-Context-Token": token},
        json={
            "purpose_code": purpose.code,
            "context": {
                "affirmative_action": "OTP",
                "affirmative_reference": "a-fabricated-reference-the-client-made-up",
            },
        },
    )
    assert resp.status_code == 200
    consent = (
        db.query(consent_service.Consent)
        .filter(consent_service.Consent.customer_id == customer.id, consent_service.Consent.purpose_id == purpose.id)
        .first()
    )
    evidence = consent.evidence[-1]
    assert evidence.affirmative_action == "OTP"
    reference = evidence.details.get("affirmative_reference")
    assert reference is not None
    assert reference != "a-fabricated-reference-the-client-made-up"
    assert "otp-verified-context" in reference


def test_crm_preference_sync_records_evidence_with_threaded_context_and_observed_ip(db, client):
    """R1-03 finding: PUT /crm/customers/{id}/consent-preferences had no
    test. Assert the resulting evidence row carries the client-context
    fields the CRM banner threads through (language, session id) AND that
    the server-observed IP/UA - not whatever the request body claimed - are
    the ones recorded as fact."""
    from app.core.encryption import hmac_digest
    from app.models.entities import CrmCustomer

    category = DataCategory(name="cat-crm-func", code="cat_crm_func")
    activity = ProcessingActivity(name="act-crm-func", code="act_crm_func")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name="Functional Cookies", code="functional", requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent to functional cookies.", is_current=True, created_by="test",
    ))
    db.commit()

    email = "crm-pref-evidence-test@example.com"
    crm_customer = CrmCustomer(name="CRM Pref Customer", email=email, email_search=hmac_digest(email))
    db.add(crm_customer)
    db.commit()
    db.refresh(crm_customer)

    resp = client.put(
        f"/crm/customers/{crm_customer.id}/consent-preferences",
        headers={"User-Agent": "server-observed-ua/1.0"},
        json={
            "lang": "en",
            "categories": {"functional": True},
            "context": {
                "language": "en",
                "session_id": "crm-sess-1",
                "ip_address": "203.0.113.99",
                "user_agent": "client-claimed-ua/9.9",
            },
        },
    )
    assert resp.status_code == 200

    consent = (
        db.query(consent_service.Consent)
        .join(Customer, consent_service.Consent.customer_id == Customer.id)
        .filter(Customer.email_search == hmac_digest(email))
        # Scope to the purpose this test toggled. The CRM sync writes a
        # consent row per purpose in the tenant's matrix, so an unscoped
        # .first() silently returns whichever row happens to come back first
        # - and only the toggled one carries the evidence being asserted on.
        # It passed while this file ran alone and failed as soon as another
        # test file added purposes ahead of it.
        .filter(consent_service.Consent.purpose_id == purpose.id)
        .order_by(consent_service.Consent.id.desc())
        .first()
    )
    assert consent is not None
    evidence = consent.evidence[-1]
    assert evidence.session_id == "crm-sess-1"
    assert evidence.language == "en"

    # Server-observed values are authoritative and differ from the claim.
    assert evidence.ip_address is not None
    assert evidence.ip_address != "203.0.113.99"
    assert evidence.user_agent == "server-observed-ua/1.0"

    # The client's claimed values survive only in a clearly separate,
    # non-authoritative field - never overwriting the observed columns.
    assert evidence.details.get("claimed_ip_address") == "203.0.113.99"
    assert evidence.details.get("claimed_user_agent") == "client-claimed-ua/9.9"


def test_grant_persists_evidence_ref_onto_history_details(db):
    """ConsentHistory.details used to be a plain JSON column.
    grant_consent/renew_consent/withdraw_consent backfill it in place
    (`consent.history[-1].details["evidence_ref"] = ...`) after creating the
    ConsentEvidence row - a plain JSON column never notices an in-place dict
    mutation (only a whole-attribute replacement), so that write was
    silently discarded and never reached the database. Force a fresh read
    from the DB (expire_all, not just re-reading the in-memory object) to
    prove it now actually persists."""
    from app.models.entities import ConsentHistory

    purpose, category, activity = _make_purpose(db, "evidence_history_mutation")
    customer = Customer(external_id="CUST-EVID-HIST-MUT", name="History Mutation Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    consent_service.grant_consent(db, consent, source_app="EVID_TEST")

    # Discard all in-memory ORM state and reload from the database, so this
    # assertion can only pass if the mutation actually reached a row - not
    # because the in-memory Python dict already happened to hold the value.
    db.expire_all()
    history_row = (
        db.query(ConsentHistory)
        .filter(ConsentHistory.consent_id == consent.id, ConsentHistory.action == "CONSENT_GRANTED")
        .order_by(ConsentHistory.id.desc())
        .first()
    )
    assert history_row is not None
    assert history_row.details.get("evidence_ref") is not None
    assert history_row.details["evidence_ref"] == consent.evidence[-1].evidence_ref


def test_evidence_signature_can_be_recomputed_and_detects_tampering(db):
    """R1-03 finding: consent_evidence.signature was written by the model
    but never populated by _create_evidence - dead schema. It must now be a
    real HMAC over (content_hash, notice_hash) that can be recomputed to
    verify a row, and that changes if either hash is tampered with (e.g. a
    direct DB edit bypassing the ORM/consent lifecycle entirely)."""
    from app.core.encryption import hmac_signature

    purpose, category, activity = _make_purpose(db, "evidence_signature")
    customer = Customer(external_id="CUST-EVID-SIG", name="Signature Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    consent_service.grant_consent(db, consent, source_app="EVID_TEST")
    db.commit()

    evidence = consent.evidence[-1]
    assert evidence.signature is not None
    assert evidence.signature == hmac_signature(evidence.content_hash, evidence.notice_hash)

    # Tamper with the stored content_hash directly (bypassing the ORM's own
    # write path, the way a raw DB edit or a compromised admin session
    # would) - recomputing the signature from the row's current hashes must
    # no longer match what was stored at collection time.
    tampered_hash = "0" * 64
    assert tampered_hash != evidence.content_hash
    evidence.content_hash = tampered_hash
    db.commit()
    db.refresh(evidence)
    assert evidence.signature != hmac_signature(evidence.content_hash, evidence.notice_hash)


def test_evidence_completeness_kpi_reports_full_coverage(db):
    """R1-04 changed what "complete" evidence means: notice_hash is only
    non-null once a real Notice is published for the purpose (previously it
    was an always-populated placeholder). `evidence_completeness` is a
    whole-database aggregate with no per-tenant/per-test filter, and this
    `db` fixture is shared for the whole pytest session with no per-test
    rollback, so asserting a global 100% here would be coupled to every
    other test's fixture data rather than to this test's own scenario.
    Assert the two things this test actually controls instead: (1) the
    consent this test creates - purpose has a published notice, so its
    evidence has both hashes - is genuinely counted as complete by the real
    `_is_complete` the KPI uses, and (2) the aggregate total/complete counts
    include it."""
    purpose, category, activity = _make_purpose(db, "evidence_kpi")
    _publish_notice(db, purpose)
    customer = Customer(external_id="CUST-EVID-003", name="KPI Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    before = evidence_completeness(db)
    consent_service.grant_consent(db, consent, source_app="EVID_TEST")
    db.commit()
    db.refresh(consent)

    evidence = consent.evidence[-1]
    assert evidence.notice_hash is not None
    assert evidence.content_hash is not None
    assert _is_complete(evidence.notice_hash, evidence.content_hash, evidence.affirmative_action, evidence.details) is True

    after = evidence_completeness(db)
    assert after["total_active_consents"] == before["total_active_consents"] + 1
    assert after["consents_with_complete_evidence"] == before["consents_with_complete_evidence"] + 1


def test_is_complete_requires_hashes_and_a_reference_when_one_is_claimed():
    """Unit-level proof that completeness now measures something real: two
    non-nullable columns with defaults (the old check) are true of every row
    regardless of whether it proves anything; missing hashes or an
    unsubstantiated DOCUMENT/CALL/OTP claim must both read as incomplete."""
    assert _is_complete("notice-hash", "content-hash", "CLICK", {}) is True
    assert _is_complete(None, "content-hash", "CLICK", {}) is False
    assert _is_complete("notice-hash", None, "CLICK", {}) is False
    assert _is_complete("notice-hash", "content-hash", "DOCUMENT", {}) is False
    assert _is_complete("notice-hash", "content-hash", "DOCUMENT", None) is False
    assert _is_complete("notice-hash", "content-hash", "DOCUMENT", {"affirmative_reference": "DOC-1"}) is True
    assert _is_complete("notice-hash", "content-hash", "OTP", {"affirmative_reference": "otp-ctx-1"}) is True


def test_evidence_completeness_kpi_counts_an_incomplete_row_as_incomplete(db):
    """Integration-level proof for the same claim, wired through the actual
    SQL query: a GRANTED consent whose only evidence row claims DOCUMENT
    proof but has no reference on file (e.g. a legacy/corrupted row -
    _create_evidence itself refuses to create one like this through the
    API) must not be counted as complete."""
    from app.models.entities import ConsentEvidence

    purpose, category, activity = _make_purpose(db, "evidence_kpi_incomplete")
    customer = Customer(external_id="CUST-EVID-KPI-INCOMPLETE", name="KPI Incomplete Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    consent.status = "GRANTED"
    db.add(ConsentEvidence(
        consent_id=consent.id,
        evidence_ref=f"EV-INCOMPLETE-{consent.id}",
        collected_by="test",
        collection_method="UI",
        source_app="EVID_TEST",
        consent_version=consent.consent_version,
        purpose_version=1,
        affirmative_action="DOCUMENT",
        notice_hash="hash-present",
        content_hash="hash-present",
        details={},  # DOCUMENT claimed, but no affirmative_reference on file
    ))
    db.commit()

    result = evidence_completeness(db)
    # total_active_consents grew by this consent but with_evidence did not,
    # so the percentage must be strictly below 100% - deterministically,
    # regardless of what other tests have already added to the shared
    # database (adding one active-but-incomplete consent can only pull the
    # ratio down from wherever it already was).
    assert result["evidence_completeness_pct"] < 100.0


def test_withdraw_persists_evidence_ref_onto_history_details(db):
    """R1-13: withdraw_consent's own docstring says it now mirrors
    grant_consent/renew_consent by writing a ConsentEvidence row on
    withdrawal (a real gap it names explicitly: "Withdrawal previously
    recorded a ConsentHistory row but never a ConsentEvidence row"), but
    that claim had no direct test - every existing test that calls
    withdraw_consent uses it only as scaffolding for something else.
    Companion to test_grant_persists_evidence_ref_onto_history_details
    above, same in-place-mutation risk (ConsentHistory.details is a plain
    JSON column; a dict mutated in place rather than reassigned is not
    detected as a change and is silently dropped), same fix, proven the
    same way: force a fresh read from the DB rather than trusting the
    in-memory object."""
    from app.models.entities import ConsentHistory

    purpose, category, activity = _make_purpose(db, "evidence_withdraw_mutation")
    customer = Customer(external_id="CUST-EVID-WITHDRAW-MUT", name="Withdraw Mutation Customer", source_app="EVID_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="EVID_TEST")
    consent_service.grant_consent(db, consent, source_app="EVID_TEST")
    evidence_count_after_grant = len(consent.evidence)

    consent_service.withdraw_consent(db, consent, source_app="EVID_TEST")

    assert consent.status == "WITHDRAWN"
    assert len(consent.evidence) == evidence_count_after_grant + 1

    db.expire_all()
    history_row = (
        db.query(ConsentHistory)
        .filter(ConsentHistory.consent_id == consent.id, ConsentHistory.action == "CONSENT_WITHDRAWN")
        .order_by(ConsentHistory.id.desc())
        .first()
    )
    assert history_row is not None
    assert history_row.details.get("evidence_ref") is not None
    assert history_row.details["evidence_ref"] == consent.evidence[-1].evidence_ref
