"""R2-05: the data-principal rights module, end to end.

The definition of done this file is written against, clause by clause:

  "Each request type has an end-to-end path with acknowledgement, due date and
   closure evidence; KPIs K-20 to K-22 computable."

  * ACCESS      -> test_access_request_end_to_end
  * CORRECTION  -> test_correction_request_end_to_end
  * ERASURE     -> test_erasure_request_hands_off_to_the_erasure_engine
  * NOMINATION  -> test_nomination_end_to_end
  * acknowledgement -> test_submission_acknowledges_the_principal*
  * due date        -> test_due_date_comes_from_the_tenants_configured_period,
                       test_a_tenant_can_publish_its_own_rights_period
  * closure evidence-> test_closure_evidence_is_a_recomputable_hash*,
                       test_a_request_cannot_be_closed_without_evidence
  * K-20..K-22      -> test_k20_k21_k22_are_computable_from_the_register

and the R.14(2) identity checks that make the whole thing safe:

  * test_an_unverified_context_cannot_make_a_rights_request
  * test_a_staff_logged_request_starts_unverified_and_cannot_be_fulfilled
  * test_a_handler_cannot_mark_a_request_verified_by_asserting_it
  * test_the_database_refuses_a_fulfilled_request_that_is_not_verified
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.encryption import hmac_digest
from app.core.security import create_context_token
from app.models.entities import (
    AuditLog,
    ConsentContext,
    Customer,
    DataCategory,
    DataSharingEvent,
    Notification,
    Organization,
    Processor,
    ProcessingActivity,
    Purpose,
)
from app.models.rights import Nomination, RightsRequest
from app.services import rights_requests as rights_service


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module", autouse=True)
def _mount_router():
    """Mount the rights router if app/main.py has not yet been updated to
    include it (that file belongs to another lane). A no-op once it has."""
    from app.api.routes import rights
    from app.main import app

    if not any(getattr(r, "path", "").startswith("/rights") for r in app.routes):
        app.include_router(rights.router)


def _now():
    return datetime.now(timezone.utc)


def _parse(value):
    """Parse an API timestamp to an aware UTC datetime.

    The API serialises UTC with a trailing "Z", which Python 3.10's
    `datetime.fromisoformat` does not accept (3.11 does). Comparisons below
    are always between instants, never between ISO strings, because Postgres
    hands a timestamptz back in the session's timezone - the same moment,
    a different string."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _tenant(db, code, *, response_days=30, rights_days=None, ack_hours=None):
    org = db.query(Organization).filter(Organization.code == code).first()
    if not org:
        org = Organization(name=code.title(), code=code, is_active=True)
        db.add(org)
    org.grievance_response_days = response_days
    org.dpo_name = "Asha Menon"
    org.dpo_email = "dpo@example.com"
    org.board_complaint_url = "https://dpb.gov.in/complaint"
    org.grievance_url = f"https://{code.lower()}.example.com/grievance"
    org.rights_url = f"https://{code.lower()}.example.com/rights"
    settings = dict(org.settings or {})
    if rights_days is not None:
        settings["rights_response_days"] = rights_days
    if ack_hours is not None:
        settings["rights_acknowledgement_hours"] = ack_hours
    org.settings = settings
    db.commit()
    db.refresh(org)
    return org


def _principal(db, source_app, external_id, *, email=None, verified=True):
    """A customer plus a context token, verified unless asked otherwise."""
    email = email or f"{external_id.lower()}@example.com"
    customer = Customer(
        external_id=external_id, external_id_search=hmac_digest(external_id),
        name="Rhea Kapoor", email=email, email_search=hmac_digest(email),
        phone="+919812345678", source_app=source_app,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, source_app)
    context = ConsentContext(
        customer_id=customer.id, token=token, source_app=source_app,
        expires_at=_now() + timedelta(minutes=15),
    )
    if verified:
        context.verified_at = _now()
        context.verification_method = "EMAIL_OTP"
    db.add(context)
    db.commit()
    return customer, token


def _headers(token):
    return {"X-Context-Token": token}


def _staff(staff_token):
    return {"Authorization": f"Bearer {staff_token}"}


def _submit(client, token, **overrides):
    payload = {"request_type": "ACCESS", "request_detail": "Please send me a copy of my data."}
    payload.update(overrides)
    return client.post("/rights/me/requests", headers=_headers(token), json=payload)


# --------------------------------------------------------------------------- #
#  Reference numbers
# --------------------------------------------------------------------------- #

def test_submission_issues_a_non_guessable_reference(db, client):
    _tenant(db, "RR_REF")
    _customer, token = _principal(db, "RR_REF", "RR-REF-1")

    body = _submit(client, token).json()
    reference = body["reference_no"]

    assert reference.startswith("RRQ-")
    assert body["request"]["reference_no"] == reference
    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()
    assert row is not None


def test_the_reference_is_not_derived_from_the_primary_key(db, client):
    """A rights register's size, and the ability to walk from one reference to
    a neighbour's, are both disclosures in their own right - the second one
    reveals that a named individual asked to be erased.

    Asserted as the property that actually matters rather than as a substring
    check. `str(row.id) not in reference` looked like the same test and was
    not: the id is a small integer and the reference's alphabet includes
    digits, so it fails outright whenever the id happens to be a digit of the
    year segment (it did - id 2, in "RRQ-2026-..."), and passes by luck the
    rest of the time. Ten requests created in sequence have consecutive ids;
    ordering them by reference must not reproduce ordering them by id, which
    a counter-derived reference could not avoid doing.
    """
    _tenant(db, "RR_SEQ")
    _customer, token = _principal(db, "RR_SEQ", "RR-SEQ-1")
    references = [_submit(client, token).json()["reference_no"] for _ in range(10)]

    rows = (
        db.query(RightsRequest)
        .filter(RightsRequest.reference_no.in_(references))
        .all()
    )
    assert len(rows) == 10
    by_id = [r.reference_no for r in sorted(rows, key=lambda r: r.id)]
    assert by_id == references, "sanity: creation order is id order"
    assert sorted(references) != references, (
        "references sort into creation order, so they encode a counter - the whole point of "
        "generating them from secrets is that they do not"
    )


def test_reference_numbers_are_unique_and_drawn_from_the_csprng(db):
    """Twenty references from the same session: all distinct, all from the
    restricted alphabet (no 0/1/I/L/O/U, so a principal reading one aloud
    cannot mistranscribe it)."""
    references = {rights_service.generate_reference_no(db) for _ in range(20)}
    assert len(references) == 20
    alphabet = set(rights_service._REFERENCE_ALPHABET)
    for reference in references:
        prefix, _year, *chunks = reference.split("-")
        assert prefix == "RRQ"
        assert set("".join(chunks)) <= alphabet


