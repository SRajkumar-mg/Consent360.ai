"""R3-08 (I-01..I-04, H-12): personal data breach register, Rule 7 filings and
the three statutory clocks.

Definition of done: "A test breach produces principal notices, the Board
report and CERT-In timeline with all clocks tracked (K-39 to K-41)." Every
half of that is exercised here against real rows, including the negative
cases that make the positive ones mean something:

* a report missing a mandated section is REFUSED, not silently filed;
* an extension that was merely *requested* does not move the 72-hour deadline;
* a tampered payload fails its content hash;
* a breach cannot be closed while a Rule 7 obligation is outstanding;
* the clocks measure from `aware_at`, and a breach whose detection long
  precedes awareness proves they are not measuring from `detected_at`.

As with tests/test_retention_and_evidence_pack.py and
tests/test_processor_propagation.py, the HTTP tests mount the breaches router
on a local app because `app/main.py` does not register it in this branch
(that registration is the coordinating agent's change).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.encryption import hmac_digest
from app.models.breach import Breach, BreachNotification
from app.models.entities import AuditLog, Customer, Notification, Organization
from app.services import breach as bs
from app.services.notifications import dispatch_pending
from app.services.tenancy import resolve_tenant_id

SOURCE = "BREACHTEST"
OTHER_SOURCE = "BREACHTEST_OTHER"


def utcnow():
    return datetime.now(timezone.utc)


@pytest.fixture()
def breach_client(db):
    from app.api.routes import breaches as breach_routes
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(breach_routes.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def tenant(db):
    """The breach tenant, with a DPO contact (R.7(1)(e)) and regulator
    addresses so the filings have somewhere to go."""
    tenant_id = resolve_tenant_id(db, SOURCE)
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    org.dpo_name = "Breach Test DPO"
    org.dpo_email = "dpo@breachtest.example.in"
    org.dpo_phone = "+91-80-0000-0000"
    org.settings = {
        "board_notification_email": "board@dpb.example.in",
        "cert_in_notification_email": "incident@cert-in.example.in",
    }
    db.commit()
    return org


def _customer(db, external_id: str, source_app: str = SOURCE) -> Customer:
    existing = (
        db.query(Customer)
        .filter(Customer.external_id_search == hmac_digest(external_id))
        .first()
    )
    if existing:
        return existing
    email = f"{external_id.lower()}@example.in"
    row = Customer(
        tenant_id=resolve_tenant_id(db, source_app),
        external_id=external_id,
        external_id_search=hmac_digest(external_id),
        name=f"Principal {external_id}",
        email=email,
        email_search=hmac_digest(email),
        phone="+91-98000-00000",
        source_app=source_app,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _register(db, *, source_app: str = SOURCE, complete: bool = True, **overrides) -> Breach:
    """A fully-populated breach unless `complete=False`, which leaves the two
    sections that only become knowable during the investigation empty."""
    now = utcnow()
    kwargs = dict(
        title="Unauthorised access to the profile store",
        source_app=source_app,
        occurred_at=now - timedelta(hours=30),
        detected_at=now - timedelta(hours=9),
        severity="HIGH",
        nature="Unauthorised access to a database replica via a compromised contractor credential",
        extent="Name, email and phone of the affected principals",
        location="Application database replica, ap-south-1",
        likely_impact="Elevated phishing risk; no financial data exposed",
        likely_consequences="Your contact details may have been read and copied",
        mitigation_measures="Credential revoked, replica isolated, MFA forced on privileged accounts",
        safety_measures="Do not act on messages asking you to confirm account details",
        cause="Contractor endpoint compromised by an info-stealer; standing grant not revoked",
        actor_username="test-dpo",
    )
    kwargs.update(overrides)
    breach = bs.register_breach(db, **kwargs)
    if complete:
        bs.update_breach(db, breach, {
            "findings_on_actor": "Caused by a third-party contractor whose endpoint was compromised",
            "remedial_measures": "Just-in-time access, automatic revocation, hardware MFA on the bastion",
        }, actor_username="test-dpo")
    return breach


def _prepare(db, *, principals=("BR-P1", "BR-P2"), aware_offset_hours=5, complete=True) -> Breach:
    breach = _register(db, complete=complete)
    bs.mark_aware(
        db, breach, aware_at=utcnow() - timedelta(hours=aware_offset_hours),
        actor_username="test-dpo",
    )
    for ext in principals:
        _customer(db, ext)
    bs.add_affected_principals(
        db, breach, external_ids=list(principals), data_involved="name, email, phone",
        actor_username="test-dpo",
    )
    bs.finalise_scope(db, breach, actor_username="test-dpo")
    return breach


# ---------------------------------------------------------------------------
# Clocks start at awareness, not detection
# ---------------------------------------------------------------------------

def test_no_clock_runs_before_awareness_is_recorded(db, tenant):
    breach = _register(db)
    assert breach.aware_at is None
    for clock in bs.breach_clocks(db, breach):
        assert clock["status"] == "NOT_STARTED", clock
        assert clock["started_at"] is None


def test_every_clock_measures_from_aware_at_not_detected_at(db, tenant):
    """The distinction is legally load-bearing: R.7 says "on becoming aware"
    and the CERT-In direction says "within 6 hours of noticing". A breach
    detected 9 hours ago but only understood to be a personal data breach 1
    hour ago still has 5 hours of its CERT-In window left."""
    now = utcnow()
    breach = _register(db, detected_at=now - timedelta(hours=9))
    bs.mark_aware(db, breach, aware_at=now - timedelta(hours=1), actor_username="test-dpo")

    aware = breach.aware_at.astimezone(timezone.utc)
    detected = breach.detected_at.astimezone(timezone.utc)
    assert aware > detected

    clocks = {c["clock"]: c for c in bs.breach_clocks(db, breach)}
    cert = clocks["CERT_IN_6H"]
    assert cert["started_at"] == aware.isoformat()
    assert cert["deadline_at"] == (aware + timedelta(hours=6)).isoformat()
    # The give-away: measured from detection this would already be overdue.
    assert detected + timedelta(hours=6) < now
    assert cert["status"] == "OPEN"
    assert 4.5 < cert["remaining_hours"] < 5.5

    board = clocks["BOARD_DETAILED_72H"]
    assert board["deadline_at"] == (aware + timedelta(hours=72)).isoformat()

    # And the triage lag is reported rather than absorbed.
    metrics = bs.breach_metrics(db, source_app=SOURCE)
    row = next(r for r in metrics["per_breach"] if r["breach_ref"] == breach.breach_ref)
    assert 7.5 < row["detect_to_aware_hours"] < 8.5


def test_awareness_cannot_precede_detection_or_be_restated(db, tenant):
    now = utcnow()
    breach = _register(db, detected_at=now - timedelta(hours=2))
    with pytest.raises(bs.BreachError):
        bs.mark_aware(db, breach, aware_at=now - timedelta(hours=5), actor_username="t")
    bs.mark_aware(db, breach, aware_at=now - timedelta(hours=1), actor_username="t")
    with pytest.raises(bs.BreachError):
        bs.mark_aware(db, breach, aware_at=now, actor_username="t")


def test_registering_with_aware_before_detected_is_refused(db, tenant):
    now = utcnow()
    with pytest.raises(bs.BreachError):
        _register(db, detected_at=now - timedelta(hours=1), aware_at=now - timedelta(hours=4))


# ---------------------------------------------------------------------------
# R.7(1): principal notices
# ---------------------------------------------------------------------------

def test_principal_notice_carries_all_five_mandated_contents(db, tenant):
    breach = _prepare(db, principals=("BR-N1", "BR-N2"))
    result = bs.notify_principals(db, breach, actor_username="test-dpo")
    assert result["generated"] == 2

    rows = (
        db.query(BreachNotification)
        .filter(BreachNotification.breach_id == breach.id,
                BreachNotification.recipient_type == "PRINCIPAL")
        .all()
    )
    assert len(rows) == 2
    for row in rows:
        payload = row.payload
        assert payload["provision"] == "DPDP Rules 2025, Rule 7(1)(a)-(e)"
        # (a)-(e), every one of them non-empty.
        assert not bs.missing_clauses(payload, bs.PRINCIPAL_NOTICE_CLAUSES)
        assert payload["contact"].startswith("Breach Test DPO")
        assert row.deadline_at is None  # "without delay" gets no invented number
        assert row.target_at is not None
        assert "without delay" in row.deadline_basis
        assert row.content_hash and len(row.content_hash) == 64
        # The rendered notice reproduces all five.
        for marker in ("1. What happened", "2. Likely consequences", "3. What we have done",
                       "4. Steps you can take", "5. Who to contact"):
            assert marker in row.content


def test_principal_notices_go_through_the_shared_notification_service(db, tenant):
    breach = _prepare(db, principals=("BR-S1",))
    bs.notify_principals(db, breach, actor_username="test-dpo")
    filing = (
        db.query(BreachNotification)
        .filter(BreachNotification.breach_id == breach.id,
                BreachNotification.recipient_type == "PRINCIPAL")
        .one()
    )
    assert filing.notification_id is not None
    assert filing.status == "QUEUED"
    notification = db.get(Notification, filing.notification_id)
    assert notification.event_type == "BREACH_NOTICE"
    assert notification.status == "PENDING"

    dispatch_pending(db)
    bs.sync_delivery(db, breach)
    db.refresh(filing)
    assert filing.status == "DELIVERED"
    assert filing.sent_at is not None and filing.delivered_at is not None


def test_principal_notice_missing_a_mandated_content_is_refused(db, tenant):
    breach = _prepare(db, principals=("BR-M1",))
    # Strip R.7(1)(d) - the safety measures the principal may take.
    breach.safety_measures = ""
    db.commit()
    with pytest.raises(bs.BreachError) as exc:
        bs.notify_principals(db, breach, actor_username="test-dpo")
    assert "R.7(1)(d)" in str(exc.value)


def test_re_notifying_does_not_double_notify(db, tenant):
    breach = _prepare(db, principals=("BR-D1", "BR-D2"))
    first = bs.notify_principals(db, breach, actor_username="test-dpo")
    second = bs.notify_principals(db, breach, actor_username="test-dpo")
    assert first["generated"] == 2
    assert second["generated"] == 0
    assert second["already_issued"] == 2


# ---------------------------------------------------------------------------
# R.7(2): the Board
# ---------------------------------------------------------------------------

def test_board_initial_carries_the_five_r72a_items(db, tenant):
    breach = _prepare(db, principals=("BR-B1",))
    filing = bs.file_board_initial(db, breach, actor_username="test-dpo")
    assert filing.payload["provision"] == "DPDP Rules 2025, Rule 7(2)(a)"
    assert not bs.missing_clauses(filing.payload, bs.BOARD_INITIAL_CLAUSES)
    # location_of_occurrence is required for the Board and absent from the
    # principal notice - the two content lists are genuinely different.
    assert filing.payload["location_of_occurrence"]
    assert "location_of_occurrence" not in bs.principal_notice_payload(
        db, breach, _customer(db, "BR-B1")
    )
    assert filing.deadline_at is None
    assert filing.status == "QUEUED"


def test_board_detailed_report_refused_until_all_six_sections_present(db, tenant):
    breach = _prepare(db, principals=("BR-B2",), complete=False)
    with pytest.raises(bs.BreachError) as exc:
        bs.file_board_detailed(db, breach, actor_username="test-dpo")
    message = str(exc.value)
    assert "six mandated sections" in message
    assert "R.7(2)(b)(iv)" in message  # findings on the person who caused it
    assert "R.7(2)(b)(v)" in message   # remedial measures


def test_board_detailed_report_has_all_six_sections_and_a_generated_section_vi(db, tenant):
    breach = _prepare(db, principals=("BR-B3", "BR-B4"))
    bs.notify_principals(db, breach, actor_username="test-dpo")
    dispatch_pending(db)
    bs.sync_delivery(db, breach)

    filing = bs.file_board_detailed(db, breach, actor_username="test-dpo")
    payload = filing.payload
    assert payload["provision"] == "DPDP Rules 2025, Rule 7(2)(b)(i)-(vi)"
    for section in bs.BOARD_DETAILED_SECTIONS:
        assert section in payload
    assert not bs.missing_clauses(payload, bs.BOARD_DETAILED_SECTIONS)

    # (vi) is computed from the notification rows, never typed in.
    report = payload["principal_intimations_report"]
    assert report["affected_principals"] == 2
    assert report["notices_sent"] == 2
    assert report["notices_delivered"] == 2
    assert len(report["notice_content_hashes"]) == 2
    assert report["hours_from_awareness_to_first_notice"] is not None

    # The rendered report numbers the six sections in the Rules' order.
    for index in range(1, 7):
        assert f"({index}) R.7(2)(b)(" in filing.content


def test_board_detailed_deadline_is_72h_from_awareness(db, tenant):
    breach = _prepare(db, principals=("BR-B5",))
    aware = breach.aware_at.astimezone(timezone.utc)
    assert bs.statutory_board_deadline(breach) == aware + timedelta(hours=72)
    assert bs.effective_board_deadline(db, breach) == aware + timedelta(hours=72)


# ---------------------------------------------------------------------------
# CERT-In
# ---------------------------------------------------------------------------

def test_cert_in_six_hour_clock_and_report(db, tenant):
    breach = _prepare(db, principals=("BR-C1",), aware_offset_hours=2)
    aware = breach.aware_at.astimezone(timezone.utc)
    assert bs.cert_in_deadline(breach) == aware + timedelta(hours=6)

    filing = bs.file_cert_in(db, breach, actor_username="test-dpo")
    assert "20(3)/2022-CERT-In" in filing.payload["provision"]
    assert filing.payload["noticed_at"] == aware.isoformat()
    assert filing.payload["report_due_by"] == (aware + timedelta(hours=6)).isoformat()
    assert filing.deadline_at.astimezone(timezone.utc) == aware + timedelta(hours=6)

    dispatch_pending(db)
    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "CERT_IN_6H")
    assert clock["status"] == "MET"
    assert clock["elapsed_hours"] < 6


def test_cert_in_clock_reports_overdue_rather_than_hiding_a_missed_window(db, tenant):
    breach = _prepare(db, principals=("BR-C2",), aware_offset_hours=8)
    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "CERT_IN_6H")
    assert clock["status"] == "OVERDUE"
    assert clock["elapsed_hours"] > 6

    bs.file_cert_in(db, breach, actor_username="test-dpo")
    dispatch_pending(db)
    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "CERT_IN_6H")
    assert clock["status"] == "MET_LATE"

    metrics = bs.breach_metrics(db, source_app=SOURCE)
    row = next(r for r in metrics["per_breach"] if r["breach_ref"] == breach.breach_ref)
    assert row["cert_in_within_6h"] is False


def test_cert_in_not_reportable_is_recorded_not_hidden(db, tenant):
    breach = _prepare(db, principals=("BR-C3",))
    bs.update_breach(db, breach, {
        "cert_in_reportable": False,
        "cert_in_not_reportable_reason": "Availability-only incident outside Annexure I",
    }, actor_username="test-dpo")
    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "CERT_IN_6H")
    assert clock["status"] == "NOT_APPLICABLE"
    assert clock["not_applicable_reason"]
    with pytest.raises(bs.BreachError):
        bs.file_cert_in(db, breach, actor_username="test-dpo")


# ---------------------------------------------------------------------------
# Extension requests (R.7(2)(b) proviso)
# ---------------------------------------------------------------------------

def test_requesting_an_extension_does_not_move_the_deadline_but_granting_does(db, tenant):
    breach = _prepare(db, principals=("BR-E1",))
    aware = breach.aware_at.astimezone(timezone.utc)
    statutory = aware + timedelta(hours=72)

    request = bs.request_extension(
        db, breach, requested_until=aware + timedelta(hours=120),
        reason="Forensic imaging incomplete", written_request_ref="LETTER/1",
        actor_username="test-dpo",
    )
    assert request.status == "REQUESTED"
    # Asking is not being granted.
    assert bs.effective_board_deadline(db, breach) == statutory

    bs.decide_extension(
        db, breach, request, status="GRANTED",
        granted_until=aware + timedelta(hours=120),
        board_reference="DPB/EXT/1", actor_username="test-dpo",
    )
    assert bs.effective_board_deadline(db, breach) == aware + timedelta(hours=120)
    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "BOARD_DETAILED_72H")
    assert clock["extension_granted"] is True
    assert clock["statutory_deadline_at"] == statutory.isoformat()
    assert clock["deadline_at"] == (aware + timedelta(hours=120)).isoformat()


def test_a_refused_extension_leaves_the_statutory_deadline_alone(db, tenant):
    breach = _prepare(db, principals=("BR-E2",))
    aware = breach.aware_at.astimezone(timezone.utc)
    request = bs.request_extension(
        db, breach, requested_until=aware + timedelta(hours=120), reason="r",
        actor_username="test-dpo",
    )
    bs.decide_extension(db, breach, request, status="REFUSED", actor_username="test-dpo")
    assert bs.effective_board_deadline(db, breach) == aware + timedelta(hours=72)


def test_extension_must_ask_for_longer_than_the_statutory_deadline(db, tenant):
    breach = _prepare(db, principals=("BR-E3",))
    aware = breach.aware_at.astimezone(timezone.utc)
    with pytest.raises(bs.BreachError):
        bs.request_extension(
            db, breach, requested_until=aware + timedelta(hours=48), reason="r",
            actor_username="test-dpo",
        )


def test_a_granted_extension_updates_an_already_generated_pending_report(db, tenant):
    breach = _prepare(db, principals=("BR-E4",))
    aware = breach.aware_at.astimezone(timezone.utc)
    org = db.query(Organization).filter(Organization.id == breach.tenant_id).first()
    org.settings = {}  # no Board address: the filing stays PENDING
    db.commit()

    filing = bs.file_board_detailed(db, breach, actor_username="test-dpo")
    assert filing.status == "PENDING"
    assert filing.deadline_at.astimezone(timezone.utc) == aware + timedelta(hours=72)

    request = bs.request_extension(
        db, breach, requested_until=aware + timedelta(hours=96), reason="r",
        actor_username="test-dpo",
    )
    bs.decide_extension(db, breach, request, status="GRANTED",
                        granted_until=aware + timedelta(hours=96), actor_username="test-dpo")
    db.refresh(filing)
    assert filing.deadline_at.astimezone(timezone.utc) == aware + timedelta(hours=96)


# ---------------------------------------------------------------------------
# Content hashes
# ---------------------------------------------------------------------------

def test_content_hash_uses_the_audit_ledger_canonicalisation(db, tenant):
    """Not a second hashing scheme: the same canonical JSON the audit chain
    uses, so key order and timezone representation cannot change the hash."""
    import hashlib
    import json

    payload = {"b": 2, "a": 1, "when": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    reordered = {"when": datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                 "a": 1, "b": 2}
    assert bs.content_hash(payload) == bs.content_hash(reordered)
    expected = hashlib.sha256(
        json.dumps({"a": 1, "b": 2, "when": "2026-01-01T00:00:00+00:00"},
                   sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert bs.content_hash(payload) == expected


def test_a_tampered_filing_fails_its_content_hash(db, tenant):
    breach = _prepare(db, principals=("BR-H1",))
    filing = bs.file_board_detailed(db, breach, actor_username="test-dpo")
    assert bs.verify_notification_hash(filing)["matches"] is True

    tampered = dict(filing.payload)
    tampered["findings_on_actor"] = "No fault was found on the part of any person."
    filing.payload = tampered
    db.commit()

    verdict = bs.verify_notification_hash(filing)
    assert verdict["matches"] is False
    assert verdict["recorded_hash"] != verdict["recomputed_hash"]


def test_a_sent_filing_cannot_be_regenerated_over(db, tenant):
    breach = _prepare(db, principals=("BR-H2",))
    bs.file_board_initial(db, breach, actor_username="test-dpo")
    dispatch_pending(db)
    bs.sync_delivery(db, breach)
    with pytest.raises(bs.BreachError) as exc:
        bs.file_board_initial(db, breach, actor_username="test-dpo")
    assert "already been" in str(exc.value)


def test_the_database_refuses_a_duplicate_regulator_filing(db, tenant):
    """The plain unique constraint cannot enforce this - regulator filings have
    customer_id NULL and Postgres treats NULLs as distinct - so a partial
    unique index over (breach_id, recipient_type, stage) WHERE customer_id IS
    NULL does. Inserted directly, bypassing the service's own upsert."""
    from sqlalchemy.exc import IntegrityError

    breach = _prepare(db, principals=("BR-H5",))
    bs.file_board_initial(db, breach, actor_username="test-dpo")
    db.add(BreachNotification(
        breach_id=breach.id, tenant_id=breach.tenant_id,
        recipient_type="BOARD", stage="INITIAL", customer_id=None,
        payload={}, content_hash="x" * 64,
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_out_of_band_filing_is_recorded_with_its_reference(db, tenant):
    breach = _prepare(db, principals=("BR-H3",))
    org = db.query(Organization).filter(Organization.id == breach.tenant_id).first()
    org.settings = {}  # the Board has a portal, not an API
    db.commit()

    filing = bs.file_board_initial(db, breach, actor_username="test-dpo")
    assert filing.status == "PENDING"
    assert "No Data Protection Board of India address configured" in filing.last_error

    bs.record_filing(db, breach, filing, filing_reference="DPB/2026/000123",
                     actor_username="test-dpo")
    assert filing.status == "DELIVERED"
    assert filing.filing_reference == "DPB/2026/000123"
    assert filing.sent_at is not None


def test_a_principal_notice_cannot_be_marked_filed_by_hand(db, tenant):
    breach = _prepare(db, principals=("BR-H4",))
    bs.notify_principals(db, breach, actor_username="test-dpo")
    filing = (
        db.query(BreachNotification)
        .filter(BreachNotification.breach_id == breach.id,
                BreachNotification.recipient_type == "PRINCIPAL")
        .first()
    )
    with pytest.raises(bs.BreachError):
        bs.record_filing(db, breach, filing, filing_reference="X", actor_username="t")


# ---------------------------------------------------------------------------
# Workflow and closure
# ---------------------------------------------------------------------------

def test_closure_is_refused_while_a_rule_7_obligation_is_outstanding(db, tenant):
    breach = _prepare(db, principals=("BR-W1",))
    bs.transition_breach(db, breach, "CONTAINED", actor_username="test-dpo")
    outstanding = bs.outstanding_obligations(db, breach)
    assert any("Board initial" in o for o in outstanding)
    assert any("72-hour" in o for o in outstanding)
    assert any("CERT-In" in o for o in outstanding)
    with pytest.raises(bs.BreachError):
        bs.transition_breach(db, breach, "CLOSED", actor_username="test-dpo")

    bs.notify_principals(db, breach, actor_username="test-dpo")
    bs.file_board_initial(db, breach, actor_username="test-dpo")
    bs.file_board_detailed(db, breach, actor_username="test-dpo")
    bs.file_cert_in(db, breach, actor_username="test-dpo")
    dispatch_pending(db)
    bs.sync_delivery(db, breach)

    assert bs.outstanding_obligations(db, breach) == []
    bs.transition_breach(db, breach, "CLOSED", note="done", actor_username="test-dpo")
    assert breach.status == "CLOSED" and breach.closed_at is not None


def test_invalid_status_transition_is_refused(db, tenant):
    breach = _register(db)
    with pytest.raises(bs.BreachError):
        bs.transition_breach(db, breach, "CLOSED", actor_username="t")


def test_principal_clock_will_not_claim_completion_before_the_scope_is_final(db, tenant):
    breach = _register(db)
    bs.mark_aware(db, breach, actor_username="test-dpo")
    _customer(db, "BR-SC1")
    bs.add_affected_principals(db, breach, external_ids=["BR-SC1"], actor_username="test-dpo")
    bs.notify_principals(db, breach, actor_username="test-dpo")
    dispatch_pending(db)

    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "PRINCIPAL_WITHOUT_DELAY")
    assert clock["scope_finalised"] is False
    assert clock["status"] != "MET"
    assert clock["scope_note"]

    bs.finalise_scope(db, breach, actor_username="test-dpo")
    clock = next(c for c in bs.breach_clocks(db, breach) if c["clock"] == "PRINCIPAL_WITHOUT_DELAY")
    assert clock["status"] == "MET"


# ---------------------------------------------------------------------------
# K-39 / K-40 / K-41
# ---------------------------------------------------------------------------

def test_metrics_report_k39_k40_and_k41(db, tenant):
    breach = _prepare(db, principals=("BR-K1", "BR-K2"), aware_offset_hours=3)
    bs.notify_principals(db, breach, actor_username="test-dpo")
    bs.file_board_initial(db, breach, actor_username="test-dpo")
    bs.file_board_detailed(db, breach, actor_username="test-dpo")
    bs.file_cert_in(db, breach, actor_username="test-dpo")
    dispatch_pending(db)
    bs.sync_delivery(db, breach)

    metrics = bs.breach_metrics(db, source_app=SOURCE)
    assert metrics["scope"] == SOURCE

    k39 = metrics["K-39"]
    assert k39["provision"] == "DPDP Rules 2025, R.7(1)"
    assert k39["mean_time_to_detect_hours"] is not None
    assert k39["mean_hours_to_first_principal_notice"] is not None

    k40 = metrics["K-40"]
    assert k40["detailed_reports_filed"] >= 1
    assert k40["detailed_within_deadline_pct"] is not None
    assert k40["mean_hours_to_board_initial"] is not None

    k41 = metrics["K-41"]
    assert k41["affected_principals_total"] >= 2
    assert 0 <= k41["notice_delivery_rate_pct"] <= 100

    # The aggregates above are module-wide - earlier tests in this file
    # deliberately create a late CERT-In filing, so asserting 100% here would
    # only prove the tests ran in a particular order. The per-breach row is
    # where this breach's own figures are checked exactly.
    assert metrics["cert_in"]["within_6h_pct"] is not None

    row = next(r for r in metrics["per_breach"] if r["breach_ref"] == breach.breach_ref)
    assert row["affected_principals"] == 2
    assert row["principal_notices_delivered"] == 2
    assert row["board_detailed_within_deadline"] is True
    assert row["cert_in_within_6h"] is True
    assert row["principal_notice_delivery_rate_pct"] == 100.0
    assert row["hours_to_first_principal_notice"] is not None
    assert row["hours_to_board_detailed"] is not None


def test_register_extract_carries_clocks_and_hashes(db, tenant):
    breach = _prepare(db, principals=("BR-R1",))
    bs.notify_principals(db, breach, actor_username="test-dpo")
    bs.file_board_initial(db, breach, actor_username="test-dpo")
    dispatch_pending(db)

    rows = bs.breach_register(db, source_app=SOURCE)
    row = next(r for r in rows if r["breach_ref"] == breach.breach_ref)
    assert len(row["clocks"]) == 4
    assert all(f["content_hash"] for f in row["filings"])
    assert any(f["recipient_type"] == "PRINCIPAL" for f in row["filings"])
    assert any(f["recipient_type"] == "BOARD" for f in row["filings"])


def test_the_register_is_deterministic_for_a_given_database_state(db, tenant):
    """app/services/evidence_pack.py takes a SHA-256 over the canonical JSON of
    every section it emits, so a register that embedded a live countdown would
    give the same pack, for the same period, a different manifest hash on every
    generation - and a regulator could not re-derive the hash they were handed.
    An OPEN clock (nothing filed yet) is the case that would tick."""
    breach = _prepare(db, principals=("BR-R2",), aware_offset_hours=1)
    first = bs.breach_register(db, source_app=SOURCE)
    second = bs.breach_register(db, source_app=SOURCE)
    assert first == second
    assert canonical_json_of(first) == canonical_json_of(second)

    row = next(r for r in first if r["breach_ref"] == breach.breach_ref)
    open_clock = next(c for c in row["clocks"] if c["status"] == "OPEN")
    # The live countdown is absent from the record...
    assert "remaining_hours" not in open_clock
    assert "elapsed_hours" not in open_clock
    # ...but the recorded facts a reader actually needs are all there.
    assert open_clock["started_at"] and open_clock["basis"] and open_clock["measured_against"]

    # A discharged obligation keeps its elapsed time: that is a fixed
    # satisfied_at - started_at, not a countdown.
    bs.file_cert_in(db, breach, actor_username="test-dpo")
    dispatch_pending(db)
    row = next(
        r for r in bs.breach_register(db, source_app=SOURCE)
        if r["breach_ref"] == breach.breach_ref
    )
    cert = next(c for c in row["clocks"] if c["clock"] == "CERT_IN_6H")
    assert cert["status"] == "MET"
    assert cert["elapsed_hours"] is not None
    assert "remaining_hours" not in cert


def canonical_json_of(value):
    return bs.canonical_json(value)


# ---------------------------------------------------------------------------
# Audit trail and tenancy
# ---------------------------------------------------------------------------

def test_every_step_is_written_to_the_append_only_ledger(db, tenant):
    breach = _prepare(db, principals=("BR-A1",))
    bs.notify_principals(db, breach, actor_username="test-dpo")
    bs.file_board_initial(db, breach, actor_username="test-dpo")
    bs.file_cert_in(db, breach, actor_username="test-dpo")

    events = {
        e[0] for e in
        db.query(AuditLog.event).filter(AuditLog.source_app == SOURCE).distinct().all()
    }
    for expected in (
        "BREACH_REGISTERED", "BREACH_AWARENESS_RECORDED", "BREACH_SCOPE_UPDATED",
        "BREACH_SCOPE_FINALISED", "BREACH_PRINCIPAL_NOTICES_ISSUED",
        "BREACH_BOARD_NOTIFIED", "BREACH_CERT_IN_NOTIFIED",
    ):
        assert expected in events, f"{expected} missing from {sorted(events)}"

    # Scoped to this tenant's chain: tests/test_audit_chain.py deliberately
    # tampers a row in another tenant's chain and leaves it tampered, so a
    # global assertion here would be asserting that test failed to do its job.
    from app.core.audit_chain import verify_chain
    tenant_id = resolve_tenant_id(db, SOURCE)
    broken = [b for b in verify_chain(db)["broken"] if b["tenant_id"] == tenant_id]
    assert broken == []


def test_breach_permissions_are_their_own_pair_not_policy_manage():
    """R3-08 gates on breach.view/breach.manage, not policy.manage: filing with
    the Board is a regulator-facing act, not policy editing. Anyone who can
    edit a policy must not thereby be able to file - or fail to file - a
    statutory notification."""
    from app.core.rbac import (
        ALL_PERMISSIONS,
        PERM_BREACH_MANAGE,
        PERM_BREACH_VIEW,
        ROLE_PERMISSIONS,
    )

    assert PERM_BREACH_VIEW in ALL_PERMISSIONS
    assert PERM_BREACH_MANAGE in ALL_PERMISSIONS

    # Only the two roles that should actually file hold `manage`.
    holders = {r for r, perms in ROLE_PERMISSIONS.items() if PERM_BREACH_MANAGE in perms}
    assert holders == {"admin", "dpo"}, holders

    # The DPO is the s.10(2)(a) point of contact and must hold it...
    assert PERM_BREACH_MANAGE in ROLE_PERMISSIONS["dpo"]
    # ...and holding policy.manage must not be a back door into it.
    for role, perms in ROLE_PERMISSIONS.items():
        if "policy.manage" in perms and role not in holders:
            pytest.fail(f"role '{role}' has policy.manage but not breach.manage - check the gating")

    # Every role that can read the audit trail can read the register.
    for role, perms in ROLE_PERMISSIONS.items():
        if "audit.view" in perms:
            assert PERM_BREACH_VIEW in perms, role

    # The read-only role stays read-only.
    assert PERM_BREACH_MANAGE not in ROLE_PERMISSIONS["auditor"]


def test_breach_routes_are_gated_on_the_breach_permissions():
    """A route that slipped back to policy.manage/audit.view would still work
    for an admin token and pass every other test in this file."""
    from app.api.routes import breaches as breach_routes

    source = __import__("pathlib").Path(breach_routes.__file__).read_text()
    assert "PERM_POLICY_MANAGE" not in source
    assert "PERM_AUDIT_VIEW" not in source
    assert "require_permission(PERM_BREACH_MANAGE)" in source
    assert "require_permission(PERM_BREACH_VIEW)" in source
    # Filing content stays on the ledger-export bar.
    assert "require_permission(PERM_AUDIT_EXPORT)" in source


def test_scoping_a_principal_from_another_tenant_is_refused(db, tenant):
    """A caller-supplied external id is resolved through resolve_customer, so
    another tenant's principal is simply not found - never pulled silently
    into this tenant's breach."""
    _customer(db, "BR-X1", source_app=OTHER_SOURCE)
    breach = _register(db, source_app=SOURCE)
    bs.mark_aware(db, breach, actor_username="test-dpo")
    result = bs.add_affected_principals(
        db, breach, external_ids=["BR-X1"], actor_username="test-dpo"
    )
    assert result["added"] == 0
    assert result["unresolved"] == ["BR-X1"]


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

def test_http_end_to_end_produces_notices_reports_and_clocks(breach_client, db, staff_token, tenant):
    _customer(db, "BR-HTTP1")
    _customer(db, "BR-HTTP2")
    now = utcnow()

    created = breach_client.post("/breaches", json={
        "title": "HTTP end-to-end breach",
        "source_app": SOURCE,
        "occurred_at": (now - timedelta(hours=20)).isoformat(),
        "detected_at": (now - timedelta(hours=6)).isoformat(),
        "severity": "HIGH",
        "nature": "Unauthorised access to the profile store",
        "extent": "Name, email and phone",
        "location": "ap-south-1 replica",
        "likely_impact": "Phishing risk",
        "likely_consequences": "Your contact details may have been read",
        "mitigation_measures": "Credential revoked and replica isolated",
        "safety_measures": "Do not act on messages asking you to confirm details",
        "cause": "Compromised contractor endpoint",
    }, headers=_auth(staff_token))
    assert created.status_code == 201, created.text
    ref = created.json()["breach_ref"]
    assert all(c["status"] == "NOT_STARTED" for c in created.json()["clocks"])

    aware = breach_client.post(
        f"/breaches/{ref}/aware",
        json={"aware_at": (now - timedelta(hours=2)).isoformat()},
        headers=_auth(staff_token),
    )
    assert aware.status_code == 200, aware.text
    clocks = {c["clock"]: c for c in aware.json()["clocks"]}
    assert clocks["CERT_IN_6H"]["status"] == "OPEN"
    assert clocks["BOARD_DETAILED_72H"]["deadline_kind"] == "STATUTORY"
    assert clocks["PRINCIPAL_WITHOUT_DELAY"]["deadline_kind"] == "WITHOUT_DELAY_NO_FIXED_PERIOD"

    scoped = breach_client.post(
        f"/breaches/{ref}/affected",
        json={"external_ids": ["BR-HTTP1", "BR-HTTP2"], "data_involved": "name, email"},
        headers=_auth(staff_token),
    )
    assert scoped.json()["affected_count"] == 2
    breach_client.post(f"/breaches/{ref}/affected/finalise", headers=_auth(staff_token))

    notices = breach_client.post(
        f"/breaches/{ref}/notify/principals", json={}, headers=_auth(staff_token)
    )
    assert notices.status_code == 200, notices.text
    assert notices.json()["generated"] == 2

    # The 72-hour report is refused while sections (iv) and (v) are empty.
    refused = breach_client.post(
        f"/breaches/{ref}/notify/board-detailed", headers=_auth(staff_token)
    )
    assert refused.status_code == 422
    assert "R.7(2)(b)(iv)" in refused.json()["detail"]

    preview = breach_client.get(
        f"/breaches/{ref}/reports/board-detailed", headers=_auth(staff_token)
    )
    assert preview.status_code == 200
    assert preview.json()["missing_mandated_sections"]

    breach_client.patch(f"/breaches/{ref}", json={
        "findings_on_actor": "Contractor endpoint compromise; no insider intent",
        "remedial_measures": "Just-in-time access and hardware MFA",
    }, headers=_auth(staff_token))

    assert breach_client.post(
        f"/breaches/{ref}/notify/board-initial", headers=_auth(staff_token)
    ).status_code == 200
    detailed = breach_client.post(
        f"/breaches/{ref}/notify/board-detailed", headers=_auth(staff_token)
    )
    assert detailed.status_code == 200, detailed.text
    cert = breach_client.post(f"/breaches/{ref}/notify/cert-in", headers=_auth(staff_token))
    assert cert.status_code == 200, cert.text

    dispatch_pending(db)

    listing = breach_client.get(f"/breaches/{ref}/notifications", headers=_auth(staff_token))
    kinds = {(n["recipient_type"], n["stage"]) for n in listing.json()}
    assert ("PRINCIPAL", "NOTICE") in kinds
    assert ("BOARD", "INITIAL") in kinds
    assert ("BOARD", "DETAILED") in kinds
    assert ("CERT_IN", "INITIAL") in kinds

    board_detailed_id = next(
        n["id"] for n in listing.json()
        if n["recipient_type"] == "BOARD" and n["stage"] == "DETAILED"
    )
    verified = breach_client.get(
        f"/breaches/{ref}/notifications/{board_detailed_id}/verify", headers=_auth(staff_token)
    )
    assert verified.json()["matches"] is True

    timeline = breach_client.get(f"/breaches/{ref}/clocks", headers=_auth(staff_token)).json()
    assert {c["status"] for c in timeline["clocks"]} <= {"MET", "MET_LATE", "OPEN", "OVERDUE"}
    assert any(e["event"] == "AWARE" for e in timeline["timeline"])
    assert any(e["event"] == "CERT_IN_DEADLINE" for e in timeline["timeline"])

    metrics = breach_client.get(
        "/breaches/metrics", params={"source_app": SOURCE}, headers=_auth(staff_token)
    ).json()
    assert "K-39" in metrics and "K-40" in metrics and "K-41" in metrics
    row = next(r for r in metrics["per_breach"] if r["breach_ref"] == ref)
    assert row["principal_notices_delivered"] == 2


def test_http_extension_log(breach_client, db, staff_token, tenant):
    breach = _prepare(db, principals=("BR-HTTPE",))
    aware = breach.aware_at.astimezone(timezone.utc)
    created = breach_client.post(
        f"/breaches/{breach.breach_ref}/extensions",
        json={
            "requested_until": (aware + timedelta(hours=120)).isoformat(),
            "reason": "Forensics incomplete",
            "written_request_ref": "LETTER/2",
        },
        headers=_auth(staff_token),
    )
    assert created.status_code == 201, created.text
    extension_id = created.json()["id"]

    decided = breach_client.post(
        f"/breaches/{breach.breach_ref}/extensions/{extension_id}/decision",
        json={
            "status": "GRANTED",
            "granted_until": (aware + timedelta(hours=120)).isoformat(),
            "board_reference": "DPB/EXT/2",
        },
        headers=_auth(staff_token),
    )
    assert decided.status_code == 200, decided.text
    clocks = breach_client.get(
        f"/breaches/{breach.breach_ref}/clocks", headers=_auth(staff_token)
    ).json()["clocks"]
    board = next(c for c in clocks if c["clock"] == "BOARD_DETAILED_72H")
    assert board["extension_granted"] is True
    assert board["deadline_at"] == (aware + timedelta(hours=120)).isoformat()


def test_metrics_route_is_not_shadowed_by_the_breach_ref_route(breach_client, staff_token, tenant):
    """`/breaches/metrics` is declared before `/breaches/{breach_ref}`; if that
    order is ever reversed this returns a 404 for breach 'metrics' instead."""
    response = breach_client.get("/breaches/metrics", headers=_auth(staff_token))
    assert response.status_code == 200
    assert "K-40" in response.json()


def test_unknown_breach_is_404_not_500(breach_client, staff_token, tenant):
    assert breach_client.get("/breaches/BR-9999-9999", headers=_auth(staff_token)).status_code == 404
