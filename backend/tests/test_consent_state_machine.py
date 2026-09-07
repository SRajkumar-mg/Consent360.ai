"""R1-13: unit tests for the consent lifecycle state machine.

`CONSENT_STATUSES` / `CONSENT_TRANSITIONS` (app/models/entities.py) declare the
legal moves; `app/services/consent.py` is supposed to be the only place that
ever assigns `Consent.status` (docs/ARCHITECTURE.md: "Never set Consent.status
directly"). These tests exercise that module directly rather than through an
HTTP route, so a broken guard fails here with a precise status pair rather
than inside an integration test that happens to touch the same code path.

Previously two different enforcement styles existed side by side in
services/consent.py: `request_consent` and `withdraw_consent` went through the
shared `_validate_transition(from_status, to_status, action)` helper (which
looks the move up in `CONSENT_TRANSITIONS`), while `grant_consent`,
`deny_consent` and `renew_consent` each carried their own hand-rolled
"reachable from these statuses" list that silently contradicted the table
(e.g. `CONSENT_TRANSITIONS` said GRANTED was not a legal target from
NOT_REQUESTED or REQUESTED, yet `grant_consent` allowed both - the
direct-accept flow of a cookie banner or portal grant with no prior formal
REQUESTED step). That divergence has been reconciled: `CONSENT_TRANSITIONS`
now describes every transition the code actually permits, and all five
functions call `_validate_transition` - there is one source of truth, and
deleting an entry from the table forbids that move everywhere.

`test_every_transition_function_accepts_exactly_the_table_permitted_sources`
below is the regression guard: for each of the five functions it independently
derives, from `CONSENT_TRANSITIONS` itself, which from-statuses are supposed
to reach that function's target status, and then drives the real function from
every declared status to confirm its accept/reject behaviour matches exactly.
It does not merely call `_validate_transition` and compare - it drives the
actual public function - so it still fails if a future change reintroduces a
private, table-bypassing status list in any one of them.
"""
import pytest
from fastapi import HTTPException

from app.models.entities import (
    CONSENT_STATUSES,
    CONSENT_TRANSITIONS,
    Consent,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.services import consent as consent_service


def _make_purpose(db, code, retention_period_days=None):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(
        name=f"Purpose {code}", code=code, requires_consent=True,
        retention_period_days=retention_period_days,
    )
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose, category, activity


def _make_consent(db, code):
    purpose, category, activity = _make_purpose(db, code)
    customer = Customer(external_id=f"CUST-{code.upper()}", name=f"{code} Customer", source_app="STATE_MACHINE_TEST")
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="STATE_MACHINE_TEST",
    )
    return consent


# ---------------------------------------------------------------------------
# Structural sanity: the declared table itself is internally consistent.
# ---------------------------------------------------------------------------

def test_every_transition_source_and_target_is_a_declared_status():
    for from_status, targets in CONSENT_TRANSITIONS.items():
        assert from_status in CONSENT_STATUSES, f"{from_status!r} is a transition source but not a declared status"
        for target in targets:
            assert target in CONSENT_STATUSES, f"{from_status!r} -> {target!r}: target is not a declared status"


def test_superseded_is_terminal():
    assert CONSENT_TRANSITIONS["SUPERSEDED"] == []


def test_every_status_has_a_transitions_entry():
    # _validate_transition does `.get(from_status, [])`, so a status missing
    # from the table would silently behave as terminal rather than raising a
    # KeyError - worth knowing about explicitly rather than by omission.
    for status in CONSENT_STATUSES:
        assert status in CONSENT_TRANSITIONS, f"{status!r} has no CONSENT_TRANSITIONS entry"


# ---------------------------------------------------------------------------
# request_consent: goes through _validate_transition / CONSENT_TRANSITIONS.
# ---------------------------------------------------------------------------

def test_request_consent_from_not_requested_succeeds(db):
    consent = _make_consent(db, "sm_request_ok")
    assert consent.status == "NOT_REQUESTED"
    consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "REQUESTED"
    assert consent.requested_at is not None


def test_request_consent_from_already_requested_is_rejected(db):
    consent = _make_consent(db, "sm_request_bad")
    consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "REQUESTED"
    with pytest.raises(HTTPException) as exc_info:
        consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# grant_consent: now also routed through _validate_transition / CONSENT_TRANSITIONS.
# ---------------------------------------------------------------------------