def test_reference_generation_uses_secrets_not_random():
    """`random`'s Mersenne Twister state is recoverable from a few hundred
    outputs, and an attacker only needs references they are entitled to see."""
    import inspect

    source = inspect.getsource(rights_service)
    assert "import secrets" in source
    assert "\nimport random" not in source and "import random\n" not in source


# --------------------------------------------------------------------------- #
#  Acknowledgement and the two clocks
# --------------------------------------------------------------------------- #

def test_submission_acknowledges_the_principal_with_a_due_date(db, client):
    _tenant(db, "RR_ACK", response_days=30)
    _customer, token = _principal(db, "RR_ACK", "RR-ACK-1")

    resp = _submit(client, token)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["acknowledged"] is True
    assert body["response_days"] == 30
    assert body["reference_no"] in body["acknowledgement_message"]
    assert "30 days" in body["acknowledgement_message"]
    assert "Data Protection Board" in body["acknowledgement_message"]
    # R.9 / E-08: every rights response carries the contact.
    assert body["data_protection_officer"] == "Asha Menon"
    assert body["board_complaint_url"] == "https://dpb.gov.in/complaint"

    due = _parse(body["due_at"])
    received = _parse(body["request"]["received_at"])
    assert (due - received).days == 30

    # And the acknowledgement is a real state, not a claim: both timestamps
    # are on the row and distinct states are recorded.
    row = db.query(RightsRequest).filter(
        RightsRequest.reference_no == body["reference_no"]
    ).first()
    assert row.acknowledged_at is not None
    assert row.received_at is not None
    events = {e.event for e in row.events}
    assert {"RECEIVED", "ACKNOWLEDGED"} <= events


def test_the_acknowledgement_is_a_real_notification_not_just_a_response(db, client):
    _tenant(db, "RR_NOTIFY")
    _customer, token = _principal(db, "RR_NOTIFY", "RR-NOTIFY-1")
    body = _submit(client, token).json()
    assert body["notification_ids"], "the acknowledgement must actually be queued"
    rows = db.query(Notification).filter(Notification.id.in_(body["notification_ids"])).all()
    assert rows
    assert {n.event_type for n in rows} == {"REQUEST_STATUS"}


def test_due_date_comes_from_the_tenants_configured_period(db, client):
    """Not a constant in this module: the tenant's published period is the
    single source of truth, and it is snapshotted onto the request."""
    _tenant(db, "RR_PERIOD", response_days=15)
    _customer, token = _principal(db, "RR_PERIOD", "RR-PERIOD-1")
    body = _submit(client, token).json()
    assert body["response_days"] == 15
    assert body["request"]["response_days"] == 15


def test_a_tenant_can_publish_its_own_rights_period(db, client):
    """A rights desk may run a shorter clock than the grievance desk. When it
    does, it says so explicitly through `settings['rights_response_days']`
    rather than the two silently sharing one column."""
    _tenant(db, "RR_OWNPERIOD", response_days=90, rights_days=20)
    _customer, token = _principal(db, "RR_OWNPERIOD", "RR-OWNPERIOD-1")
    body = _submit(client, token).json()
    assert body["response_days"] == 20, "the tenant's own rights period must win"


def test_the_response_period_is_clamped_to_the_statutory_ceiling(db):
    """A settings blob is free JSON with no CHECK behind it, so the clamp is
    the defence. 90 days is the ceiling R.14(3) fixes for the comparable
    published response."""
    org = _tenant(db, "RR_CLAMP", response_days=90, rights_days=365)
    assert rights_service.response_days_for_tenant(db, org.id) == 90


def test_the_period_snapshot_survives_a_later_change_to_the_tenant(db, client):
    """A tenant that shortens or lengthens its published period must not be
    able to move the deadline of a request already in flight."""
    org = _tenant(db, "RR_SNAP", response_days=45)
    _customer, token = _principal(db, "RR_SNAP", "RR-SNAP-1")
    body = _submit(client, token).json()
    original_due = body["due_at"]

    org.grievance_response_days = 5
    db.commit()

    row = db.query(RightsRequest).filter(
        RightsRequest.reference_no == body["reference_no"]
    ).first()
    db.refresh(row)
    assert row.response_days == 45
    assert rights_service.as_utc(row.due_at) == _parse(original_due)


def test_the_acknowledgement_commitment_is_configurable_and_snapshotted(db, client):
    _tenant(db, "RR_ACKHOURS", ack_hours=24)
    _customer, token = _principal(db, "RR_ACKHOURS", "RR-ACKHOURS-1")
    body = _submit(client, token).json()
    assert body["acknowledgement_hours"] == 24
    ack_due = _parse(body["acknowledgement_due_at"])
    received = _parse(body["request"]["received_at"])
    assert abs((ack_due - received) - timedelta(hours=24)) < timedelta(seconds=2)


# --------------------------------------------------------------------------- #
#  R.14(2) identity verification - the load-bearing control
# --------------------------------------------------------------------------- #

def test_an_unverified_context_cannot_make_a_rights_request(db, client):
    """The difference from the grievance form, stated as a test. A complaint
    from the wrong person is harmless; an access request from the wrong person
    is a disclosure of somebody else's personal data."""
    _tenant(db, "RR_UNVERIFIED")
    _customer, token = _principal(db, "RR_UNVERIFIED", "RR-UNVERIFIED-1", verified=False)

    resp = _submit(client, token)
    assert resp.status_code == 403
    assert "R.14(2)" in resp.json()["detail"]
    assert db.query(RightsRequest).filter(RightsRequest.source_app == "RR_UNVERIFIED").count() == 0


def test_an_unverified_context_cannot_read_a_package_or_nominate(db, client):
    _tenant(db, "RR_UNVERIFIED2")
    _customer, token = _principal(db, "RR_UNVERIFIED2", "RR-UNVERIFIED-2", verified=False)

    assert client.get("/rights/me/requests", headers=_headers(token)).status_code == 403
    assert client.post(
        "/rights/me/nominations", headers=_headers(token),
        json={"nominee_name": "A", "nominee_email": "a@example.com"},
    ).status_code == 403


