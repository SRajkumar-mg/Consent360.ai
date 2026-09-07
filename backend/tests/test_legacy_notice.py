"""Tests for `app/services/legacy_notice.py` (R2-11 / gap A-08, K-26): the
s.5(2) legacy-notice campaign for consent given before the Act's notice
requirements took effect.

This module shipped with no tests of its own. The cases below are the ones
its own docstrings and comments flag as load-bearing:

* the cutoff boundary is `< cutoff`, not `<= cutoff` — a consent last
  affirmed exactly ON the cutoff is a post-Act affirmation and falls OUTSIDE
  the cohort (see `as_cutoff_datetime` and `build_cohort`'s module doc);
* `include_notified=False` excludes only a principal whose notice actually
  *reached* her (`REACHED_STATUSES`) — a FAILED notice is not a notice, and
  must leave her in the cohort;
* a legacy-notice campaign is identified only by `details.legacy_campaign_ref`
  on the `Notification` rows it queued, grouped back by `list_campaigns` /
  `campaign_detail` — there is deliberately no campaigns table;
* K-26 (`legacy_notice_delivery_pct`) is `None`, not `0.0`, when there is no
  pre-Act cohort at all;
* the route guard in `routes/reconsent.py::_legacy_scope` refuses an
  org-scoped caller naming a foreign `source_app` with a 403, the same way
  every other tenant-scoped surface in this codebase does.

Every customer gets its own `source_app` (derived from its external_id,
following `tests/test_reconsent.py`'s convention) unless a test explicitly
needs several customers under one shared tenant, because `tests/conftest.py`
gives every test its own `SessionLocal()` but not its own transaction — rows
committed by one test are visible to every test that runs after it in the
same session, so a query that filters loosely by a shared `source_app` would
silently pick up other tests' data depending on execution order. Scoping
every query this tightly also lets several assertions below check the
COHORT'S SIZE, not just one member's presence in it.

`_legacy_scope` lives in `routes/reconsent.py`, not in `legacy_notice.py`
itself (the service raises nothing HTTP-shaped), so its test below imports
and calls it directly rather than going through a live HTTP round trip:
today's `codex_admin` / `jobhub_admin` / `skilllearn_admin` roles (see
`rbac.ROLE_PERMISSIONS`) do not carry `policy.view`/`policy.manage`, so an
org-scoped staff token cannot reach `/re-consent/legacy-notice/*` at all yet
— it would 403 on the permission dependency before ever reaching
`_legacy_scope`. That looks like a real gap worth someone's attention, but
it is an `rbac.py` permissions question, not a `legacy_notice.py`/
`_legacy_scope` bug, so it is reported here rather than "fixed" by loosening
a role this task was told not to edit.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models.entities import (
    AuditLog,
    Consent,
    Customer,
    DataCategory,
    Notification,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    Role,
    User,
)
from app.services import legacy_notice
from app.services.tenancy import resolve_tenant_id

BASE = "LEGACY_NOTICE_TEST"


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures / factories
# --------------------------------------------------------------------------- #
def _make_purpose(db, code, *, retention_days=365):
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
        data_items=[{"data_category_id": category.id, "necessity": "required", "description": "contact"}],
        consent_text="I consent.", is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()
    return purpose, category, activity, pv


def _make_customer(db, external_id, *, source_app=None, email=None, status="ACTIVE"):
    """Defaults `source_app` to one derived from `external_id`, so a test
    with a single customer gets it its own tenant for free and can assert
    on the exact cohort/campaign it produces, not merely on that customer's
    presence inside a cohort other tests may also be writing into."""
    source_app = source_app or f"{BASE}_{external_id.replace('-', '_')}"
    email = f"{external_id.lower()}@example.test" if email is None else email
    customer = Customer(
        external_id=external_id, name="Test Principal", email=email,
        source_app=source_app, status=status, tenant_id=resolve_tenant_id(db, source_app),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _make_consent(
    db, customer, purpose, category, activity, pv, *,
    status="GRANTED", granted_at=None, renewed_at=None, created_at=None, source_app=None,
):
    """Built directly rather than through `services/consent.py`'s transition
    functions, because these tests need exact control over the affirmed-at
    timestamp (`coalesce(renewed_at, granted_at, created_at)`) relative to a
    chosen cutoff — the transition helpers always stamp "now"."""
    consent = Consent(
        customer_id=customer.id, purpose_id=purpose.id, purpose_version_id=pv.id,
        data_category_id=category.id, processing_activity_id=activity.id,
        status=status, source_app=source_app if source_app is not None else customer.source_app,
        tenant_id=customer.tenant_id,
        granted_at=granted_at, renewed_at=renewed_at,
        created_at=created_at or granted_at or renewed_at or legacy_notice.utcnow(),
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent


def _make_legacy_notification(db, customer, *, status, campaign_ref=None, source_app=None, created_at=None):
    """A `LEGACY_NOTICE` row built directly (bypassing `queue_notification`)
    so a test can pin its `status` (including terminal ones like FAILED /
    ACKNOWLEDGED that the real send path never produces synchronously) and,
    when `campaign_ref` is None, exercise the "(ad-hoc)" bucket
    `list_campaigns` groups a campaign-less notice into."""
    details = {"legacy_cutoff": date(2027, 5, 13).isoformat()}
    if campaign_ref:
        details["legacy_campaign_ref"] = campaign_ref
    notification = Notification(
        tenant_id=customer.tenant_id, customer_id=customer.id, event_type="LEGACY_NOTICE",
        channel="EMAIL", status=status, source_app=source_app or customer.source_app,
        details=details, created_at=created_at or legacy_notice.utcnow(),
        sent_at=legacy_notice.utcnow() if status in legacy_notice.REACHED_STATUSES else None,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


# --------------------------------------------------------------------------- #
# Cutoff boundary: `< cutoff`, not `<= cutoff`
# --------------------------------------------------------------------------- #
def test_a_consent_renewed_exactly_on_the_cutoff_is_outside_the_cohort(db):
    """`as_cutoff_datetime` returns midnight UTC at the start of the cutoff
    date, and `build_cohort` filters `_last_affirmed() < cutoff_dt` - so a
    consent whose renewal lands exactly on that instant is a post-Act
    affirmation, not a pre-Act one, and must be excluded."""
    purpose, category, activity, pv = _make_purpose(db, "cutoff_on")
    cutoff = date(2027, 5, 13)
    cutoff_dt = legacy_notice.as_cutoff_datetime(cutoff)

    on_the_dot = _make_customer(db, "LNC-ON-CUTOFF")
    _make_consent(db, on_the_dot, purpose, category, activity, pv, renewed_at=cutoff_dt)

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=on_the_dot.source_app)
    assert members == []


def test_a_consent_affirmed_one_microsecond_before_the_cutoff_is_in_the_cohort(db):
    purpose, category, activity, pv = _make_purpose(db, "cutoff_before")
    cutoff = date(2027, 5, 13)
    cutoff_dt = legacy_notice.as_cutoff_datetime(cutoff)

    just_before = _make_customer(db, "LNC-JUST-BEFORE")
    _make_consent(
        db, just_before, purpose, category, activity, pv,
        renewed_at=cutoff_dt - timedelta(microseconds=1),
    )

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=just_before.source_app)
    assert [m.external_id for m in members] == [just_before.external_id]


def test_a_consent_affirmed_after_the_cutoff_is_outside_the_cohort(db):
    purpose, category, activity, pv = _make_purpose(db, "cutoff_after")
    cutoff = date(2027, 5, 13)

    later = _make_customer(db, "LNC-AFTER-CUTOFF")
    _make_consent(db, later, purpose, category, activity, pv, granted_at=_utc(2027, 6, 1))

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=later.source_app)
    assert members == []


def test_a_renewal_after_the_cutoff_removes_a_previously_pre_act_consent(db):
    """The module's own doc: a renewal after the cut-off is a fresh
    affirmative act and takes the consent OUT of the cohort, even though the
    original grant was pre-Act - `coalesce(renewed_at, granted_at, created_at)`
    picks the renewal, not the original grant."""
    purpose, category, activity, pv = _make_purpose(db, "renewed_out")
    cutoff = date(2027, 5, 13)

    customer = _make_customer(db, "LNC-RENEWED-OUT")
    _make_consent(
        db, customer, purpose, category, activity, pv,
        granted_at=_utc(2020, 1, 1), renewed_at=_utc(2027, 6, 1),
    )

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=customer.source_app)
    assert members == []


# --------------------------------------------------------------------------- #
# include_notified=False excludes only REACHED notices, never FAILED ones
# --------------------------------------------------------------------------- #
def test_include_notified_false_excludes_a_reached_notice_but_keeps_a_failed_one(db):
    purpose, category, activity, pv = _make_purpose(db, "reach_vs_fail")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    tenant = f"{BASE}_REACH_VS_FAIL"

    reached = _make_customer(db, "LNC-REACHED", source_app=tenant)
    _make_consent(db, reached, purpose, category, activity, pv, granted_at=before_cutoff)
    _make_legacy_notification(db, reached, status="SENT")

    failed = _make_customer(db, "LNC-FAILED", source_app=tenant)
    _make_consent(db, failed, purpose, category, activity, pv, granted_at=before_cutoff)
    _make_legacy_notification(db, failed, status="FAILED")

    never_tried = _make_customer(db, "LNC-NEVER-TRIED", source_app=tenant)
    _make_consent(db, never_tried, purpose, category, activity, pv, granted_at=before_cutoff)

    with_all = {
        m.external_id for m in
        legacy_notice.build_cohort(db, cutoff=cutoff, source_app=tenant, include_notified=True)
    }
    assert with_all == {reached.external_id, failed.external_id, never_tried.external_id}

    outstanding_only = {
        m.external_id for m in
        legacy_notice.build_cohort(db, cutoff=cutoff, source_app=tenant, include_notified=False)
    }
    assert outstanding_only == {failed.external_id, never_tried.external_id}, (
        "a REACHED notice must drop her from the outstanding cohort, but a FAILED one - a bounce, "
        "not a notice - must not"
    )


def test_already_notified_property_matches_reached_statuses_exactly(db):
    """Belt and braces on `CohortMember.already_notified` itself, independent
    of the cohort-filtering test above: SENT/DELIVERED/ACKNOWLEDGED read as
    already notified, PENDING/FAILED do not."""
    purpose, category, activity, pv = _make_purpose(db, "already_notified_prop")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    tenant = f"{BASE}_ALREADY_NOTIFIED"

    outcomes = {}
    for status in ("PENDING", "SENT", "DELIVERED", "FAILED", "ACKNOWLEDGED"):
        customer = _make_customer(db, f"LNC-STATUS-{status}", source_app=tenant)
        _make_consent(db, customer, purpose, category, activity, pv, granted_at=before_cutoff)
        _make_legacy_notification(db, customer, status=status)
        outcomes[status] = customer.external_id

    members_by_id = {
        m.external_id: m
        for m in legacy_notice.build_cohort(db, cutoff=cutoff, source_app=tenant, include_notified=True)
    }
    for status in ("SENT", "DELIVERED", "ACKNOWLEDGED"):
        assert members_by_id[outcomes[status]].already_notified is True, status
    for status in ("PENDING", "FAILED"):
        assert members_by_id[outcomes[status]].already_notified is False, status


# --------------------------------------------------------------------------- #
# Campaign grouping by `details.legacy_campaign_ref`
# --------------------------------------------------------------------------- #
def test_send_legacy_notices_stamps_every_queued_notification_with_the_same_campaign_ref(db):
    purpose, category, activity, pv = _make_purpose(db, "campaign_stamp")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    tenant = f"{BASE}_CAMPAIGN_STAMP"

    c1 = _make_customer(db, "LNC-CAMPAIGN-1", source_app=tenant)
    _make_consent(db, c1, purpose, category, activity, pv, granted_at=before_cutoff)
    c2 = _make_customer(db, "LNC-CAMPAIGN-2", source_app=tenant)
    _make_consent(db, c2, purpose, category, activity, pv, granted_at=before_cutoff)

    result = legacy_notice.send_legacy_notices(db, cutoff=cutoff, source_app=tenant)
    ref = result["campaign_ref"]
    assert ref and ref.startswith("LNC-")
    assert result["cohort_size"] == 2
    assert result["recipients"] == 2

    rows = db.query(Notification).filter(
        Notification.event_type == "LEGACY_NOTICE", Notification.source_app == tenant,
    ).all()
    assert rows, "the campaign must have queued at least one notification"
    for row in rows:
        assert row.details.get("legacy_campaign_ref") == ref


def test_list_campaigns_groups_by_ref_and_buckets_ad_hoc_notices_separately(db):
    purpose, category, activity, pv = _make_purpose(db, "campaign_grouping")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    tenant = f"{BASE}_CAMPAIGN_GROUPING"

    customer = _make_customer(db, "LNC-GROUPING", source_app=tenant)
    _make_consent(db, customer, purpose, category, activity, pv, granted_at=before_cutoff)
    result = legacy_notice.send_legacy_notices(db, cutoff=cutoff, source_app=tenant)
    ref = result["campaign_ref"]

    # A LEGACY_NOTICE queued by hand (e.g. via POST /notifications/trigger)
    # carries no campaign ref at all.
    ad_hoc_customer = _make_customer(db, "LNC-AD-HOC", source_app=tenant)
    _make_legacy_notification(db, ad_hoc_customer, status="SENT", campaign_ref=None)

    campaigns = legacy_notice.list_campaigns(db, source_app=tenant)
    by_ref = {c["campaign_ref"]: c for c in campaigns}
    assert set(by_ref) == {ref, "(ad-hoc)"}
    assert by_ref[ref]["recipients"] == 1
    assert by_ref["(ad-hoc)"]["recipients"] == 1


def test_campaign_detail_returns_only_that_campaigns_own_rows(db):
    purpose, category, activity, pv = _make_purpose(db, "campaign_detail_scope")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    tenant = f"{BASE}_CAMPAIGN_DETAIL"

    c1 = _make_customer(db, "LNC-DETAIL-1", source_app=tenant)
    _make_consent(db, c1, purpose, category, activity, pv, granted_at=before_cutoff)
    first = legacy_notice.send_legacy_notices(db, cutoff=cutoff, source_app=tenant)

    c2 = _make_customer(db, "LNC-DETAIL-2", source_app=tenant)
    _make_consent(db, c2, purpose, category, activity, pv, granted_at=before_cutoff)
    second = legacy_notice.send_legacy_notices(db, cutoff=cutoff, source_app=tenant, include_notified=True)
    assert first["campaign_ref"] != second["campaign_ref"]

    detail = legacy_notice.campaign_detail(db, first["campaign_ref"], source_app=tenant)
    assert detail is not None
    assert detail["campaign_ref"] == first["campaign_ref"]
    delivered_refs = {d["customer_external_id"] for d in detail["deliveries"]}
    assert delivered_refs == {c1.external_id}, (
        "the second campaign's delivery must not leak into the first campaign's detail"
    )


def test_campaign_detail_returns_none_for_an_unknown_ref(db):
    assert legacy_notice.campaign_detail(db, "LNC-DOES-NOT-EXIST", source_app=f"{BASE}_UNKNOWN_REF") is None


def test_a_campaign_is_audited_even_when_the_cohort_is_empty(db):
    """`send_legacy_notices` writes LEGACY_NOTICE_CAMPAIGN_STARTED BEFORE
    queuing anything and regardless of cohort size - the docstring's own
    stated reason: a campaign with zero recipients otherwise leaves no trace
    in `notifications` at all."""
    tenant = f"{BASE}_EMPTY_COHORT"
    result = legacy_notice.send_legacy_notices(db, cutoff=date(2027, 5, 13), source_app=tenant)
    assert result["cohort_size"] == 0
    assert result["recipients"] == 0

    audit_row = (
        db.query(AuditLog)
        .filter(AuditLog.event == "LEGACY_NOTICE_CAMPAIGN_STARTED", AuditLog.source_app == tenant)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit_row is not None
    assert audit_row.details["cohort_size"] == 0
    assert audit_row.details["legacy_campaign_ref"] == result["campaign_ref"]


# --------------------------------------------------------------------------- #
# K-26: None, not 0.0, for an empty cohort
# --------------------------------------------------------------------------- #
def test_k26_delivery_pct_is_none_not_zero_for_an_empty_cohort(db):
    tenant = f"{BASE}_K26_EMPTY"
    metrics = legacy_notice.legacy_notice_metrics(db, cutoff=date(2027, 5, 13), source_app=tenant)
    assert metrics["pre_act_consents"] == 0
    assert metrics["legacy_notice_delivery_pct"] is None
    assert metrics["legacy_notice_delivery_pct"] != 0.0


def test_k26_delivery_pct_counts_only_reached_notices(db):
    purpose, category, activity, pv = _make_purpose(db, "k26_pct")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    tenant = f"{BASE}_K26_PARTIAL"

    notified = _make_customer(db, "LNC-K26-NOTIFIED", source_app=tenant)
    _make_consent(db, notified, purpose, category, activity, pv, granted_at=before_cutoff)
    _make_legacy_notification(db, notified, status="DELIVERED")

    unreached = _make_customer(db, "LNC-K26-UNREACHED", source_app=tenant)
    _make_consent(db, unreached, purpose, category, activity, pv, granted_at=before_cutoff)
    _make_legacy_notification(db, unreached, status="FAILED")

    metrics = legacy_notice.legacy_notice_metrics(db, cutoff=cutoff, source_app=tenant)
    assert metrics["pre_act_consents"] == 2
    assert metrics["consents_notified"] == 1
    assert metrics["legacy_notice_delivery_pct"] == 50.0


# --------------------------------------------------------------------------- #
# Tenant scoping inside the service itself
# --------------------------------------------------------------------------- #
def test_source_app_filter_excludes_another_tenants_consents(db):
    purpose, category, activity, pv = _make_purpose(db, "tenant_scope")
    cutoff = date(2027, 5, 13)
    before_cutoff = _utc(2020, 1, 1)
    mine_tenant = f"{BASE}_TENANT_SCOPE_MINE"
    their_tenant = f"{BASE}_TENANT_SCOPE_THEIRS"

    mine = _make_customer(db, "LNC-MINE", source_app=mine_tenant)
    _make_consent(db, mine, purpose, category, activity, pv, granted_at=before_cutoff)
    theirs = _make_customer(db, "LNC-THEIRS", source_app=their_tenant)
    _make_consent(db, theirs, purpose, category, activity, pv, granted_at=before_cutoff)

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=mine_tenant)
    assert [m.external_id for m in members] == [mine.external_id]


# --------------------------------------------------------------------------- #
# Cohort composition rules not explicitly called out above but exercised by
# `build_cohort`'s own logic - a purged customer, and a consent status that
# is not being relied on to process, must not appear.
# --------------------------------------------------------------------------- #
def test_a_purged_customer_is_excluded_from_the_cohort(db):
    purpose, category, activity, pv = _make_purpose(db, "purged_excluded")
    cutoff = date(2027, 5, 13)

    purged = _make_customer(db, "LNC-PURGED", status="PURGED")
    _make_consent(db, purged, purpose, category, activity, pv, granted_at=_utc(2020, 1, 1))

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=purged.source_app)
    assert members == []


@pytest.mark.parametrize("status", ["WITHDRAWN", "DENIED", "EXPIRED", "NOT_REQUESTED"])
def test_a_consent_not_being_relied_on_is_excluded_from_the_cohort(db, status):
    """s.5(2) has nothing to bite on for a consent that authorises no
    processing today - only RELIED_ON_STATUSES belong in the cohort."""
    purpose, category, activity, pv = _make_purpose(db, f"not_relied_{status.lower()}")
    cutoff = date(2027, 5, 13)

    customer = _make_customer(db, f"LNC-NOT-RELIED-{status}")
    _make_consent(db, customer, purpose, category, activity, pv, status=status, granted_at=_utc(2020, 1, 1))

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=customer.source_app)
    assert members == []


def test_multiple_pre_act_consents_for_one_customer_are_grouped_into_one_member(db):
    purpose_a, cat_a, act_a, pv_a = _make_purpose(db, "grouping_a")
    purpose_b, cat_b, act_b, pv_b = _make_purpose(db, "grouping_b")
    cutoff = date(2027, 5, 13)

    customer = _make_customer(db, "LNC-GROUPED-CUSTOMER")
    _make_consent(db, customer, purpose_a, cat_a, act_a, pv_a, granted_at=_utc(2019, 1, 1))
    _make_consent(db, customer, purpose_b, cat_b, act_b, pv_b, granted_at=_utc(2020, 6, 1))

    members = legacy_notice.build_cohort(db, cutoff=cutoff, source_app=customer.source_app)
    assert len(members) == 1, "one principal with two pre-Act consents is one cohort member, not two"
    member = members[0]
    assert member.consent_count == 2
    assert sorted(member.purpose_codes) == sorted(["grouping_a", "grouping_b"])
    # oldest_consent_at is the EARLIEST affirmation across her consents.
    assert member.oldest_consent_at == _utc(2019, 1, 1)


# --------------------------------------------------------------------------- #
# Org-scope: a foreign source_app is refused with a 403.
#
# `_legacy_scope` lives in routes/reconsent.py (the service itself never
# raises HTTP errors) - see this file's module docstring for why the test
# below calls it directly instead of round-tripping through the live routes.
# --------------------------------------------------------------------------- #
def _org_scoped_user(db, role_name):
    from app.core.encryption import hmac_digest
    from app.core.rbac import ROLE_PERMISSIONS
    from app.core.security import hash_password

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        role = Role(name=role_name, description=role_name, permissions=ROLE_PERMISSIONS[role_name], is_system=True)
        db.add(role)
        db.flush()
        db.commit()
    username = f"{role_name}-legacy-notice-test"
    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(
            username=username, full_name=username, email=f"{username}@example.com",
            email_search=hmac_digest(f"{username}@example.com"),
            password_hash=hash_password("Test@1234"), role_id=role.id, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def test_legacy_scope_refuses_an_org_scoped_caller_naming_a_foreign_source_app(db):
    from app.api.routes.reconsent import _legacy_scope

    codex_user = _org_scoped_user(db, "codex_admin")
    with pytest.raises(HTTPException) as exc:
        _legacy_scope(codex_user, "SKILLLEARN")
    assert exc.value.status_code == 403
    assert "own tenant" in exc.value.detail


def test_legacy_scope_allows_an_org_scoped_caller_naming_its_own_source_app(db):
    from app.api.routes.reconsent import _legacy_scope

    codex_user = _org_scoped_user(db, "codex_admin")
    assert _legacy_scope(codex_user, "CODEX") == "CODEX"


def test_legacy_scope_defaults_an_org_scoped_caller_to_its_own_tenant_when_unspecified(db):
    from app.api.routes.reconsent import _legacy_scope

    jobhub_user = _org_scoped_user(db, "jobhub_admin")
    assert _legacy_scope(jobhub_user, None) == "JOBHUB"


def test_legacy_scope_lets_an_unscoped_role_name_any_tenant(db):
    """A platform role (admin, dpo, auditor, operator, viewer - none of them
    in ORG_SCOPE_MAP) is not pinned to one tenant, so naming any source_app,
    or none at all, is allowed."""
    from app.api.routes.reconsent import _legacy_scope

    admin_user = _org_scoped_user(db, "admin")
    assert _legacy_scope(admin_user, "SKILLLEARN") == "SKILLLEARN"
    assert _legacy_scope(admin_user, None) is None