def test_grant_consent_from_requested_succeeds_and_writes_evidence(db):
    consent = _make_consent(db, "sm_grant_ok")
    consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "GRANTED"
    assert consent.granted_at is not None
    assert len(consent.evidence) == 1
    assert consent.notice_version_id == consent.evidence[-1].notice_version_id


def test_grant_consent_from_active_is_rejected(db):
    consent = _make_consent(db, "sm_grant_bad")
    consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    consent_service.activate_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "ACTIVE"
    with pytest.raises(HTTPException) as exc_info:
        consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert exc_info.value.status_code == 400


def test_grant_consent_permits_direct_accept_with_no_prior_request(db):
    """CONSENT_TRANSITIONS deliberately lists GRANTED as reachable directly
    from NOT_REQUESTED and REQUESTED - the direct-accept flow (a cookie
    banner's accept-all, or a portal grant with no prior formal REQUESTED
    step). This is intentional, reconciled behaviour, not a gap."""
    assert "GRANTED" in CONSENT_TRANSITIONS["NOT_REQUESTED"]
    assert "GRANTED" in CONSENT_TRANSITIONS["REQUESTED"]

    direct = _make_consent(db, "sm_grant_direct")
    assert direct.status == "NOT_REQUESTED"
    consent_service.grant_consent(db, direct, source_app="STATE_MACHINE_TEST")
    assert direct.status == "GRANTED"

    requested = _make_consent(db, "sm_grant_from_requested")
    consent_service.request_consent(db, requested, source_app="STATE_MACHINE_TEST")
    consent_service.grant_consent(db, requested, source_app="STATE_MACHINE_TEST")
    assert requested.status == "GRANTED"


# ---------------------------------------------------------------------------
# deny_consent: now also routed through _validate_transition / CONSENT_TRANSITIONS.
# ---------------------------------------------------------------------------

def test_deny_consent_from_requested_succeeds(db):
    consent = _make_consent(db, "sm_deny_ok")
    consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    consent_service.deny_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "DENIED"
    assert consent.denied_at is not None


def test_deny_consent_from_granted_is_rejected(db):
    consent = _make_consent(db, "sm_deny_bad")
    consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    with pytest.raises(HTTPException) as exc_info:
        consent_service.deny_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# withdraw_consent: also on _validate_transition / CONSENT_TRANSITIONS.
# ---------------------------------------------------------------------------

def test_withdraw_consent_from_granted_succeeds(db):
    consent = _make_consent(db, "sm_withdraw_ok")
    consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    consent_service.withdraw_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "WITHDRAWN"
    assert consent.withdrawn_at is not None


def test_withdraw_consent_from_not_requested_is_rejected(db):
    consent = _make_consent(db, "sm_withdraw_bad")
    assert consent.status == "NOT_REQUESTED"
    with pytest.raises(HTTPException) as exc_info:
        consent_service.withdraw_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# renew_consent: bumps consent_version; also routed through _validate_transition.
# ---------------------------------------------------------------------------

def test_renew_consent_from_granted_bumps_version_and_writes_evidence(db):
    consent = _make_consent(db, "sm_renew_ok")
    consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    version_before = consent.consent_version
    consent_service.renew_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "RENEWED"
    assert consent.consent_version == version_before + 1
    assert len(consent.evidence) == 2  # one from grant, one from renew


def test_renew_consent_from_denied_is_rejected(db):
    consent = _make_consent(db, "sm_renew_bad")
    consent_service.request_consent(db, consent, source_app="STATE_MACHINE_TEST")
    consent_service.deny_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert consent.status == "DENIED"
    with pytest.raises(HTTPException) as exc_info:
        consent_service.renew_consent(db, consent, source_app="STATE_MACHINE_TEST")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# expire_consents: the sweep, not a per-consent guard.
# ---------------------------------------------------------------------------

def test_expire_consents_only_expires_those_past_their_expiry(db):
    from datetime import datetime, timedelta, timezone

    overdue = _make_consent(db, "sm_expire_overdue")
    consent_service.grant_consent(db, overdue, expires_in_days=1, source_app="STATE_MACHINE_TEST")
    overdue.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    not_due = _make_consent(db, "sm_expire_not_due")
    consent_service.grant_consent(db, not_due, expires_in_days=30, source_app="STATE_MACHINE_TEST")
    db.commit()

    count = consent_service.expire_consents(db)
    assert count >= 1

    db.refresh(overdue)
    db.refresh(not_due)
    assert overdue.status == "EXPIRED"
    assert not_due.status == "GRANTED"