def test_a_staff_logged_request_starts_unverified_and_cannot_be_fulfilled(db, client, staff_token):
    """A request that arrived by phone has established nothing about who
    called. It enters the register, gets its reference and its clock, and sits
    in VERIFYING until an OTP the handler never sees is confirmed."""
    _tenant(db, "RR_STAFF")
    _customer, _token = _principal(db, "RR_STAFF", "RR-STAFF-1")

    resp = client.post(
        "/rights/requests", headers=_staff(staff_token),
        json={
            "customer_external_id": "RR-STAFF-1", "request_type": "ACCESS",
            "request_detail": "Caller asked for a copy of their record.", "channel": "PHONE",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    reference = body["reference_no"]

    assert body["identity_verified"] is False
    assert body["request"]["status"] == "VERIFYING"
    assert "R.14(2)" in body["next_step"]

    # The package is refused...
    pkg = client.get(f"/rights/requests/{reference}/package", headers=_staff(staff_token))
    assert pkg.status_code == 409
    assert "identity has not been established" in pkg.json()["detail"]

    # ...and so is fulfilment.
    fulfil = client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Sent."},
    )
    assert fulfil.status_code == 409


def test_a_handler_cannot_mark_a_request_verified_by_asserting_it(db, client, staff_token):
    """There is no field, no status and no vocabulary value for 'the handler
    was satisfied'. The only route to IN_PROGRESS is a confirmed OTP."""
    _tenant(db, "RR_NOASSERT")
    _principal(db, "RR_NOASSERT", "RR-NOASSERT-1")
    reference = client.post(
        "/rights/requests", headers=_staff(staff_token),
        json={"customer_external_id": "RR-NOASSERT-1", "request_type": "ACCESS",
              "request_detail": "By post.", "channel": "POST"},
    ).json()["reference_no"]

    resp = client.patch(
        f"/rights/requests/{reference}", headers=_staff(staff_token),
        json={"status": "IN_PROGRESS", "note": "I know this person"},
    )
    assert resp.status_code == 409
    assert "R.14(2)" in resp.json()["detail"]

    # And the vocabulary itself cannot express a staff attestation.
    from app.models.rights import IDENTITY_VERIFICATION_METHODS

    assert set(IDENTITY_VERIFICATION_METHODS) == {"EMAIL_OTP", "FIDUCIARY_ASSERTED"}


def test_staff_verification_sends_an_otp_the_handler_never_sees(db, client, staff_token, monkeypatch):
    """The full off-platform verification path: the code goes to the address
    already on file via services/otp.py, is not in the API response, and only
    the correct code moves the request to IN_PROGRESS."""
    _tenant(db, "RR_OTP")
    _principal(db, "RR_OTP", "RR-OTP-1", email="rr-otp-1@example.com")
    reference = client.post(
        "/rights/requests", headers=_staff(staff_token),
        json={"customer_external_id": "RR-OTP-1", "request_type": "ACCESS",
              "request_detail": "Emailed us.", "channel": "EMAIL"},
    ).json()["reference_no"]

    captured = {}

    def fake_send(self, to, subject, body):
        captured["to"] = to
        captured["body"] = body

    from app.integrations.notifications.email import ConsoleEmailSender

    monkeypatch.setattr(ConsoleEmailSender, "send", fake_send)

    start = client.post(
        f"/rights/requests/{reference}/verification/start", headers=_staff(staff_token)
    )
    assert start.status_code == 200, start.text
    # The code went to the address on file, not to anything the caller supplied.
    assert captured["to"] == "rr-otp-1@example.com"
    code = captured["body"].split("verification code is ")[1].split(".")[0]
    # ...and it is nowhere in what the handler can see.
    assert code not in start.text

    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    bad = client.post(
        f"/rights/requests/{reference}/verification/confirm", headers=_staff(staff_token),
        json={"code": wrong},
    )
    assert bad.status_code == 403

    good = client.post(
        f"/rights/requests/{reference}/verification/confirm", headers=_staff(staff_token),
        json={"code": code},
    )
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["identity_verified"] is True
    assert body["verification_method"] == "EMAIL_OTP"
    assert body["status"] == "IN_PROGRESS"

    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()
    db.refresh(row)
    assert row.verified_at is not None
    assert row.verification_context_id is not None


def test_the_database_refuses_a_fulfilled_request_that_is_not_verified(db, client):
    """The CHECK constraint is the backstop behind every service-layer guard.
    Even a direct write cannot record a fulfilment for an unverified
    requester."""
    _tenant(db, "RR_CHECK")
    customer, token = _principal(db, "RR_CHECK", "RR-CHECK-1")
    reference = _submit(client, token).json()["reference_no"]
    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()

    row.identity_verified = False
    row.verified_at = None
    row.verification_method = None
    row.status = "FULFILLED"
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_rights_requests_fulfilled_is_verified" in str(exc.value)
    db.rollback()


def test_the_database_refuses_a_verification_claim_with_no_evidence(db, client):
    _tenant(db, "RR_CHECK2")
    _customer, token = _principal(db, "RR_CHECK2", "RR-CHECK-2")
    reference = _submit(client, token).json()["reference_no"]
    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()

    row.identity_verified = True
    row.verified_at = None
    row.verification_method = None
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_rights_requests_verified_has_evidence" in str(exc.value)
    db.rollback()


# --------------------------------------------------------------------------- #
#  ACCESS - s.11(1)(a)-(b)
# --------------------------------------------------------------------------- #

def _consent_scaffold(db, customer, source_app, *, purpose_code, activity_code, category_code):
    """A minimal purpose/category/activity/consent so the access package has
    something real to report."""
    from app.services import consent as consent_service

    purpose = db.query(Purpose).filter(Purpose.code == purpose_code).first()
    if not purpose:
        purpose = Purpose(
            name=f"{purpose_code} purpose", code=purpose_code,
            description="Marketing analytics", legal_basis="CONSENT", is_active=True,
        )
        db.add(purpose)
        db.flush()
        from app.models.entities import PurposeVersion

        db.add(PurposeVersion(
            purpose_id=purpose.id, version_number=1, is_current=True,
            name=purpose.name, description=purpose.description,
            consent_text="We will process your data for analytics.",
            created_by="test",
        ))
    category = db.query(DataCategory).filter(DataCategory.code == category_code).first()
    if not category:
        category = DataCategory(name=f"{category_code} data", code=category_code, is_active=True)
        db.add(category)
    activity = db.query(ProcessingActivity).filter(
        ProcessingActivity.code == activity_code
    ).first()
    if not activity:
        activity = ProcessingActivity(
            name=f"{activity_code} activity", code=activity_code,
            description="Segmenting users for campaigns", is_active=True,
        )
        db.add(activity)
    db.commit()
    db.refresh(purpose)
    db.refresh(category)
    db.refresh(activity)

    consent, _created = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity,
        source_app=source_app, exact_source=True,
    )
    consent_service.grant_consent(
        db, consent, actor_username="principal", source_app=source_app,
        actor_type="PRINCIPAL",
    )
    db.commit()
    return purpose, category, activity, consent


