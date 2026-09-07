"""B-01/B-02 (s.6(10)): the CRM/Codex/SkillLearn cookie banner records a
refusal, it does not merely fail to record a grant.

THE DEFECT THESE PIN. `POST /portal/deny` was added and CareerHub - one of the
four demo sites - started recording refusals through it. The other three (CRM,
Codex, SkillLearn) never call `/portal/*` at all: they save the banner through
`PUT /crm/customers/{id}/consent-preferences`, which mirrors the toggles into
consent rows in `app/api/routes/crm.py::_sync_consent_preferences`. That
function handled a switched-off category with

    elif consent.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED"):
        withdraw_consent(...)

- i.e. it only ever WITHDREW something already granted, and did nothing at all
for a row that had never been granted. `deny_consent` was never called from it.

Reproduced live against the running backend: a first-time CRM_PORTAL visitor
who rejected every one of the four categories got HTTP 200 and left all 33 of
her consent rows `NOT_REQUESTED`, zero `consent_evidence` rows, and only
`CONSENT_CREATED` in the audit log. On three of the four demo sites a refusal
therefore lived in a `consent_preferences` blob and that browser's
localStorage and nowhere else - indistinguishable, in the ledger, from never
having been asked, so s.6(10)'s burden of proving she refused could not be
discharged, and on a new device the banner asked again as though nothing had
ever been decided.

`CONSENT_TRANSITIONS` already permitted NOT_REQUESTED/REQUESTED/PENDING ->
DENIED and `deny_consent` already existed. Only this call site was missing.

These tests pin, in order: that a first-time reject-all lands in the ledger as
DENIED with history, evidence and audit; that its evidence is no weaker than a
grant's; that an already-active consent is still WITHDRAWN rather than denied;
that a refusal never overwrites a record the principal already made and never
double-records; that the server-observed GPC signal survives onto a refusal;
that the GPC enforcement on the grant path is not bypassed by any of it; and
that the "which statuses" rule has exactly one definition, shared with
`/portal/deny`.
"""
import pytest

from app.core.encryption import hmac_digest
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentEvidence,
    ConsentHistory,
    CrmCustomer,
    DataCategory,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)

# The banner only touches purposes whose code is one of
# COOKIE_CATEGORY_TO_PURPOSE's values - see app/api/routes/crm.py.
COOKIE_CATEGORIES = {"necessary": False, "functional": False, "analytics": False, "advertising": False}

REJECT_ALL = dict(COOKIE_CATEGORIES)
ACCEPT_ALL = {k: True for k in COOKIE_CATEGORIES}

BANNER_CONTEXT = {
    "language": "en",
    "session_id": "sess-reject-all",
    "screen_id": "cookie_banner",
    "ui_control_id": "reject_all",
    "banner_version": "v3",
    "interaction_step": 1,
    "affirmative_action": "CLICK",
}


