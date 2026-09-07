"""R2-07: banner-event instrumentation and the compliance KPI dashboard.

The definition of done this file is written against:

  "Dashboard shows the KPI catalogue with drill-down; at least the eight
   derivable KPIs live in Phase 1, the rest as their data arrives."

  * whole catalogue        -> test_catalogue_covers_every_kpi_in_the_register
  * drill-down             -> test_drilldown_breaks_a_kpi_down_by_purpose,
                              test_trend_returns_a_series_for_a_supported_kpi
  * at least eight live    -> test_at_least_eight_kpis_are_live_on_seeded_data
  * "as their data arrives" -> test_unknown_is_never_rendered_as_zero,
                              test_every_unavailable_kpi_names_what_would_unblock_it,
                              test_kpis_whose_owning_module_exists_are_wired_to_it

That last one is the guard against the failure mode this project has already
hit once, in the evidence pack: a section that kept reporting UNAVAILABLE
after the register behind it had been built. It pins the exact set of KPIs
that are allowed to have no computation, so wiring up a new module without
wiring up its KPI fails here rather than quietly under-reporting forever.

Emission from the demo sites (the other half of R2-07) is exercised through
the same public endpoint the banners call.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.encryption import hmac_digest
from app.models.analytics import BannerEvent
from app.models.entities import (
    Consent,
    Customer,
    DataCategory,
    Organization,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    Role,
    User,
)
from app.services import banner_events as banner_service
from app.services import kpi_catalogue

TENANT = "KPILANE"


@pytest.fixture(scope="module", autouse=True)
def _mount_routers():
    """Mount this lane's routers if app/main.py has not yet been updated to
    include them (that file belongs to another lane). A no-op once it has."""
    from app.api.routes import analytics
    from app.main import app

    if not any(getattr(r, "path", "").startswith("/analytics") for r in app.routes):
        app.include_router(analytics.router)
    if not any("banner-events" in getattr(r, "path", "") for r in app.routes):
        app.include_router(analytics.public_router)


@pytest.fixture()
def tenant(db):
    org = db.query(Organization).filter(Organization.code == TENANT).first()
    if not org:
        org = Organization(name="KPI Lane", code=TENANT, is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


@pytest.fixture()
def auth(staff_token):
    return {"Authorization": f"Bearer {staff_token}"}


def _clear_events(db, tenant_id):
    db.query(BannerEvent).filter(BannerEvent.tenant_id == tenant_id).delete()
    db.commit()


def _beacon(client, *, event_type="NOTICE_SHOWN", session_id="sess-aaaaaaaa", **kwargs):
    payload = {"event_type": event_type, "session_id": session_id, **kwargs}
    return client.post(f"/public/{TENANT}/banner-events", json=payload)


# --------------------------------------------------------------------------- #
#  Emission: the write path the demo banners use
# --------------------------------------------------------------------------- #
def test_a_banner_impression_is_recorded_without_authentication(client, db, tenant):
    """A cookie banner is painted before anyone logs in, so the beacon cannot
    carry a credential."""
    _clear_events(db, tenant.id)
    response = _beacon(
        client, session_id="sess-impression-1", banner_version="crm-cookie-banner-v1",
        language="ta", purposes_offered=["ANALYTICS", "ADVERTISING"],
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"recorded": True, "event_type": "NOTICE_SHOWN"}

    row = db.query(BannerEvent).filter(BannerEvent.tenant_id == tenant.id).one()
    assert row.event_type == "NOTICE_SHOWN"
    assert row.language == "ta"
    assert row.purposes_offered == ["ADVERTISING", "ANALYTICS"]


def test_the_session_nonce_is_stored_only_as_a_hash(client, db, tenant):
    """The impression -> decision join has to work, but the value on disk must
    not be correlatable with any nonce held elsewhere."""
    _clear_events(db, tenant.id)
    _beacon(client, session_id="sess-secret-value")
    row = db.query(BannerEvent).filter(BannerEvent.tenant_id == tenant.id).one()
    assert row.session_ref != "sess-secret-value"
    assert row.session_ref == banner_service.hash_session("sess-secret-value")
    assert len(row.session_ref) == 64


def test_no_column_on_the_event_table_can_hold_personal_data(db):
    """A structural check, not a behavioural one: this table is written by an
    open endpoint on nearly every page view, so the defence has to be that
    there is nowhere to put an identifier at all."""
    forbidden = {"ip_address", "user_agent", "email", "customer_id", "external_id",
                 "recipient", "phone", "consent_text", "reason", "details"}
    columns = {c.name for c in BannerEvent.__table__.columns}
    assert not (columns & forbidden), f"banner_events gained a PII-capable column: {columns & forbidden}"


def test_the_recorded_path_is_redacted_and_the_query_string_dropped(client, db, tenant):
    _clear_events(db, tenant.id)
    _beacon(
        client, session_id="sess-path-1",
        page_path="/users/4821/profile?email=ravi@example.com&utm=x",
    )
    row = db.query(BannerEvent).filter(BannerEvent.tenant_id == tenant.id).one()
    assert "ravi@example.com" not in (row.page_ref or "")
    assert "?" not in (row.page_ref or "")
    assert row.page_ref == "/users/{id}/profile"


def test_an_unknown_tenant_is_refused_and_never_provisioned(client, db):
    """`resolve_tenant_id` auto-creates a tenant on first sight of an unknown
    source_app, which would let anyone with curl fill the organizations table
    through this open endpoint."""
    before = db.query(Organization).count()
    response = _beacon(client, session_id="sess-unknown-tenant")
    response = client.post(
        "/public/NOT_A_REAL_TENANT/banner-events",
        json={"event_type": "NOTICE_SHOWN", "session_id": "sess-unknown-tenant"},
    )
    assert response.status_code == 404
    assert db.query(Organization).count() == before


@pytest.mark.parametrize(
    "payload, why",
    [
        ({"event_type": "NOTICE_SHOWN", "session_id": "sess-x1", "decision": "ACCEPT_ALL"},
         "an impression is not a decision"),
        ({"event_type": "DECISION", "session_id": "sess-x2"},
         "a decision must say which decision"),
        ({"event_type": "DECISION", "session_id": "sess-x3", "decision": "GRANULAR",
          "purposes_offered": ["A"], "purposes_granted": ["A", "B"]},
         "granting a purpose that was never offered would put K-01's numerator above its denominator"),
        ({"event_type": "NOTICE_SHOWN", "session_id": "sess-x4",
          "purposes_offered": ["ravi@example.com"]},
         "purpose codes are codes, never free text"),
        ({"event_type": "NOTICE_SHOWN", "session_id": "short"},
         "the session nonce must actually be a nonce"),
    ],
)
def test_malformed_beacons_are_refused(client, tenant, payload, why):
    response = client.post(f"/public/{TENANT}/banner-events", json=payload)
    assert response.status_code == 422, f"{why}: {response.status_code} {response.text}"


def test_time_to_decision_is_measured_between_two_server_stamped_rows(client, db, tenant):
    _clear_events(db, tenant.id)
    _beacon(client, session_id="sess-timed", purposes_offered=["ANALYTICS"])
    shown = (
        db.query(BannerEvent)
        .filter(BannerEvent.tenant_id == tenant.id, BannerEvent.event_type == "NOTICE_SHOWN")
        .one()
    )
    shown.occurred_at = datetime.now(timezone.utc) - timedelta(seconds=9)
    db.commit()

    _beacon(client, event_type="DECISION", session_id="sess-timed", decision="ACCEPT_ALL",
            purposes_offered=["ANALYTICS"], purposes_granted=["ANALYTICS"])
    decision = (
        db.query(BannerEvent)
        .filter(BannerEvent.tenant_id == tenant.id, BannerEvent.event_type == "DECISION")
        .one()
    )
    assert decision.time_to_decision_ms is not None
    assert 8_000 <= decision.time_to_decision_ms <= 11_000


def test_a_decision_with_no_impression_records_no_duration(client, db, tenant):
    """Rather than guessing one. K-04 then reports a smaller sample."""
    _clear_events(db, tenant.id)
    _beacon(client, event_type="DECISION", session_id="sess-orphan-decision",
            decision="REJECT_ALL", purposes_offered=["ANALYTICS"])
    row = db.query(BannerEvent).filter(BannerEvent.tenant_id == tenant.id).one()
    assert row.time_to_decision_ms is None


# --------------------------------------------------------------------------- #
#  The funnel maths
# --------------------------------------------------------------------------- #
def _seed_funnel(client, db, tenant):
    """Five sessions, one of each shape, so every ratio below has a known
    answer rather than a plausible one."""
    _clear_events(db, tenant.id)
    offered = ["ANALYTICS", "ADVERTISING", "FUNCTIONAL"]
    shapes = [
        ("s-accept", "ACCEPT_ALL", offered),
        ("s-reject", "REJECT_ALL", []),
        ("s-granular", "GRANULAR", ["ANALYTICS"]),
        ("s-dismiss", "DISMISSED", []),
    ]
    for session_id, decision, granted in shapes:
        _beacon(client, session_id=session_id, purposes_offered=offered)
        _beacon(client, event_type="DECISION", session_id=session_id, decision=decision,
                purposes_offered=offered, purposes_granted=granted)
    # A fifth session that was shown the banner and never answered it.
    _beacon(client, session_id="s-ignored", purposes_offered=offered)
    db.commit()


def test_the_funnel_counts_sessions_and_derives_the_ignore_rate(client, db, tenant):
    _seed_funnel(client, db, tenant)
    now = datetime.now(timezone.utc)
    f = banner_service.funnel(
        db, tenant_id=tenant.id, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    assert f["impression_sessions"] == 5
    assert f["decision_sessions"] == 4
    assert f["no_choice_sessions"] == 1, "the session that was shown a banner and never answered"
    assert f["mix"] == {"ACCEPT_ALL": 1, "REJECT_ALL": 1, "GRANULAR": 1, "DISMISSED": 1}
    assert f["partial_sessions"] == 1, "only the session that granted a strict subset"


def test_changing_your_mind_does_not_double_count_the_session(client, db, tenant):
    """A principal who accepts and later rejects in the preference centre is
    one session with one first decision - otherwise K-02's ignore rate can go
    negative."""
    _seed_funnel(client, db, tenant)
    _beacon(client, event_type="DECISION", session_id="s-accept", decision="REJECT_ALL",
            surface="PREFERENCE_CENTRE", purposes_offered=["ANALYTICS"])
    now = datetime.now(timezone.utc)
    f = banner_service.funnel(
        db, tenant_id=tenant.id, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    assert f["decision_sessions"] == 4
    assert f["subsequent_decisions"] == 1
    assert f["mix"]["ACCEPT_ALL"] == 1, "the session's FIRST decision still counts"
    assert f["no_choice_sessions"] == 1


def test_rejecting_everything_optional_is_not_partial_consent(client, db, tenant):
    """The seeded funnel has one reject-all, which still leaves the
    strictly-necessary category on and so grants a strict *subset* of what was
    offered. Counting that as partial consent overstated K-03 by more than
    double on realistic traffic, in the direction that flatters the banner:
    people who refused everything they were allowed to refuse were reported as
    people who made a nuanced, purpose-by-purpose choice."""
    _seed_funnel(client, db, tenant)
    now = datetime.now(timezone.utc)
    f = banner_service.funnel(
        db, tenant_id=tenant.id, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    assert f["mix"]["REJECT_ALL"] == 1
    assert f["partial_sessions"] == 1, "the granular session only, not the reject-all"


def test_a_granular_decision_that_grants_everything_is_not_partial(client, db, tenant):
    """Opening the toggles and ticking them all is a granular *control* (K-47)
    but a full consent (K-03)."""
    _clear_events(db, tenant.id)
    offered = ["ANALYTICS", "ADVERTISING"]
    _beacon(client, session_id="s-granular-all", purposes_offered=offered)
    _beacon(client, event_type="DECISION", session_id="s-granular-all", decision="GRANULAR",
            purposes_offered=offered, purposes_granted=offered)
    now = datetime.now(timezone.utc)
    f = banner_service.funnel(
        db, tenant_id=tenant.id, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    assert f["mix"]["GRANULAR"] == 1
    assert f["partial_sessions"] == 0


def test_optin_by_purpose_uses_distinct_sessions(client, db, tenant):
    _seed_funnel(client, db, tenant)
    now = datetime.now(timezone.utc)
    rates = banner_service.optin_by_purpose(
        db, tenant_id=tenant.id, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    assert rates["ANALYTICS"]["offered_sessions"] == 5
    assert rates["ANALYTICS"]["granted_sessions"] == 2, "accept-all and the granular subset"
    assert rates["ADVERTISING"]["granted_sessions"] == 1, "accept-all only"


# --------------------------------------------------------------------------- #
#  The catalogue
# --------------------------------------------------------------------------- #
def test_catalogue_covers_every_kpi_in_the_register(client, auth):
    response = client.get("/analytics/kpis", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    ids = [k["id"] for k in body["kpis"]]
    assert ids == [f"K-{n:02d}" for n in range(1, 50)], "the K-series, in order, with no gaps"
    assert body["counts"]["total"] == 49
    assert sum(body["counts"][k] for k in ("live", "no_data", "partial", "unavailable")) == 49


def test_every_kpi_carries_a_status_and_a_display_string(client, auth):
    body = client.get("/analytics/kpis", headers=auth).json()
    for kpi in body["kpis"]:
        assert kpi["status"] in {"LIVE", "NO_DATA", "PARTIAL", "UNAVAILABLE"}, kpi["id"]
        assert kpi["display"], kpi["id"]
        assert kpi["statutory_ref"], kpi["id"]
        assert kpi["formula"], kpi["id"]


def test_unknown_is_never_rendered_as_zero(client, auth):
    """The whole point. A KPI with no value must not present one, and must
    not present a dash that a reader could mistake for 0."""
    body = client.get("/analytics/kpis", headers=auth).json()
    for kpi in body["kpis"]:
        if kpi["status"] in {"NO_DATA", "UNAVAILABLE"}:
            assert kpi["value"] is None, f"{kpi['id']} claims a value it cannot have"
            assert kpi["display"] == "—", f"{kpi['id']} renders a number for an unknown"
            assert kpi["reason"], f"{kpi['id']} gives no reason for having no value"
        else:
            assert kpi["value"] is not None, f"{kpi['id']} is {kpi['status']} with no value"


def test_a_percentage_never_contradicts_the_ratio_shown_beside_it(client, auth):
    """The tile prints the value on one line and "n of m" on the next, so a
    KPI whose percentage is computed over a different denominator than the one
    it reports prints two numbers that cannot both be true. K-08 did exactly
    that - "100.0%" above "0 of 8" - because its percentage is met / decided
    while the tile was handed met / all-withdrawals."""
    body = client.get("/analytics/kpis", headers=auth).json()
    for kpi in body["kpis"]:
        if kpi["status"] not in {"LIVE", "PARTIAL"} or kpi["unit"] != "percent":
            continue
        if kpi["numerator"] is None or not kpi["denominator"]:
            continue
        implied = round(kpi["numerator"] / kpi["denominator"] * 100, 2)
        assert abs(implied - kpi["value"]) < 0.5, (
            f"{kpi['id']} displays {kpi['value']}% but its stated ratio "
            f"{kpi['numerator']}/{kpi['denominator']} implies {implied}%"
        )


#: Every upstream percentage that hard-codes a value for an empty set, with
#: the KPI it feeds. `x if n else 100.0` is a reasonable default for an
#: alerting threshold - no data, no alarm - and an affirmative false assurance
#: on a compliance dashboard: it asserts full compliance with a control that
#: never ran. `... else 0.0` is the same defect pointing the other way.
#:
#: Audited across the whole catalogue after K-08 was caught printing "100.0%"
#: for eight withdrawals that reached no processor at all. The defects are the
#: owning modules'; this dashboard must not surface them, and the invariant
#: below is how it proves it does not. `app/services/breach.py` (K-39..K-41)
#: and `app/services/grievance.py` (K-23) return None for an empty set and are
#: deliberately not in this list - they got it right.
_FALSE_DEFAULT_UPSTREAMS = {
    "K-08": "app/services/processors.py::propagation_sla_metrics (else 100.0 on an undecided SLA)",
    "K-10": "app/api/routes/purposes.py::coverage_report (else 100.0 with zero activities)",
    "K-18": "app/services/kpi.py::evidence_completeness (else 100.0 with zero consents)",
    "K-30": "app/services/erasure.py::erasure_metrics (else 100.0 with zero executions)",
    "K-32": "app/services/processors.py::contract_coverage_report (else 100.0 with zero processors)",
    "K-42": "app/api/routes/consent_manager.py::metrics (else 100.0 with zero calls)",
    "K-44": "app/services/kpi.py::notification_delivery_metrics (else 100.0 with zero attempts)",
    "K-45": "app/services/kpi.py::notification_delivery_metrics (else 0.0 with zero delivered)",
}


@pytest.mark.parametrize("kpi_id", sorted(_FALSE_DEFAULT_UPSTREAMS))
def test_an_empty_set_is_never_republished_as_full_compliance(client, auth, kpi_id):
    """A figure only survives to the dashboard with a real denominator behind
    it. Where the sample is empty the KPI reports its status honestly and
    carries no number, whatever the owning module chose to return."""
    body = client.get("/analytics/kpis", headers=auth).json()
    kpi = next(k for k in body["kpis"] if k["id"] == kpi_id)
    where = _FALSE_DEFAULT_UPSTREAMS[kpi_id]
    if kpi["status"] in {"LIVE", "PARTIAL"}:
        assert kpi["denominator"], (
            f"{kpi_id} carries a figure with no denominator - it is republishing {where}"
        )
        assert kpi["value"] is not None
    else:
        assert kpi["value"] is None, f"{kpi_id} carries a value with no sample ({where})"
        assert kpi["reason"], f"{kpi_id} withholds a value without saying why"


def test_the_guard_is_on_the_denominator_the_percentage_actually_uses():
    """The K-08 bug in one line. Its guard was on `withdrawals_total`, a
    plausible-looking sibling count, while the percentage is computed over
    `fully_acknowledged_within_sla + sla_breached`. Eight withdrawals existed,
    so the guard passed; none had a decided outcome, so the module returned
    its 100.0 default; the tile printed "100.0%" above "0 of 8"."""
    import inspect

    from app.services import kpi_catalogue

    source = inspect.getsource(kpi_catalogue.k08_propagation_sla)
    assert "sla_breached" in source, (
        "K-08 must gate on the decided set (met + breached), not on the total number of "
        "withdrawals - see this test's docstring"
    )


def test_the_honesty_note_uses_the_words_the_dashboard_shows(client, auth):
    """The band explaining the four states is the legend that makes "zero" and
    "unknown" tellable apart at a glance. It quoted the API's own enum names
    (NO_DATA, UNAVAILABLE) while the tiles were labelled "No data yet" and
    "Not instrumented", so a reader had to translate between two vocabularies
    to use the one thing on the page whose whole job is to prevent a
    misreading."""
    note = client.get("/analytics/kpis", headers=auth).json()["honesty_note"]
    for label in ("Live", "Partial", "No data yet", "Not instrumented"):
        assert label in note, f"the honesty note never uses the on-screen label {label!r}"
    for enum_name in ("NO_DATA", "UNAVAILABLE", "LIVE", "PARTIAL"):
        assert enum_name not in note, f"the honesty note still shows the raw enum {enum_name!r}"


def test_every_unavailable_kpi_names_what_would_unblock_it(client, auth):
    body = client.get("/analytics/kpis", headers=auth).json()
    for kpi in body["kpis"]:
        if kpi["status"] == "UNAVAILABLE":
            assert kpi["unblocked_by"], f"{kpi['id']} is unavailable with no stated blocker"


def test_every_partial_kpi_states_what_it_leaves_out(client, auth):
    body = client.get("/analytics/kpis", headers=auth).json()
    for kpi in body["kpis"]:
        if kpi["status"] == "PARTIAL":
            assert kpi["coverage"], f"{kpi['id']} is partial without saying what it omits"


def test_kpis_whose_owning_module_exists_are_wired_to_it():
    """The reverse-of-UNAVAILABLE guard.

    This is the exact bug the evidence pack hit: a section kept saying
    UNAVAILABLE after the register behind it was built. Pinning the set of
    KPIs that legitimately have no computation means the next lane to land a
    module has to come here and remove its KPI from this list, rather than
    leaving the dashboard permanently under-reporting.

    Removing an id from this set is the *only* correct way to make this test
    fail-then-pass. Adding one requires deleting a working computation, which
    should never be an accident.
    """
    expected_uncomputed = {
        "K-07",  # no UI step instrumentation
        "K-37", "K-38",  # no backup/drill or vulnerability register (H-06, H-09)
        "K-46",  # cross-tenant refusals are indistinguishable from "not found"
        "K-48",  # client-side tag scanner has no server sink
        "K-49",  # no consent-sync push telemetry (O-03)
    }
    actual = {spec.id for spec in kpi_catalogue.CATALOGUE if spec.compute is None}
    assert actual == expected_uncomputed, (
        "A KPI's data source has appeared or disappeared. If a module was just built, wire its "
        "KPI up and remove its id here; the dashboard must not keep reporting UNAVAILABLE for "
        "something the platform can now answer."
    )


#: The module each still-unanswerable KPI is waiting on. When one of these
#: files appears, the KPIs beside it stop being unanswerable, and the test
#: below turns red until somebody wires them up. This is the tripwire the
#: evidence pack did not have.
_MODULE_WATCH = {
    "app/services/erasure.py": {"K-28", "K-29", "K-30", "K-31"},
    "app/services/material_change.py": {"K-09"},
    "app/services/legacy_notice.py": {"K-26"},
    "app/services/rights_requests.py": {"K-20", "K-21", "K-22"},
    # R1-14 shipped as guardian.py, not age_assurance.py. The watch named a
    # file that never appeared, so it could not have fired when the module
    # landed - a tripwire pointed at the wrong filename is not a tripwire.
    "app/services/guardian.py": {"K-24", "K-25"},
    "app/services/vulnerabilities.py": {"K-38"},
}


def test_a_kpi_stops_being_unavailable_the_moment_its_module_lands():
    """The evidence pack once kept reporting UNAVAILABLE for a register that
    had been built. Comparing a static list against itself would never have
    caught that; this looks at the filesystem instead."""
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    uncomputed = {spec.id for spec in kpi_catalogue.CATALOGUE if spec.compute is None}
    for module, kpi_ids in _MODULE_WATCH.items():
        if not (backend / module).exists():
            continue
        still_unwired = kpi_ids & uncomputed
        assert not still_unwired, (
            f"{module} exists, so {sorted(still_unwired)} can now be answered, but the KPI "
            f"catalogue still reports them UNAVAILABLE. Aggregate that module's own metrics "
            f"function — do not recompute it here."
        )


def test_every_computed_kpi_names_the_module_that_owns_it():
    """`computed_by` is the receipt for 'this dashboard aggregates, it does
    not recompute'. A KPI with a computation and no attribution is one nobody
    can check for divergence against the screen that owns it."""
    for spec in kpi_catalogue.CATALOGUE:
        if spec.compute is not None:
            assert spec.computed_by, f"{spec.id} computes a value but names no owning module"


# --------------------------------------------------------------------------- #
#  Live KPIs on real data
# --------------------------------------------------------------------------- #
@pytest.fixture()
def seeded_consents(db, tenant):
    """One purpose, three principals: granted, denied, withdrawn."""
    category = db.query(DataCategory).first()
    if not category:
        category = DataCategory(code="CONTACT", name="Contact details")
        db.add(category)
        db.flush()
    activity = db.query(ProcessingActivity).first()
    if not activity:
        activity = ProcessingActivity(code="MARKETING", name="Marketing")
        db.add(activity)
        db.flush()
    purpose = db.query(Purpose).filter(Purpose.code == "KPI_MARKETING").first()
    if not purpose:
        purpose = Purpose(code="KPI_MARKETING", name="KPI marketing", is_active=True,
                          requires_consent=True, tenant_id=tenant.id)
        db.add(purpose)
        db.flush()
        version = PurposeVersion(purpose_id=purpose.id, version_number=1, is_current=True,
                                 name=purpose.name, requires_consent=True)
        db.add(version)
        db.flush()
    version = (
        db.query(PurposeVersion)
        .filter(PurposeVersion.purpose_id == purpose.id, PurposeVersion.is_current.is_(True))
        .first()
    )

    now = datetime.now(timezone.utc)
    for index, status in enumerate(("GRANTED", "DENIED", "WITHDRAWN")):
        external_id = f"kpi-principal-{index}"
        # `name`/`external_id` are encrypted columns; equality only works
        # through the HMAC search companion (see docs/ARCHITECTURE.md).
        customer = (
            db.query(Customer)
            .filter(
                Customer.source_app == TENANT,
                Customer.external_id_search == hmac_digest(external_id),
            )
            .first()
        )
        if not customer:
            customer = Customer(
                name=external_id, external_id=external_id, email=f"{external_id}@example.com",
                source_app=TENANT, tenant_id=tenant.id,
            )
            db.add(customer)
            db.flush()
        existing = (
            db.query(Consent)
            .filter(Consent.customer_id == customer.id, Consent.purpose_id == purpose.id)
            .first()
        )
        if not existing:
            db.add(Consent(
                customer_id=customer.id, purpose_id=purpose.id, purpose_version_id=version.id,
                data_category_id=category.id, processing_activity_id=activity.id,
                status=status, source_app=TENANT, tenant_id=tenant.id,
                granted_at=now if status == "GRANTED" else None,
                denied_at=now if status == "DENIED" else None,
                withdrawn_at=now if status == "WITHDRAWN" else None,
                expires_at=now + timedelta(days=200) if status == "GRANTED" else None,
            ))
    db.commit()
    return purpose


def test_at_least_eight_kpis_are_live_on_seeded_data(client, db, auth, tenant, seeded_consents):
    """The DoD's number. LIVE or PARTIAL both carry a real figure; NO_DATA and
    UNAVAILABLE do not, and are not counted here."""
    _seed_funnel(client, db, tenant)
    body = client.get("/analytics/kpis?period_days=30", headers=auth).json()
    answered = [k for k in body["kpis"] if k["status"] in {"LIVE", "PARTIAL"}]
    assert len(answered) >= 8, (
        f"only {len(answered)} KPIs carry a figure: "
        f"{[(k['id'], k['status']) for k in body['kpis']]}"
    )
    for kpi in answered:
        assert kpi["value"] is not None


def test_the_five_banner_kpis_go_live_once_events_arrive(client, db, auth, tenant):
    """K-01's impression half, K-02, K-03, K-04 and K-47 are the register's
    'data not captured' rows; this is the instrumentation that captures it."""
    _seed_funnel(client, db, tenant)
    body = client.get(f"/analytics/kpis?period_days=1&source_app={TENANT}", headers=auth).json()
    by_id = {k["id"]: k for k in body["kpis"]}

    assert by_id["K-02"]["status"] == "LIVE"
    assert by_id["K-02"]["value"] == 25.0, "one reject-all out of four decisions"
    assert by_id["K-02"]["detail"]["no_choice_rate_pct"] == 20.0, "one ignored out of five impressions"
    assert by_id["K-03"]["status"] == "LIVE"
    assert by_id["K-03"]["value"] == 25.0, "one strict subset out of four decisions"
    assert by_id["K-47"]["status"] == "LIVE"
    assert by_id["K-47"]["value"] == 25.0, "one granular choice out of four decisions"
    assert by_id["K-04"]["status"] == "LIVE"
    assert by_id["K-04"]["detail"]["samples"] == 4


def test_no_banner_events_reads_as_unknown_not_as_a_zero_percent_optin(client, db, auth, tenant):
    _clear_events(db, tenant.id)
    body = client.get(f"/analytics/kpis?period_days=1&source_app={TENANT}", headers=auth).json()
    by_id = {k["id"]: k for k in body["kpis"]}
    for kpi_id in ("K-02", "K-03", "K-04", "K-47"):
        assert by_id[kpi_id]["status"] == "NO_DATA", kpi_id
        assert by_id[kpi_id]["value"] is None, kpi_id
        assert by_id[kpi_id]["display"] == "—", kpi_id


# --------------------------------------------------------------------------- #
#  Drill-down and trend
# --------------------------------------------------------------------------- #
def test_consent_coverage_cannot_exceed_one_hundred_percent(client, auth, seeded_consents):
    """A consent row is per principal x purpose x data category x processing
    activity x source_app, so one covered (principal, purpose) pair spans
    several rows. Counting rows against a denominator of principals x purposes
    compared two different units and printed 791% coverage on ordinary seed
    data - a ratio that cannot exceed 100% by construction."""
    kpi = client.get("/analytics/kpis/K-11", headers=auth).json()
    assert kpi["status"] == "LIVE", f"the seeded fixture should make K-11 live: {kpi['reason']}"
    assert kpi["value"] <= 100.0, kpi["detail"]
    assert kpi["numerator"] <= kpi["denominator"]


def test_no_percentage_kpi_can_exceed_one_hundred_percent(client, auth, seeded_consents):
    """The generalisation of the K-11 bug. A share of something cannot be more
    than all of it, and a dashboard that prints 791% - or 104% - is not merely
    inaccurate, it is visibly not measuring what it claims to."""
    body = client.get("/analytics/kpis", headers=auth).json()
    for kpi in body["kpis"]:
        if kpi["unit"] != "percent" or kpi["value"] is None:
            continue
        assert kpi["value"] <= 100.0, (
            f"{kpi['id']} reports {kpi['value']}% - a share cannot exceed the whole. "
            f"detail={kpi['detail']}"
        )


def test_drilldown_breaks_a_kpi_down_by_purpose(client, auth, seeded_consents):
    response = client.get("/analytics/kpis/K-01", headers=auth)
    assert response.status_code == 200, response.text
    kpi = response.json()
    assert kpi["supports_breakdown"] is True
    labels = [row["label"] for row in kpi["breakdown"]]
    assert any("KPI_MARKETING" in label for label in labels), labels
    row = next(r for r in kpi["breakdown"] if "KPI_MARKETING" in r["label"])
    assert row["numerator"] == 1, "one granted"
    assert row["denominator"] == 3, "one granted, one denied, one withdrawn"


def test_drilldown_of_an_unknown_kpi_is_a_404(client, auth):
    assert client.get("/analytics/kpis/K-99", headers=auth).status_code == 404
    assert client.get("/analytics/kpis/K-99/trend", headers=auth).status_code == 404


def test_trend_returns_a_series_for_a_supported_kpi(client, db, auth, tenant):
    _seed_funnel(client, db, tenant)
    response = client.get(
        f"/analytics/kpis/K-47/trend?period_days=7&source_app={TENANT}", headers=auth
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "LIVE"
    assert body["points"], "a period containing four decisions must plot at least one bucket"
    assert all("period" in point for point in body["points"])


def test_a_point_in_time_kpi_says_it_has_no_series_rather_than_faking_one(client, auth):
    body = client.get("/analytics/kpis/K-32/trend", headers=auth).json()
    assert body["status"] == "UNAVAILABLE"
    assert "point-in-time" in body["reason"]
    assert body["points"] == []


# --------------------------------------------------------------------------- #
#  Tenant scoping
# --------------------------------------------------------------------------- #
def _org_scoped_headers(db):
    from app.core.rbac import ROLE_PERMISSIONS
    from app.core.security import create_access_token, hash_password

    role = db.query(Role).filter(Role.name == "codex_admin").first()
    if not role:
        role = Role(name="codex_admin", description="Codex admin",
                    permissions=list(ROLE_PERMISSIONS["codex_admin"]), is_system=True)
        db.add(role)
        db.flush()
    user = db.query(User).filter(User.username == "kpi-codex-admin").first()
    if not user:
        user = User(
            username="kpi-codex-admin", full_name="Codex Admin",
            email="kpi-codex-admin@example.com",
            email_search=hmac_digest("kpi-codex-admin@example.com"),
            password_hash=hash_password("Test@1234"), role_id=role.id, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    db.commit()
    token = create_access_token(user.id, user.username, role.name)
    return {"Authorization": f"Bearer {token}"}


def test_an_org_scoped_role_cannot_widen_its_own_scope(client, db, tenant):
    org = db.query(Organization).filter(Organization.code == "CODEX").first()
    if not org:
        db.add(Organization(name="Codex", code="CODEX", is_active=True))
        db.commit()
    headers = _org_scoped_headers(db)
    body = client.get(f"/analytics/kpis?source_app={TENANT}", headers=headers).json()
    assert body["scope"] == "CODEX", "the role's own scope wins over the query parameter"
    assert body["scope_locked"] is True


def test_a_platform_wide_kpi_is_withheld_from_a_tenant_scoped_role(client, db, tenant):
    """Rather than shown, and rather than recomputed a second, divergent way.
    K-18/K-42/K-44 are computed platform-wide by the modules that own them."""
    org = db.query(Organization).filter(Organization.code == "CODEX").first()
    if not org:
        db.add(Organization(name="Codex", code="CODEX", is_active=True))
        db.commit()
    headers = _org_scoped_headers(db)
    body = client.get("/analytics/kpis", headers=headers).json()
    by_id = {k["id"]: k for k in body["kpis"]}
    for kpi_id in ("K-18", "K-42", "K-44", "K-45", "K-10", "K-33"):
        assert by_id[kpi_id]["status"] == "UNAVAILABLE", kpi_id
        assert by_id[kpi_id]["value"] is None, kpi_id
        assert "tenant" in by_id[kpi_id]["reason"].lower(), kpi_id


def test_an_unscoped_role_still_sees_the_platform_wide_kpis(client, auth):
    body = client.get("/analytics/kpis", headers=auth).json()
    by_id = {k["id"]: k for k in body["kpis"]}
    assert by_id["K-34"]["status"] == "PARTIAL", "encryption coverage is answerable platform-wide"
    assert by_id["K-19"]["status"] in {"LIVE", "NO_DATA"}


def test_the_catalogue_requires_the_dashboard_permission(client):
    assert client.get("/analytics/kpis").status_code == 401


# --------------------------------------------------------------------------- #
#  Evidence pack
# --------------------------------------------------------------------------- #
def test_the_dashboard_points_at_the_one_real_evidence_pack_endpoint(client, auth):
    """Not a second export. A regulator must be able to re-derive the manifest
    hash the platform published, and two serialisations would disagree."""
    body = client.get("/analytics/evidence-pack-availability", headers=auth).json()
    assert body["endpoint"] == "/retention/evidence-pack"
    assert body["required_permission"] == "audit.export"
    assert body["allowed"] is True


def test_a_role_without_audit_export_is_told_why_it_cannot_download(client, db):
    org = db.query(Organization).filter(Organization.code == "CODEX").first()
    if not org:
        db.add(Organization(name="Codex", code="CODEX", is_active=True))
        db.commit()
    headers = _org_scoped_headers(db)
    body = client.get("/analytics/evidence-pack-availability", headers=headers).json()
    assert body["allowed"] is False
    assert "audit.export" in body["reason"]