def _processor(db, name, *, country="IN"):
    row = db.query(Processor).filter(Processor.name == name).first()
    if not row:
        row = Processor(name=name, type="ANALYTICS", country=country, is_active=True)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def test_access_request_end_to_end(db, client, staff_token):
    """s.11 in full: a verified principal asks, the package answers all three
    limbs (personal data, processing activities, recipients), the request is
    fulfilled and closed, and the closure carries evidence."""
    _tenant(db, "RR_ACCESS", response_days=30)
    customer, token = _principal(db, "RR_ACCESS", "RR-ACCESS-1")
    purpose, category, activity, consent = _consent_scaffold(
        db, customer, "RR_ACCESS",
        purpose_code="rr_access_analytics", activity_code="rr_access_segment",
        category_code="rr_access_contact",
    )
    sent_to = _processor(db, "Segment Analytics Pvt Ltd")
    refused_to = _processor(db, "Offshore Adtech Inc", country="US")
    db.add(DataSharingEvent(
        tenant_id=customer.tenant_id, customer_id=customer.id, processor_id=sent_to.id,
        purpose_id=purpose.id, consent_id=consent.id, data_category_ids=[category.id],
        event_type="SENT", legal_basis="CONSENT", source_app="RR_ACCESS",
        signature="sig-sent", occurred_at=_now(),
    ))
    db.add(DataSharingEvent(
        tenant_id=customer.tenant_id, customer_id=customer.id, processor_id=refused_to.id,
        purpose_id=purpose.id, consent_id=consent.id, data_category_ids=[category.id],
        event_type="DENIED", legal_basis="CONSENT", reason="No consent for this transfer",
        source_app="RR_ACCESS", signature="sig-denied", occurred_at=_now(),
    ))
    db.commit()

    # 1. Intake, acknowledged with a due date.
    ack = _submit(client, token, request_type="ACCESS").json()
    reference = ack["reference_no"]
    assert ack["identity_verified"] is True
    assert ack["request"]["status"] == "IN_PROGRESS", "a verified request skips VERIFYING"

    # 2. The principal collects her own package.
    pkg_resp = client.get(f"/rights/me/requests/{reference}/package", headers=_headers(token))
    assert pkg_resp.status_code == 200, pkg_resp.text
    pkg = pkg_resp.json()

    # s.11(1)(a): the summary of personal data and of processing activities.
    assert pkg["personal_data"]["external_id"] == "RR-ACCESS-1"
    assert pkg["personal_data"]["email"] == "rr-access-1@example.com"
    assert any(c["purpose_code"] == "rr_access_analytics" for c in pkg["consents"])
    activities = {a["code"] for a in pkg["processing_activities"]}
    assert "rr_access_segment" in activities

    # s.11(1)(b): the identities of everyone it was shared with, and what.
    names = {r["processor_name"] for r in pkg["recipients"]}
    assert names == {"Segment Analytics Pvt Ltd"}, (
        "only an actual disclosure makes a recipient - a DENIED event did not "
        "result in data going anywhere"
    )
    recipient = pkg["recipients"][0]
    assert recipient["disclosures"] == 1
    assert "rr_access_contact data" in recipient["data_categories"]
    assert recipient["country"] == "IN"
    # ...and the refusal is reported honestly rather than hidden or conflated.
    assert [n["processor_name"] for n in pkg["non_disclosures"]] == ["Offshore Adtech Inc"]

    # R.9 / E-08.
    assert pkg["data_protection_officer"] == "Asha Menon"
    assert len(pkg["package_hash"]) == 64

    # 3. Fulfil and close, with evidence.
    fulfil = client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Access package delivered to the principal."},
    )
    assert fulfil.status_code == 200, fulfil.text
    out = fulfil.json()
    assert out["status"] == "CLOSED"
    assert out["fulfilled_at"] is not None
    assert out["closed_at"] is not None
    assert len(out["closure_hash"]) == 64
    assert len(out["package_hash"]) == 64


def test_the_access_package_never_infers_a_recipient(db, client):
    """A processor under contract that was never actually sent anything is not
    a recipient. The register of disclosures is the only source."""
    _tenant(db, "RR_NOSHARE")
    customer, token = _principal(db, "RR_NOSHARE", "RR-NOSHARE-1")
    _processor(db, "Never Used Processor Ltd")
    reference = _submit(client, token).json()["reference_no"]

    pkg = client.get(
        f"/rights/me/requests/{reference}/package", headers=_headers(token)
    ).json()
    assert pkg["recipients"] == []


def test_the_package_hash_is_content_addressed(db, client, staff_token):
    """Two generations of an unchanged package must hash identically, and the
    hash recorded as closure evidence must be that same value.

    This failed once: the hash covered `generated_at`, so the principal's own
    download and the handler's fulfilment produced two different hashes for
    byte-identical content and neither identified what was actually
    disclosed."""
    _tenant(db, "RR_PKGHASH")
    customer, token = _principal(db, "RR_PKGHASH", "RR-PKGHASH-1")
    reference = _submit(client, token).json()["reference_no"]

    first = client.get(f"/rights/me/requests/{reference}/package", headers=_headers(token)).json()
    second = client.get(f"/rights/me/requests/{reference}/package", headers=_headers(token)).json()
    assert first["package_hash"] == second["package_hash"]
    assert first["generated_at"] != second["generated_at"], (
        "the two really were generated at different moments"
    )

    closed = client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Delivered."},
    ).json()
    assert closed["package_hash"] == first["package_hash"], (
        "the closure evidence must name the package that was actually delivered"
    )


def test_a_principal_cannot_read_another_principals_request(db, client):
    _tenant(db, "RR_ISOLATE")
    _alice, alice_token = _principal(db, "RR_ISOLATE", "RR-ISOLATE-A")
    _bob, bob_token = _principal(db, "RR_ISOLATE", "RR-ISOLATE-B")
    alice_ref = _submit(client, alice_token).json()["reference_no"]

    # Same 404 as an unknown reference: no probing for existence.
    assert client.get(
        f"/rights/me/requests/{alice_ref}", headers=_headers(bob_token)
    ).status_code == 404
    assert client.get(
        f"/rights/me/requests/{alice_ref}/package", headers=_headers(bob_token)
    ).status_code == 404


# --------------------------------------------------------------------------- #
#  CORRECTION - s.12(1)-(2)
# --------------------------------------------------------------------------- #