def _cookie_purpose(db, code, *, legal_basis="CONSENT", requires_consent=True):
    """Get-or-create one of the cookie purposes.

    `Purpose.code` is unique and the suite shares one database across the
    session, so this must never assume it is first - several files reach for
    these same four codes and the order they run in is randomised.
    """
    existing = db.query(Purpose).filter(Purpose.code == code).first()
    if existing:
        return existing
    category = DataCategory(name=f"cat-{code}", code=f"crmref_cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"crmref_act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=code.title(), code=code, legal_basis=legal_basis,
                      requires_consent=requires_consent, is_active=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose


@pytest.fixture()
def cookie_purposes(db):
    """`analytics` and `advertising` are the consent-based purposes these tests
    decide about; `strictly_necessary` is created with the SEEDED semantics
    (s.7(a), requires_consent False) because that is what the product ships and
    what the GPC tests assert about it - creating it as consent-based here
    would silently change their subject.

    `functional` is deliberately NOT created: tests/test_evidence.py creates it
    unconditionally, and `Purpose.code` is unique, so creating it here would
    make that file fail whenever this one happened to run first."""
    return {
        "necessary": _cookie_purpose(db, "strictly_necessary",
                                     legal_basis="S7_A", requires_consent=False),
        "analytics": _cookie_purpose(db, "analytics"),
        "advertising": _cookie_purpose(db, "advertising"),
    }


def _consent_based_consents(db, email):
    """The rows these tests decide about. `strictly_necessary` is excluded for
    the same reason the production code excludes it - see below."""
    return [c for c in _consents(db, email) if c.purpose.requires_consent]


def _visitor(db, email):
    """A first-time visitor: a CRM directory row and nothing else. No platform
    Customer, no consent rows - exactly the state the reproduction starts in."""
    row = CrmCustomer(name="Refusing Visitor", email=email, email_search=hmac_digest(email))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _banner(client, visitor, categories, *, headers=None, context=None):
    return client.put(
        f"/crm/customers/{visitor.id}/consent-preferences",
        headers=headers or {},
        json={"lang": "en", "categories": categories, "context": context or BANNER_CONTEXT},
    )


def _platform_customer(db, email):
    from app.services.tenancy import resolve_customer

    customer = resolve_customer(db, source_app="CRM_PORTAL", email=email)
    assert customer is not None, "the banner should have linked a platform Customer"
    return customer


def _consents(db, email):
    db.expire_all()
    customer = _platform_customer(db, email)
    return db.query(Consent).filter(Consent.customer_id == customer.id).all()


def _evidence(db, consent):
    return (
        db.query(ConsentEvidence)
        .filter(ConsentEvidence.consent_id == consent.id)
        .order_by(ConsentEvidence.id)
        .all()
    )


# =========================================================================== #
#  The reproduction, inverted
# =========================================================================== #
def test_a_first_time_visitor_rejecting_everything_is_recorded_as_denied(db, client, cookie_purposes):
    """The whole gap in one assertion: every row left NOT_REQUESTED before."""
    email = "crm-reject-first-time@example.com"
    visitor = _visitor(db, email)

    resp = _banner(client, visitor, REJECT_ALL)
    assert resp.status_code == 200, resp.text

    consents = _consent_based_consents(db, email)
    assert consents, "the banner must have materialised consent rows to decide about"
    assert {c.status for c in consents} == {"DENIED"}
    assert all(c.denied_at is not None for c in consents)
    assert all(c.granted_at is None for c in consents)


def test_the_refusal_is_evidenced(db, client, cookie_purposes):
    """Zero evidence rows before. A refusal that leaves no evidence is not a
    record of anything."""
    email = "crm-reject-evidence@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, REJECT_ALL)

    for consent in _consent_based_consents(db, email):
        evidence = _evidence(db, consent)
        assert len(evidence) == 1, "exactly one evidence row per refusal"
        row = evidence[0]
        assert row.collection_method == "UI"
        assert row.source_app == "CRM_PORTAL"
        # The evidence_ref is backfilled onto the CONSENT_DENIED history row,
        # exactly as a grant's and a withdrawal's are - otherwise the ledger
        # entry and its evidence are not linked to each other.
        history = (
            db.query(ConsentHistory)
            .filter(ConsentHistory.consent_id == consent.id,
                    ConsentHistory.action == "CONSENT_DENIED")
            .order_by(ConsentHistory.id.desc())
            .first()
        )
        assert history is not None
        assert history.details["evidence_ref"] == row.evidence_ref


def test_the_refusal_is_in_the_audit_ledger(db, client, cookie_purposes):
    """Only CONSENT_CREATED was written before - a row was materialised and
    then nothing happened to it, as far as the ledger could tell."""
    email = "crm-reject-audit@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, REJECT_ALL)

    consents = _consent_based_consents(db, email)
    for consent in consents:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.event == "CONSENT_DENIED", AuditLog.consent_id == consent.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].old_status == "NOT_REQUESTED"
        assert rows[0].new_status == "DENIED"