# ---------------------------------------------------------------------------
# update_consent_for_purpose_version: silently no-ops outside its own list
# (no HTTPException - a different contract than every guard above).
# ---------------------------------------------------------------------------

def test_update_consent_for_purpose_version_applies_from_granted(db):
    consent = _make_consent(db, "sm_update_ok")
    consent_service.grant_consent(db, consent, source_app="STATE_MACHINE_TEST")
    purpose = consent.purpose
    new_pv = PurposeVersion(
        purpose_id=purpose.id, version_number=2, name=purpose.name,
        data_category_ids=[consent.data_category_id], processing_activity_ids=[consent.processing_activity_id],
        consent_text="I consent (v2).", is_current=True, created_by="test",
    )
    db.add(new_pv)
    db.commit()
    consent_service.update_consent_for_purpose_version(db, consent, new_pv, source_app="STATE_MACHINE_TEST")
    assert consent.status == "UPDATED"
    assert consent.purpose_version_id == new_pv.id


def test_update_consent_for_purpose_version_no_ops_outside_its_list(db):
    consent = _make_consent(db, "sm_update_noop")
    assert consent.status == "NOT_REQUESTED"
    purpose = consent.purpose
    new_pv = PurposeVersion(
        purpose_id=purpose.id, version_number=2, name=purpose.name,
        data_category_ids=[consent.data_category_id], processing_activity_ids=[consent.processing_activity_id],
        consent_text="I consent (v2).", is_current=True, created_by="test",
    )
    db.add(new_pv)
    db.commit()
    result = consent_service.update_consent_for_purpose_version(db, consent, new_pv, source_app="STATE_MACHINE_TEST")
    assert result.status == "NOT_REQUESTED"
    assert result.purpose_version_id != new_pv.id


# ---------------------------------------------------------------------------
# Reconciliation regression guard.
#
# request_consent, grant_consent, deny_consent, withdraw_consent and
# renew_consent now all enforce CONSENT_TRANSITIONS via _validate_transition,
# so CONSENT_TRANSITIONS is the only place a divergence could reappear (e.g. a
# future edit reintroducing a private, hand-rolled status list inside one of
# them). For each function this independently derives - straight from
# CONSENT_TRANSITIONS, not from any copy of the old private lists - which
# from-statuses are supposed to reach that function's target status, then
# drives the real public function from every declared CONSENT_STATUSES value
# and checks its accept/reject behaviour matches exactly. Because it calls the
# actual function rather than re-deriving from _validate_transition, it still
# catches a regression where a function stops consulting the table at all.
# ---------------------------------------------------------------------------

_TRANSITION_FUNCTIONS = {
    "REQUESTED": consent_service.request_consent,
    "GRANTED": consent_service.grant_consent,
    "DENIED": consent_service.deny_consent,
    "WITHDRAWN": consent_service.withdraw_consent,
    "RENEWED": consent_service.renew_consent,
}


@pytest.mark.parametrize("target_status", sorted(_TRANSITION_FUNCTIONS))
def test_every_transition_function_accepts_exactly_the_table_permitted_sources(db, target_status):
    fn = _TRANSITION_FUNCTIONS[target_status]
    permitted_sources = {
        from_status for from_status, targets in CONSENT_TRANSITIONS.items() if target_status in targets
    }
    # Sanity check on the derivation itself: a target nobody can legally reach
    # would make every iteration below a (vacuous) rejection check.
    assert permitted_sources, f"CONSENT_TRANSITIONS grants no path to {target_status!r} at all"

    for from_status in CONSENT_STATUSES:
        consent = _make_consent(db, f"sm_all_{target_status.lower()}_{from_status.lower()}")
        consent.status = from_status
        db.commit()

        if from_status in permitted_sources:
            fn(db, consent, source_app="STATE_MACHINE_TEST")
            db.refresh(consent)
            assert consent.status == target_status, (
                f"{fn.__name__} from {from_status!r} is declared legal by CONSENT_TRANSITIONS "
                f"(target {target_status!r}) but the function did not reach it"
            )
        else:
            with pytest.raises(HTTPException) as exc_info:
                fn(db, consent, source_app="STATE_MACHINE_TEST")
            assert exc_info.value.status_code == 400, (
                f"{fn.__name__} from {from_status!r} should be rejected: CONSENT_TRANSITIONS permits "
                f"{target_status!r} only from {sorted(permitted_sources)}"
            )