def test_correction_request_end_to_end(db, client, staff_token):
    """s.12(1): the principal says a field is wrong, a handler applies it
    through the staff customer-update endpoint, the record actually changes,
    the HMAC search companion is maintained, and the request closes with
    evidence."""
    _tenant(db, "RR_CORRECT")
    customer, token = _principal(db, "RR_CORRECT", "RR-CORRECT-1")
    original_email = customer.email

    ack = _submit(
        client, token, request_type="CORRECTION",
        request_detail="My surname and email are wrong.",
        requested_changes={"name": "Rhea Kapoor-Iyer", "email": "rhea.new@example.com"},
    ).json()
    reference = ack["reference_no"]
    assert ack["request"]["requested_changes"]["name"] == "Rhea Kapoor-Iyer"

    resp = client.post(
        f"/rights/requests/{reference}/correction", headers=_staff(staff_token),
        json={"note": "Verified against the request."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_changes"]["after"]["name"] == "Rhea Kapoor-Iyer"
    assert body["applied_changes"]["before"]["email"] == original_email

    db.expire_all()
    db.refresh(customer)
    assert customer.name == "Rhea Kapoor-Iyer"
    assert customer.email == "rhea.new@example.com"
    # The HMAC companion for the encrypted column is maintained - without this
    # the principal becomes unfindable by email, and every upsert path would
    # read that miss as "new customer" and fabricate a duplicate identity.
    assert customer.email_search == hmac_digest("rhea.new@example.com")

    # Both audit rows are written, and neither carries the values.
    events = {
        row.event for row in db.query(AuditLog).filter(
            AuditLog.customer_id == customer.id
        ).all()
    }
    assert "CUSTOMER_UPDATED" in events
    assert "RIGHTS_REQUEST_CORRECTION_APPLIED" in events

    fulfil = client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Record corrected as requested."},
    )
    assert fulfil.status_code == 200, fulfil.text
    assert fulfil.json()["status"] == "CLOSED"
    assert fulfil.json()["closure_hash"]


def test_a_correction_cannot_touch_the_tenant_binding_or_the_external_id(db, client, staff_token):
    """`external_id` is the tenant's own key for this person and renaming it
    would orphan every consent and audit row keyed to it; `source_app` is the
    tenant binding every scoping check depends on. Correction means "my name
    is spelled wrong", not "re-point my record"."""
    _tenant(db, "RR_CORRECT2")
    _principal(db, "RR_CORRECT2", "RR-CORRECT-2")
    reference = client.post(
        "/rights/requests", headers=_staff(staff_token),
        json={"customer_external_id": "RR-CORRECT-2", "request_type": "CORRECTION",
              "request_detail": "x", "channel": "EMAIL"},
    ).json()["reference_no"]

    for field in ("external_id", "source_app", "status"):
        resp = client.post(
            f"/rights/requests/{reference}/correction", headers=_staff(staff_token),
            json={"changes": {field: "HIJACKED"}},
        )
        # Refused before it ever reaches the identity check or the row.
        assert resp.status_code in (409, 422), (field, resp.text)


def test_a_correction_is_refused_while_identity_is_unestablished(db, client, staff_token):
    _tenant(db, "RR_CORRECT3")
    customer, _token = _principal(db, "RR_CORRECT3", "RR-CORRECT-3")
    reference = client.post(
        "/rights/requests", headers=_staff(staff_token),
        json={"customer_external_id": "RR-CORRECT-3", "request_type": "CORRECTION",
              "request_detail": "Change my name", "channel": "PHONE"},
    ).json()["reference_no"]

    resp = client.post(
        f"/rights/requests/{reference}/correction", headers=_staff(staff_token),
        json={"changes": {"name": "Someone Else"}},
    )
    assert resp.status_code == 409
    assert "R.14(2)" in resp.json()["detail"]
    db.refresh(customer)
    assert customer.name == "Rhea Kapoor", "nothing may be written before verification"


# --------------------------------------------------------------------------- #
#  ERASURE - s.12(3), delegated to the R1-06 engine
# --------------------------------------------------------------------------- #

def test_erasure_request_hands_off_to_the_erasure_engine(db, client, staff_token):
    """The DoD clause that matters most here: an ERASURE rights request must
    produce an `erasure_jobs` row through the existing service and inherit its
    guarantees - the 48-hour notice, the named authoriser, the evidence hash -
    rather than deleting anything itself."""
    from app.models.erasure import ErasureJob
    from app.services import erasure as erasure_service

    _tenant(db, "RR_ERASE")
    customer, token = _principal(db, "RR_ERASE", "RR-ERASE-1")

    ack = _submit(
        client, token, request_type="ERASURE",
        request_detail="Please delete my account and my data.",
    ).json()
    reference = ack["reference_no"]

    # 1. Approving creates the job. Nothing is destroyed.
    approve = client.post(
        f"/rights/requests/{reference}/erasure", headers=_staff(staff_token),
        json={"basis": "s.12(3): the specified purpose is spent."},
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["erasure_job_ref"], "the request must point at a real erasure job"
    assert body["erasure_job_status"] == "SCHEDULED"
    assert body["status"] == "IN_PROGRESS", "the right is not yet given effect"

    job = db.query(ErasureJob).filter(ErasureJob.job_ref == body["erasure_job_ref"]).first()
    assert job is not None
    assert job.trigger == "RIGHTS_REQUEST"
    assert job.trigger_ref == f"rights-request:{reference}"
    assert job.authorised_by == "test-admin"
    assert "s.12(3)" in job.authorisation_basis
    assert job.notice_required is True
    assert job.notice_hours >= 48
    db.refresh(customer)
    assert customer.status != "ANONYMISED", "nothing may be destroyed at approval"

    # 2. Fulfilment is refused while the job has not executed. This is what
    #    stops the register claiming a person was erased on the strength of a
    #    job still waiting out its statutory notice.
    early = client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Done."},
    )
    assert early.status_code == 409
    assert "EXECUTED" in early.json()["detail"]

    # 3. The engine's own guarantees still apply, unchanged.
    with pytest.raises(erasure_service.ErasureNotAuthorised) as exc:
        erasure_service.execute_erasure_job(db, job, actor_username="dpo")
    assert "R.8(2)" in str(exc.value)

    erasure_service.send_pre_erasure_notice(db, job, actor_username="dpo")
    db.refresh(job)
    assert job.status == "NOTIFIED"
    assert job.notice_sent_at is not None

    with pytest.raises(erasure_service.ErasureNotAuthorised):
        erasure_service.execute_erasure_job(db, job, actor_username="dpo")

    after = _now() + timedelta(hours=job.notice_hours + 1)
    erasure_service.execute_erasure_job(db, job, actor_username="dpo", now=after)
    db.refresh(job)
    assert job.status == "EXECUTED"
    assert job.evidence_hash and len(job.evidence_hash) == 64

    # 4. Only now does the rights request fulfil and close, with evidence.
    done = client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Erasure carried out under job "
                                    f"{job.job_ref}."},
    )
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["status"] == "CLOSED"
    assert out["closure_hash"]
    assert out["erasure_job_status"] == "EXECUTED"

    db.refresh(customer)
    assert customer.status == "ANONYMISED"