def test_a_refusals_evidence_is_no_weaker_than_a_grants(db, client, cookie_purposes):
    """The ClientContext must survive the refusal path intact. Two visitors,
    same banner payload, opposite answers: every evidentiary field a grant
    records, a refusal records too."""
    granting = _visitor(db, "crm-parity-grant@example.com")
    refusing = _visitor(db, "crm-parity-deny@example.com")

    headers = {"User-Agent": "Mozilla/5.0 (banner-parity)"}
    _banner(client, granting, ACCEPT_ALL, headers=headers)
    _banner(client, refusing, REJECT_ALL, headers=headers)

    def one_evidence(email, action):
        for consent in _consents(db, email):
            for row in _evidence(db, consent):
                if row.details.get("purpose_code") and (
                    (action == "grant" and consent.status in ("GRANTED", "ACTIVE"))
                    or (action == "deny" and consent.status == "DENIED")
                ):
                    return row
        raise AssertionError(f"no {action} evidence found for {email}")

    grant_row = one_evidence("crm-parity-grant@example.com", "grant")
    deny_row = one_evidence("crm-parity-deny@example.com", "deny")

    for field in ("language", "session_id", "screen_id", "ui_control_id",
                  "banner_version", "affirmative_action", "user_agent"):
        assert getattr(deny_row, field) == getattr(grant_row, field), field
        assert getattr(deny_row, field) is not None, field
    assert deny_row.ip_address == grant_row.ip_address
    assert deny_row.notice_version_id == grant_row.notice_version_id
    assert deny_row.signature is not None
    assert deny_row.details["interaction_step"] == grant_row.details["interaction_step"]


# =========================================================================== #
#  Deny what was never granted; withdraw what is live
# =========================================================================== #
def test_an_active_consent_is_withdrawn_not_denied(db, client, cookie_purposes):
    """The branch that already worked must keep working: DENIED is not a legal
    transition out of ACTIVE, and calling a withdrawal a denial would misstate
    what happened - valid consent DID exist and was revoked."""
    email = "crm-active-then-reject@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, ACCEPT_ALL)
    assert {c.status for c in _consents(db, email)} <= {"GRANTED", "ACTIVE", "DENIED"}

    _banner(client, visitor, REJECT_ALL)

    consents = _consents(db, email)
    assert consents
    assert {c.status for c in consents} == {"WITHDRAWN"}
    for consent in consents:
        assert db.query(AuditLog).filter(
            AuditLog.event == "CONSENT_WITHDRAWN", AuditLog.consent_id == consent.id
        ).count() == 1


def test_a_purpose_that_does_not_rest_on_consent_is_not_denied(db, client, cookie_purposes):
    """The regression this change could have introduced, pinned.

    `_sync_consent_preferences` INFERS a refusal from a category map:
    `categories.get(key, False)` reads an absent or false `necessary` as "no"
    for the seeded `strictly_necessary` purpose - s.7(a), requires_consent
    False, the login/session-security/consent-record purpose the CRM banner
    itself renders as a locked, always-on toggle. Recording DENIED there would
    make `evaluate_decision` answer DENY for it, because it reads the consent
    status before it ever reaches `purpose.requires_consent is False -> ALLOW`.
    A reject-all would switch off the service rather than protect anybody.

    Same predicate, same boundary, same reason as GPC enforcement's
    (`app/services/gpc.py::purpose_is_consent_based`)."""
    from app.services.decision_engine import evaluate_decision

    email = "crm-reject-necessary@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, REJECT_ALL)

    necessary = cookie_purposes["necessary"]
    rows = [c for c in _consents(db, email) if c.purpose_id == necessary.id]
    assert rows, "the banner must still materialise the strictly-necessary rows"
    assert {c.status for c in rows} == {"NOT_REQUESTED"}
    for consent in rows:
        assert _evidence(db, consent) == []
        decision = evaluate_decision(
            db, consent.customer, consent.purpose, consent.data_category,
            consent.processing_activity, source_app=consent.source_app,
        )
        assert decision.decision == "ALLOW", (
            "strictly necessary processing must survive a reject-all"
        )