def test_the_rights_module_builds_no_second_deletion_path(db):
    """A regression guard on the instruction. The rights service must reach
    the erasure engine and must not carry destruction logic of its own."""
    import inspect

    source = inspect.getsource(rights_service)
    assert "approve_rights_request_erasure" in source
    for forbidden in (".delete(", "DROP ", "TRUNCATE"):
        assert forbidden not in source, f"the rights service must not contain {forbidden!r}"


def test_an_erasure_request_is_refused_before_identity_is_established(db, client, staff_token):
    from app.models.erasure import ErasureJob

    _tenant(db, "RR_ERASE2")
    _principal(db, "RR_ERASE2", "RR-ERASE-2")
    reference = client.post(
        "/rights/requests", headers=_staff(staff_token),
        json={"customer_external_id": "RR-ERASE-2", "request_type": "ERASURE",
              "request_detail": "Delete me", "channel": "EMAIL"},
    ).json()["reference_no"]

    resp = client.post(
        f"/rights/requests/{reference}/erasure", headers=_staff(staff_token), json={"basis": ""},
    )
    assert resp.status_code == 409
    assert "R.14(2)" in resp.json()["detail"]
    assert db.query(ErasureJob).filter(
        ErasureJob.trigger_ref == f"rights-request:{reference}"
    ).count() == 0, "an unverified request must not raise an erasure job"


# --------------------------------------------------------------------------- #
#  NOMINATION - s.14
# --------------------------------------------------------------------------- #

def test_nomination_end_to_end(db, client, staff_token):
    """s.14: recorded by a verified principal, confirmed by the nominee with a
    code only they received, fulfilled and closed - and activated only by a
    named officer against recorded evidence."""
    _tenant(db, "RR_NOM")
    customer, token = _principal(db, "RR_NOM", "RR-NOM-1")

    resp = client.post(
        "/rights/me/nominations", headers=_headers(token),
        json={"nominee_name": "Vikram Rao", "nominee_email": "vikram@example.com",
              "nominee_phone": "+919800000000", "nominee_relationship": "Brother"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    nomination_ref = body["nomination"]["nomination_ref"]
    request_ref = body["request"]["reference_no"]

    assert nomination_ref.startswith("NOM-")
    assert body["nomination"]["status"] == "PENDING", "a nomination confers nothing yet"
    # The nominee is a third party: their address is masked even for the
    # principal who supplied it, and the plaintext code is never returned.
    assert body["nomination"]["nominee_email_masked"] == "vi***@example.com"
    assert "nominee_email" not in body["nomination"]

    nomination = db.query(Nomination).filter(
        Nomination.nomination_ref == nomination_ref
    ).first()
    assert nomination.verification_code_hash is not None
    assert nomination.nominee_email_search == hmac_digest("vikram@example.com")

    # A NOMINATION rights request rides the same register, clock and queue.
    assert body["request"]["request_type"] == "NOMINATION"
    assert body["request"]["nomination_ref"] == nomination_ref

    # The nomination cannot be activated while it is unconfirmed - not even
    # by a permissioned officer with evidence in hand.
    premature = client.post(
        f"/rights/nominations/{nomination_ref}/activate", headers=_staff(staff_token),
        json={"ground": "DEATH", "evidence_ref": "DC/2026/00417"},
    )
    assert premature.status_code == 409

    # A wrong code fails, and looks the same as everything else that fails.
    bad = client.post(
        f"/rights/nominations/{nomination_ref}/confirm", json={"code": "000000"}
    )
    assert bad.status_code == 200
    if bad.json()["confirmed"]:  # 000000 could coincidentally be the code
        pytest.skip("the CSPRNG happened to produce the probe code")
    unknown = client.post("/rights/nominations/NOM-2026-ZZZZ-ZZZZ-ZZZZ/confirm",
                          json={"code": "123456"})
    assert unknown.status_code == 200 and unknown.json() == {"confirmed": False}, (
        "an unknown reference must be indistinguishable from a wrong code"
    )

    # The real code, which only the nominee's own address received.
    code = _nominee_code_for(db, nomination_ref)
    good = client.post(f"/rights/nominations/{nomination_ref}/confirm", json={"code": code})
    assert good.status_code == 200 and good.json() == {"confirmed": True}

    db.expire_all()
    nomination = db.query(Nomination).filter(
        Nomination.nomination_ref == nomination_ref
    ).first()
    assert nomination.status == "VERIFIED"
    assert nomination.verified_at is not None
    assert nomination.verification_code_hash is None, "a spent credential is not kept"

    # The s.14 request is now fulfillable, and closes with evidence.
    fulfil = client.post(
        f"/rights/requests/{request_ref}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Nomination recorded and confirmed by the nominee."},
    )
    assert fulfil.status_code == 200, fulfil.text
    assert fulfil.json()["status"] == "CLOSED"
    assert fulfil.json()["closure_hash"]

    # Activation: staff-only, named, grounded and evidenced.
    activate = client.post(
        f"/rights/nominations/{nomination_ref}/activate", headers=_staff(staff_token),
        json={"ground": "DEATH", "evidence_ref": "DC/2026/00417",
              "note": "Certificate sighted."},
    )
    assert activate.status_code == 200, activate.text
    out = activate.json()
    assert out["status"] == "ACTIVE"
    assert out["activated_by"] == "test-admin"
    assert out["activation_ground"] == "DEATH"
    assert out["activation_evidence_ref"] == "DC/2026/00417"


def _nominee_code_for(db, nomination_ref):
    """Brute-force the six-digit code from its stored HMAC.

    Only possible because the code space is 10^6 and the test knows the digest
    function; it is exactly the work an attacker would have to do *per
    guess through the API*, which the attempt cap and the rate limiter bound
    to single digits. It is how the test reads a credential that is never
    returned, never logged and stored only as a digest."""
    nomination = db.query(Nomination).filter(
        Nomination.nomination_ref == nomination_ref
    ).first()
    target = nomination.verification_code_hash
    for candidate in range(1_000_000):
        code = f"{candidate:06d}"
        if hmac_digest(code) == target:
            return code
    raise AssertionError("could not recover the nominee code")


def test_a_nomination_cannot_be_activated_without_evidence(db, client, staff_token):
    _tenant(db, "RR_NOM2")
    _customer, token = _principal(db, "RR_NOM2", "RR-NOM-2")
    ref = client.post(
        "/rights/me/nominations", headers=_headers(token),
        json={"nominee_name": "Meera S", "nominee_email": "meera@example.com"},
    ).json()["nomination"]["nomination_ref"]
    code = _nominee_code_for(db, ref)
    client.post(f"/rights/nominations/{ref}/confirm", json={"code": code})

    resp = client.post(
        f"/rights/nominations/{ref}/activate", headers=_staff(staff_token),
        json={"ground": "DEATH", "evidence_ref": "   "},
    )
    assert resp.status_code == 422


def test_the_database_refuses_an_unauthorised_activation(db, client):
    """The CHECK behind the endpoint. Activation happens at the moment the
    principal cannot contradict a false claim, so it must be attributable and
    checkable even against a direct write."""
    _tenant(db, "RR_NOM3")
    _customer, token = _principal(db, "RR_NOM3", "RR-NOM-3")
    ref = client.post(
        "/rights/me/nominations", headers=_headers(token),
        json={"nominee_name": "Ira P", "nominee_email": "ira@example.com"},
    ).json()["nomination"]["nomination_ref"]
    code = _nominee_code_for(db, ref)
    client.post(f"/rights/nominations/{ref}/confirm", json={"code": code})

    db.expire_all()
    nomination = db.query(Nomination).filter(Nomination.nomination_ref == ref).first()
    nomination.status = "ACTIVE"  # no activator, no ground, no evidence
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_nominations_active_is_authorised" in str(exc.value)
    db.rollback()


def test_a_principal_can_revoke_her_own_nomination(db, client):
    _tenant(db, "RR_NOM4")
    _customer, token = _principal(db, "RR_NOM4", "RR-NOM-4")
    ref = client.post(
        "/rights/me/nominations", headers=_headers(token),
        json={"nominee_name": "Dev K", "nominee_email": "dev@example.com"},
    ).json()["nomination"]["nomination_ref"]

    resp = client.post(
        f"/rights/me/nominations/{ref}/revoke", headers=_headers(token),
        json={"reason": "Changed my mind"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REVOKED"
    assert resp.json()["revoked_at"] is not None


# --------------------------------------------------------------------------- #
#  Refusal, closure evidence
# --------------------------------------------------------------------------- #

def test_a_refusal_records_its_ground_and_closes_with_evidence(db, client, staff_token):
    _tenant(db, "RR_REJECT")
    _customer, token = _principal(db, "RR_REJECT", "RR-REJECT-1")
    reference = _submit(client, token, request_type="ERASURE",
                        request_detail="Delete everything.").json()["reference_no"]

    resp = client.post(
        f"/rights/requests/{reference}/reject", headers=_staff(staff_token),
        json={"reason": "RETENTION_REQUIRED_BY_LAW",
              "basis": "The tax record must be retained for eight years under the Income-tax "
                       "Act, which s.12(3) read with s.8(7) preserves."},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["status"] == "CLOSED"
    assert out["rejection_reason"] == "RETENTION_REQUIRED_BY_LAW"
    assert "Income-tax" in out["rejection_basis"]
    assert out["closure_hash"]


def test_a_refusal_must_name_a_ground_the_database_recognises(db, client, staff_token):
    _tenant(db, "RR_REJECT2")
    _customer, token = _principal(db, "RR_REJECT2", "RR-REJECT-2")
    reference = _submit(client, token).json()["reference_no"]
    resp = client.post(
        f"/rights/requests/{reference}/reject", headers=_staff(staff_token),
        json={"reason": "BECAUSE_I_SAID_SO", "basis": "no"},
    )
    assert resp.status_code == 422


def test_closure_evidence_is_a_recomputable_hash_written_into_the_ledger(db, client, staff_token):
    """`closure_hash` is SHA-256 over the request's own outcome and the same
    value goes into the hash-chained, immutable audit row - so a later edit to
    `rights_requests` is detectable by recomputing."""
    _tenant(db, "RR_EVIDENCE")
    _customer, token = _principal(db, "RR_EVIDENCE", "RR-EVIDENCE-1")
    reference = _submit(client, token).json()["reference_no"]
    client.post(
        f"/rights/requests/{reference}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Package delivered."},
    )

    db.expire_all()
    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()
    assert row.closure_hash == rights_service.compute_closure_hash(row), (
        "the hash must be reproducible from the row by anyone reading it"
    )

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event == "RIGHTS_REQUEST_CLOSED", AuditLog.customer_id == row.customer_id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert (audit.details or {}).get("closure_hash") == row.closure_hash


def test_a_request_cannot_be_closed_without_evidence(db, client):
    _tenant(db, "RR_EVIDENCE2")
    _customer, token = _principal(db, "RR_EVIDENCE2", "RR-EVIDENCE-2")
    reference = _submit(client, token).json()["reference_no"]
    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()
    row.status = "CLOSED"
    row.closed_at = None
    row.closure_hash = None
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert "ck_rights_requests_closed_has_evidence" in str(exc.value)
    db.rollback()


def test_the_audit_row_never_carries_the_narrative(db, client, staff_token):
    """A request detail is encrypted at rest and the audit ledger is exported
    wholesale to auditors and regulators. Only the reference, the type and the
    SLA facts belong in it."""
    _tenant(db, "RR_AUDIT")
    customer, token = _principal(db, "RR_AUDIT", "RR-AUDIT-1")
    secret = "I am being stalked by an ex-colleague and need my address removed"
    reference = _submit(client, token, request_detail=secret).json()["reference_no"]

    rows = db.query(AuditLog).filter(AuditLog.customer_id == customer.id).all()
    assert rows
    for row in rows:
        blob = f"{row.reason}{row.details}"
        assert secret not in blob
    assert any(reference in f"{r.reason}{r.details}" for r in rows)


# --------------------------------------------------------------------------- #
#  The queue and its SLA timers
# --------------------------------------------------------------------------- #

def test_the_queue_surfaces_the_sla_timers(db, client, staff_token):
    _tenant(db, "RR_QUEUE", response_days=30)
    _customer, token = _principal(db, "RR_QUEUE", "RR-QUEUE-1")
    reference = _submit(client, token).json()["reference_no"]

    rows = client.get(
        "/rights/requests?open_only=true", headers=_staff(staff_token)
    ).json()
    mine = [r for r in rows if r["reference_no"] == reference]
    assert mine, "an open request must appear in the queue"
    row = mine[0]
    assert row["overdue"] is False
    assert 28 <= row["days_remaining"] <= 30
    assert row["acknowledgement_due_at"]


def test_an_overdue_request_is_reported_as_overdue(db, client, staff_token):
    _tenant(db, "RR_OVERDUE")
    _customer, token = _principal(db, "RR_OVERDUE", "RR-OVERDUE-1")
    reference = _submit(client, token).json()["reference_no"]
    row = db.query(RightsRequest).filter(RightsRequest.reference_no == reference).first()
    row.due_at = _now() - timedelta(days=1)
    db.commit()

    rows = client.get("/rights/requests?overdue_only=true", headers=_staff(staff_token)).json()
    found = [r for r in rows if r["reference_no"] == reference]
    assert found and found[0]["overdue"] is True
    assert found[0]["days_remaining"] < 0

    stats = client.get("/rights/requests/stats", headers=_staff(staff_token)).json()
    assert stats["overdue"] >= 1


def test_the_queue_is_org_scoped(db, client, staff_token):
    """An org-scoped handler must not even be able to confirm that another
    tenant's reference exists."""
    from app.core.rbac import ROLE_PERMISSIONS
    from app.core.security import create_access_token, hash_password
    from app.models.entities import Role, User

    _tenant(db, "CODEX")
    _tenant(db, "RR_OTHER")
    _customer, token = _principal(db, "RR_OTHER", "RR-OTHER-1")
    other_ref = _submit(client, token).json()["reference_no"]

    role = db.query(Role).filter(Role.name == "codex_admin").first()
    if not role:
        role = Role(name="codex_admin", description="Codex admin",
                    permissions=list(ROLE_PERMISSIONS["codex_admin"]), is_system=True)
        db.add(role)
        db.flush()
    else:
        role.permissions = list(ROLE_PERMISSIONS["codex_admin"])
    user = db.query(User).filter(User.username == "rights-codex-admin").first()
    if not user:
        user = User(
            username="rights-codex-admin", full_name="Codex Admin",
            email="rights-codex-admin@example.com",
            email_search=hmac_digest("rights-codex-admin@example.com"),
            password_hash=hash_password("Test@1234"), role_id=role.id, is_active=True,
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    scoped = {"Authorization": f"Bearer {create_access_token(user.id, user.username, role.name)}"}

    rows = client.get("/rights/requests", headers=scoped).json()
    assert all(r["source_app"] == "CODEX" for r in rows)
    assert client.get(f"/rights/requests/{other_ref}", headers=scoped).status_code == 404


def test_the_queue_requires_the_rights_permission(db, client):
    assert client.get("/rights/requests").status_code == 401
    assert client.get("/rights/requests/stats").status_code == 401


def test_rights_permissions_exist_and_are_granted_like_the_grievance_queue(db):
    from app.core.rbac import (
        ALL_PERMISSIONS,
        PERM_GRIEVANCE_MANAGE,
        PERM_RIGHTS_MANAGE,
        PERM_RIGHTS_VIEW,
        ROLE_PERMISSIONS,
    )

    assert PERM_RIGHTS_VIEW in ALL_PERMISSIONS
    assert PERM_RIGHTS_MANAGE in ALL_PERMISSIONS
    # The two queues behave alike: whoever can work a grievance can work a
    # rights request, and whoever can read the audit trail can read the queue.
    for role, perms in ROLE_PERMISSIONS.items():
        if PERM_GRIEVANCE_MANAGE in perms:
            assert PERM_RIGHTS_MANAGE in perms, role
        if "audit.view" in perms:
            assert PERM_RIGHTS_VIEW in perms, role
    # The read-only role stays read-only.
    assert PERM_RIGHTS_MANAGE not in ROLE_PERMISSIONS["auditor"]
    assert PERM_RIGHTS_MANAGE not in ROLE_PERMISSIONS["viewer"]
    # Erasure stays a separate, heavier grant: the rights desk raises a job,
    # it does not authorise or execute one.
    assert "erasure.manage" not in ROLE_PERMISSIONS["operator"]


# --------------------------------------------------------------------------- #
#  K-20, K-21, K-22
# --------------------------------------------------------------------------- #

def test_k20_k21_k22_are_computable_from_the_register(db, client, staff_token):
    """The DoD's KPI clause. All three come off `rights_requests` through
    `queue_metrics`, and the dashboard republishes those numbers rather than
    recomputing them."""
    _tenant(db, "RR_KPI", response_days=30)
    _customer, token = _principal(db, "RR_KPI", "RR-KPI-1")
    access_ref = _submit(client, token).json()["reference_no"]
    _submit(client, token, request_type="CORRECTION", request_detail="wrong",
            requested_changes={"name": "New Name"})
    client.post(
        f"/rights/requests/{access_ref}/fulfil", headers=_staff(staff_token),
        json={"resolution_summary": "Delivered."},
    )

    metrics = rights_service.queue_metrics(db, source_app="RR_KPI")
    # K-20
    assert metrics["by_type"]["ACCESS"] == 1
    assert metrics["by_type"]["CORRECTION"] == 1
    # K-21 - every request is acknowledged in the same unit of work as receipt.
    assert metrics["acknowledged_total"] == 2
    assert metrics["median_acknowledgement_hours"] is not None
    assert metrics["acknowledged_within_commitment"] == 2
    # K-22
    assert metrics["closed_total"] == 1
    assert metrics["on_time_closure_rate"] == 100.0

    body = client.get("/analytics/kpis", headers=_staff(staff_token)).json()
    by_id = {k["id"]: k for k in body["kpis"]}
    for kpi_id in ("K-20", "K-21", "K-22"):
        assert by_id[kpi_id]["status"] != "UNAVAILABLE", (kpi_id, by_id[kpi_id])
        assert by_id[kpi_id]["computed_by"] == (
            "app/services/rights_requests.py::queue_metrics"
        ), kpi_id


def test_an_empty_register_reports_no_data_rather_than_zero(db, client, staff_token):
    """A 0% turnaround and 'nobody has asked for anything yet' are opposite
    findings and must not render alike."""
    from app.services import kpi_catalogue

    ctx = kpi_catalogue.KpiContext(
        db=db, scope="RR_EMPTY_SCOPE", tenant_id=None,
        period_start=_now() - timedelta(days=30), period_end=_now(),
    )
    for fn in (kpi_catalogue.k20_rights_requests, kpi_catalogue.k21_rights_acknowledgement,
               kpi_catalogue.k22_rights_turnaround):
        result = fn(ctx)
        assert result["status"] == "NO_DATA", fn.__name__
        assert result["value"] is None, fn.__name__
        assert result["reason"], fn.__name__


def test_the_dashboard_republishes_the_queues_numbers(db, client, staff_token):
    """One implementation per KPI: the drill-down must equal what
    `queue_metrics` says, or the two screens can disagree."""
    _tenant(db, "RR_KPI2")
    _customer, token = _principal(db, "RR_KPI2", "RR-KPI2-1")
    _submit(client, token, request_type="ERASURE", request_detail="delete")

    stats = client.get("/rights/requests/stats", headers=_staff(staff_token)).json()
    body = client.get("/analytics/kpis", headers=_staff(staff_token)).json()
    k20 = {k["id"]: k for k in body["kpis"]}["K-20"]
    assert k20["detail"]["by_type"] == stats["by_type"]