def test_a_second_reject_all_does_not_double_record(db, client, cookie_purposes):
    """The banner re-sends every category on every save. A DENIED row must not
    be re-denied - `CONSENT_TRANSITIONS` has no DENIED -> DENIED edge, and a
    second identical entry would inflate the ledger with an act that did not
    happen."""
    email = "crm-reject-twice@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, REJECT_ALL)
    first = {c.id: (c.status, c.consent_version) for c in _consent_based_consents(db, email)}

    assert _banner(client, visitor, REJECT_ALL).status_code == 200

    for consent in _consent_based_consents(db, email):
        assert consent.status == "DENIED"
        assert (consent.status, consent.consent_version) == first[consent.id]
        assert db.query(ConsentHistory).filter(
            ConsentHistory.consent_id == consent.id,
            ConsentHistory.action == "CONSENT_DENIED",
        ).count() == 1
        assert len(_evidence(db, consent)) == 1


def test_a_refusal_does_not_overwrite_the_principals_own_withdrawal(db, client, cookie_purposes):
    """Her withdrawal is her act. A later reject-all may not restate it as a
    system denial - and WITHDRAWN -> DENIED is not a legal edge anyway."""
    email = "crm-withdrawn-then-reject@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, ACCEPT_ALL)
    _banner(client, visitor, REJECT_ALL)
    assert {c.status for c in _consents(db, email)} == {"WITHDRAWN"}
    before = {c.id: len(_evidence(db, c)) for c in _consents(db, email)}

    _banner(client, visitor, REJECT_ALL)

    for consent in _consents(db, email):
        assert consent.status == "WITHDRAWN"
        assert len(_evidence(db, consent)) == before[consent.id]


# =========================================================================== #
#  The GPC choke point is not bypassed
# =========================================================================== #
def test_the_server_observed_gpc_header_is_stamped_on_a_refusal(db, client, cookie_purposes):
    """A refusal's evidence carries the server's own read of `Sec-GPC`, for the
    same reason a grant's does - and never the client's claim."""
    email = "crm-reject-gpc@example.com"
    visitor = _visitor(db, email)

    _banner(client, visitor, REJECT_ALL, headers={"Sec-GPC": "1"},
            context={**BANNER_CONTEXT, "gpc_signal": False})

    for consent in _consent_based_consents(db, email):
        row = _evidence(db, consent)[-1]
        assert row.gpc_signal is True, "the observed header, not the body's claim"
        assert row.details["claimed_gpc_signal"] is False


def test_gpc_enforcement_still_runs_on_the_grant_path(db, client, cookie_purposes):
    """Guard against the refusal change quietly routing around the R2-11/Q-07
    choke point: an ACCEPT under a server-observed objection must still be
    recorded as DENIED, with its GPC_OBJECTION_ENFORCED audit row."""
    email = "crm-accept-under-gpc@example.com"
    visitor = _visitor(db, email)

    assert _banner(client, visitor, ACCEPT_ALL, headers={"Sec-GPC": "1"}).status_code == 200

    consent_based = [c for c in _consents(db, email) if c.purpose.requires_consent]
    assert consent_based
    assert {c.status for c in consent_based} == {"DENIED"}
    for consent in consent_based:
        assert db.query(AuditLog).filter(
            AuditLog.event == "GPC_OBJECTION_ENFORCED", AuditLog.consent_id == consent.id
        ).count() == 1


# =========================================================================== #
#  One definition of the rule, not one per route
# =========================================================================== #
def test_the_deniable_statuses_come_from_the_state_machine():
    from app.models.entities import CONSENT_TRANSITIONS
    from app.services import consent as consent_service

    assert set(consent_service.DENIABLE_STATUSES) == {
        s for s, allowed in CONSENT_TRANSITIONS.items() if "DENIED" in allowed
    }
    assert set(consent_service.WITHDRAWABLE_STATUSES) == {
        s for s, allowed in CONSENT_TRANSITIONS.items() if "WITHDRAWN" in allowed
    }
    assert not set(consent_service.DENIABLE_STATUSES) & set(consent_service.WITHDRAWABLE_STATUSES)


def test_the_portal_and_the_crm_banner_share_one_definition():
    """`/portal/deny` and `_sync_consent_preferences` must not each carry their
    own copy of "which statuses" - a copy per route is exactly how three of the
    four demo sites ended up recording no refusal at all."""
    from app.api.routes import portal
    from app.services import consent as consent_service

    assert portal.DENIABLE_STATUSES is consent_service.DENIABLE_STATUSES
