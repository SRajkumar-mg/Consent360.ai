"""R2-07: the compliance KPI catalogue.

This module is an **aggregator**. It computes nothing that another module
already owns. Where a specialised module publishes a KPI - the breach
register's K-39..K-41, the processor register's K-08 and K-32, the grievance
queue's K-23, the Consent Manager's K-42/K-43, `services/kpi.py`'s K-18 and
K-44/K-45 - this module calls that function and republishes its number, and
`KpiOut.computed_by` names the module it came from. Two screens disagreeing
about one compliance figure is worse than one screen not showing it, and the
only structural defence against that is to have exactly one implementation
per KPI. Where nothing owns a KPI yet, the derivation lives here and here
only, and this module becomes its owner.

**Every KPI carries a status, and zero is never used to mean unknown.**
Four states, the vocabulary borrowed intact from
`app/services/evidence_pack.py`:

* ``LIVE``        - a real number over a real denominator.
* ``NO_DATA``     - instrumented, queried, empty sample. The ratio is
                    undefined, so ``value`` is null. A 0% opt-in rate and
                    "nobody has been shown a banner yet" are opposite
                    findings and must not render alike.
* ``PARTIAL``     - computed over less than the KPI's full definition, with
                    ``coverage`` stating exactly what is excluded.
* ``UNAVAILABLE`` - this build has no data source. ``unblocked_by`` names
                    the gap-register item that would supply one.

The evidence pack was bitten once by a section that kept saying UNAVAILABLE
after the register behind it had been built. The same trap is live here:
every UNAVAILABLE below is a claim about this build that stops being true the
moment someone lands the feature named in `unblocked_by`, and
`tests/test_kpi_dashboard.py` asserts that the ones whose modules DO exist
are not sitting in that state.

**Tenant scoping fails closed.** Six of the upstream computations are
unconditionally platform-wide (`services/kpi.py`'s three, the Consent Manager
metrics, `purposes.coverage_report`, `retention_scan`). Surfacing those to an
org-scoped role - `jobhub_admin`, `codex_admin`, `skilllearn_admin` - would
show one tenant another tenant's aggregate. Rather than re-implementing them
with a filter (which would be exactly the divergence this module exists to
prevent), a `PLATFORM_ONLY` KPI viewed by a scoped role reports UNAVAILABLE
with that as its stated reason. A missing number is a smaller failure than a
leaked one, and a silently wrong one is the largest failure of the three.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import (
    AuditLog,
    Consent,
    ConsentEvidence,
    Customer,
    KpiSnapshot,
    Notification,
    NoticeVersion,
    Organization,
    Purpose,
    PurposeVersion,
)

logger = logging.getLogger("app.kpi_catalogue")

ACTIVE_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")
NEGATIVE_STATUSES = ("DENIED", "WITHDRAWN")

LIVE = "LIVE"
NO_DATA = "NO_DATA"
PARTIAL = "PARTIAL"
UNAVAILABLE = "UNAVAILABLE"

# Scope support, per KPI.
TENANT = "TENANT"          # can be filtered to one tenant
PLATFORM_ONLY = "PLATFORM_ONLY"  # upstream computation is platform-wide

HONESTY_NOTE = (
    "Only \u201cLive\u201d and \u201cPartial\u201d carry a number. \u201cNo data yet\u201d means this metric is "
    "instrumented and was queried, and the sample for this period was empty \u2014 its value is "
    "unknown, not zero. \u201cNot instrumented\u201d means this build has no data source for it at all, "
    "and the drill-down names the work that would give it one. Neither is a finding of nil, and "
    "neither is a zero. \u201cPartial\u201d states in its drill-down exactly what it leaves out."
)


# ---------------------------------------------------------------------------
#  Context and result helpers
# ---------------------------------------------------------------------------
@dataclass
class KpiContext:
    db: Session
    scope: Optional[str]              # source_app, or None for all tenants
    tenant_id: Optional[int]
    period_start: datetime
    period_end: datetime
    purpose_code: Optional[str] = None
    language: Optional[str] = None
    cache: dict = field(default_factory=dict)

    @property
    def period_days(self) -> int:
        return max(1, int((self.period_end - self.period_start).total_seconds() // 86400))

    def memo(self, key: str, producer: Callable[[], dict]) -> dict:
        """Call one upstream metrics function once per request even when four
        KPIs read from it (K-39/K-40/K-41 all come out of `breach_metrics`)."""
        if key not in self.cache:
            try:
                self.cache[key] = {"ok": True, "value": producer()}
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("KPI source %r failed", key)
                self.cache[key] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return self.cache[key]


def _fmt(value: Optional[float], unit: str) -> str:
    if value is None:
        return "—"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "count":
        return f"{int(value):,}"
    if unit == "seconds":
        return f"{value:.1f}s"
    if unit == "hours":
        return f"{value:.1f} h"
    if unit == "days":
        return f"{value:.1f} d"
    return f"{value:g}"


def live(value: Optional[float], *, numerator=None, denominator=None, sample_size=None,
         detail: Optional[dict] = None, breakdown: Optional[list] = None) -> dict:
    return {
        "status": LIVE, "value": value, "numerator": numerator, "denominator": denominator,
        "sample_size": sample_size, "detail": detail or {}, "breakdown": breakdown or [],
    }


def no_data(reason: str, *, detail: Optional[dict] = None) -> dict:
    return {"status": NO_DATA, "value": None, "reason": reason, "detail": detail or {}, "breakdown": []}


def partial(value: Optional[float], coverage: str, *, numerator=None, denominator=None,
            sample_size=None, detail: Optional[dict] = None, breakdown: Optional[list] = None) -> dict:
    return {
        "status": PARTIAL, "value": value, "coverage": coverage, "numerator": numerator,
        "denominator": denominator, "sample_size": sample_size,
        "detail": detail or {}, "breakdown": breakdown or [],
    }


def unavailable(reason: str, unblocked_by: str) -> dict:
    return {"status": UNAVAILABLE, "value": None, "reason": reason,
            "unblocked_by": unblocked_by, "detail": {}, "breakdown": []}


def _pct(numerator: float, denominator: float) -> Optional[float]:
    """None, not 0.0, when the denominator is empty. Every caller here then
    reports NO_DATA rather than a fabricated 0%."""
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 2)


def _row(dimension: str, label: str, *, value=None, numerator=None, denominator=None,
         unit: str = "count", status: str = LIVE, detail: Optional[dict] = None) -> dict:
    return {
        "dimension": dimension, "label": label, "value": value, "numerator": numerator,
        "denominator": denominator, "unit": unit, "status": status, "detail": detail or {},
    }


def _source_error(source: dict, what: str) -> dict:
    """An upstream module that raised is not a nil finding either."""
    return unavailable(
        f"The module that owns {what} could not be read this request: {source.get('error')}",
        "Fix or redeploy the owning module; this dashboard never recomputes a KPI it does not own.",
    )


# ---------------------------------------------------------------------------
#  Scoped query helpers over the core consent tables
# ---------------------------------------------------------------------------
def _consent_count(ctx: KpiContext, *filters) -> int:
    q = ctx.db.query(func.count(Consent.id))
    if ctx.scope:
        q = q.filter(Consent.source_app == ctx.scope)
    if ctx.purpose_code:
        q = q.join(Purpose, Purpose.id == Consent.purpose_id).filter(Purpose.code == ctx.purpose_code)
    for f in filters:
        q = q.filter(f)
    return q.scalar() or 0


# ---------------------------------------------------------------------------
#  K-01 .. K-04, K-47: the banner funnel (this module's own instrumentation)
# ---------------------------------------------------------------------------
def _banner(ctx: KpiContext) -> dict:
    from app.services import banner_events

    return ctx.memo(
        "banner_funnel",
        lambda: banner_events.funnel(
            ctx.db, tenant_id=ctx.tenant_id, start=ctx.period_start, end=ctx.period_end,
            language=ctx.language,
        ),
    )


_NO_BANNER_EVENTS = (
    "No banner impression or decision events have been recorded for this scope and period. "
    "The demo sites emit them from their consent banners; a period with no traffic, or a "
    "deployment whose banners predate this instrumentation, produces an empty sample."
)


def k01_optin_rate(ctx: KpiContext) -> dict:
    """Decision-based per purpose (the register's preferred formula), with
    the impression-based ratio alongside rather than instead."""
    q = (
        ctx.db.query(Purpose.code, Purpose.name, Consent.status, func.count(Consent.id))
        .join(Consent, Consent.purpose_id == Purpose.id)
    )
    if ctx.scope:
        q = q.filter(Consent.source_app == ctx.scope)
    if ctx.purpose_code:
        q = q.filter(Purpose.code == ctx.purpose_code)
    rows = q.group_by(Purpose.code, Purpose.name, Consent.status).all()

    agg: dict[str, dict] = {}
    for code, name, status, count in rows:
        bucket = agg.setdefault(code, {"name": name, "in": 0, "out": 0})
        if status in ACTIVE_STATUSES:
            bucket["in"] += count
        elif status in NEGATIVE_STATUSES:
            bucket["out"] += count

    impression_side = {}
    source = ctx.memo("banner_optin", lambda: _banner_optin(ctx))
    if source.get("ok"):
        impression_side = source["value"]

    breakdown = []
    total_in = total_out = 0
    for code in sorted(agg):
        bucket = agg[code]
        decided = bucket["in"] + bucket["out"]
        total_in += bucket["in"]
        total_out += bucket["out"]
        impression = impression_side.get(code, {})
        breakdown.append(_row(
            "purpose", f"{code} — {bucket['name']}",
            value=_pct(bucket["in"], decided), numerator=bucket["in"], denominator=decided,
            unit="percent", status=LIVE if decided else NO_DATA,
            detail={
                "opt_ins": bucket["in"], "opt_outs": bucket["out"],
                "impression_based_pct": impression.get("optin_rate_pct"),
                "offered_sessions": impression.get("offered_sessions"),
                "granted_sessions": impression.get("granted_sessions"),
            },
        ))

    decided_total = total_in + total_out
    if not decided_total:
        return no_data(
            "No consent has been granted, denied or withdrawn for this scope, so there is no "
            "decision to compute an opt-in rate over.",
            detail={"impression_based": impression_side},
        )
    return live(
        _pct(total_in, decided_total), numerator=total_in, denominator=decided_total,
        sample_size=decided_total, breakdown=breakdown,
        detail={
            "formula_used": "decision-based (opt-ins / (opt-ins + opt-outs))",
            "impression_based_available": bool(impression_side),
            "note": (
                "The register prefers the decision-based ratio because bots and ad-blockers "
                "inflate impressions. The impression-based ratio is carried per purpose in the "
                "drill-down rather than substituted for this one."
            ),
        },
    )


def _banner_optin(ctx: KpiContext) -> dict:
    from app.services import banner_events

    return banner_events.optin_by_purpose(
        ctx.db, tenant_id=ctx.tenant_id, start=ctx.period_start, end=ctx.period_end,
        language=ctx.language,
    )


def k02_reject_and_no_choice(ctx: KpiContext) -> dict:
    source = _banner(ctx)
    if not source.get("ok"):
        return _source_error(source, "banner events")
    f = source["value"]
    shown = f["impression_sessions"]
    decided = f["decided_with_impression"]
    if not shown and not f["decision_sessions"]:
        return no_data(_NO_BANNER_EVENTS)

    reject_pct = _pct(f["mix"]["REJECT_ALL"], f["decision_sessions"])
    no_choice_pct = _pct(f["no_choice_sessions"], shown)
    breakdown = [
        _row("outcome", "Reject-all rate (of decisions)", value=reject_pct,
             numerator=f["mix"]["REJECT_ALL"], denominator=f["decision_sessions"],
             unit="percent", status=LIVE if f["decision_sessions"] else NO_DATA),
        _row("outcome", "No-choice / ignore rate (of impressions)", value=no_choice_pct,
             numerator=f["no_choice_sessions"], denominator=shown,
             unit="percent", status=LIVE if shown else NO_DATA),
    ]
    if reject_pct is None:
        return no_data(_NO_BANNER_EVENTS, detail=f["mix"])
    return live(
        reject_pct, numerator=f["mix"]["REJECT_ALL"], denominator=f["decision_sessions"],
        sample_size=f["decision_sessions"], breakdown=breakdown,
        detail={
            "no_choice_rate_pct": no_choice_pct,
            "impression_sessions": shown,
            "decision_sessions": f["decision_sessions"],
            "decisions_without_impression": f["decisions_without_impression"],
            "subsequent_decisions": f["subsequent_decisions"],
            "note": (
                "Sessions, not rows: a session's decision is its first one. "
                "'No choice' is derived from the absence of a decision, never reported by the "
                "client - a script that stopped running cannot report that it stopped."
            ),
        },
    )


def k03_partial_consent(ctx: KpiContext) -> dict:
    source = _banner(ctx)
    if not source.get("ok"):
        return _source_error(source, "banner events")
    f = source["value"]
    denominator = f["sessions_with_offered_purposes"]
    if not denominator:
        return no_data(
            _NO_BANNER_EVENTS if not f["decision_sessions"] else
            "Decisions were recorded but none listed the purposes offered, so no decision can be "
            "classified as a subset."
        )
    return live(
        _pct(f["partial_sessions"], denominator),
        numerator=f["partial_sessions"], denominator=denominator, sample_size=denominator,
        detail={
            "partial_sessions": f["partial_sessions"],
            "note": "Counts decisions taken through the per-purpose controls that landed on "
                    "something other than everything. A blanket accept-all or reject-all is not "
                    "a partial choice, whatever set of purposes it happens to leave switched on.",
        },
    )


def k04_time_to_decision(ctx: KpiContext) -> dict:
    from app.services import banner_events

    source = _banner(ctx)
    if not source.get("ok"):
        return _source_error(source, "banner events")
    stats = banner_events.duration_stats(source["value"]["durations_ms"])
    if not stats["samples"]:
        return no_data(
            _NO_BANNER_EVENTS if not source["value"]["decision_sessions"] else
            "Decisions were recorded but none could be paired with the impression that preceded "
            "them, so no elapsed time was measurable. The duration is measured between two "
            "server-stamped rows and is never taken from a client-reported number."
        )
    return live(
        stats["median_seconds"], sample_size=stats["samples"],
        detail=stats | {"note": "Median seconds from notice shown to first affirmative act."},
    )


def k47_decision_mix(ctx: KpiContext) -> dict:
    source = _banner(ctx)
    if not source.get("ok"):
        return _source_error(source, "banner events")
    f = source["value"]
    total = f["decision_sessions"]
    if not total:
        return no_data(_NO_BANNER_EVENTS)
    breakdown = [
        _row("outcome", label, value=_pct(f["mix"][key], total), numerator=f["mix"][key],
             denominator=total, unit="percent")
        for key, label in (
            ("ACCEPT_ALL", "Accept all"), ("REJECT_ALL", "Reject all"),
            ("GRANULAR", "Granular choice"), ("DISMISSED", "Dismissed"),
        )
    ]
    for version, counts in sorted(f["mix_by_banner_version"].items()):
        breakdown.append(_row(
            "banner_version", version, value=_pct(counts.get("GRANULAR", 0), counts["total"]),
            numerator=counts.get("GRANULAR", 0), denominator=counts["total"], unit="percent",
            detail=counts,
        ))
    return live(
        _pct(f["mix"]["GRANULAR"], total), numerator=f["mix"]["GRANULAR"], denominator=total,
        sample_size=total, breakdown=breakdown,
        detail=f["mix"] | {
            "headline": "share of decisions using the granular option",
            "note": "s.6(1) 'specific': a banner whose granular option is never used is not offering one.",
        },
    )


# ---------------------------------------------------------------------------
#  K-05, K-06: notice version and language
# ---------------------------------------------------------------------------
def k05_notice_version_distribution(ctx: KpiContext) -> dict:
    q = ctx.db.query(Consent.notice_version_id, func.count(Consent.id)).filter(
        Consent.status.in_(ACTIVE_STATUSES)
    )
    if ctx.scope:
        q = q.filter(Consent.source_app == ctx.scope)
    rows = q.group_by(Consent.notice_version_id).all()
    total = sum(count for _, count in rows)
    if not total:
        return no_data("No active consent exists for this scope, so there is no notice version to attribute.")

    pinned = sum(count for version_id, count in rows if version_id is not None)
    version_ids = [vid for vid, _ in rows if vid is not None]
    labels = {
        vid: f"Notice v{version_number}"
        for vid, version_number in ctx.db.query(NoticeVersion.id, NoticeVersion.version_number)
        .filter(NoticeVersion.id.in_(version_ids)).all()
    } if version_ids else {}

    breakdown = [
        _row("notice_version",
             labels.get(version_id, "No notice version pinned" if version_id is None else f"Version {version_id}"),
             value=_pct(count, total), numerator=count, denominator=total, unit="percent",
             status=LIVE if version_id is not None else PARTIAL)
        for version_id, count in sorted(rows, key=lambda r: (r[0] is None, r[0] or 0))
    ]
    return live(
        _pct(pinned, total), numerator=pinned, denominator=total, sample_size=total,
        breakdown=breakdown,
        detail={
            "target": "100% of active consents map to a retained notice version",
            "unpinned": total - pinned,
            "note": (
                "A consent granted before any Notice existed for its purpose has none to pin; "
                "those are the shortfall, not a counting error."
            ),
        },
    )


def k06_language_distribution(ctx: KpiContext) -> dict:
    from app.services import banner_events

    q = ctx.db.query(ConsentEvidence.language, func.count(ConsentEvidence.id))
    if ctx.scope:
        q = q.filter(ConsentEvidence.source_app == ctx.scope)
    rows = q.group_by(ConsentEvidence.language).all()
    total = sum(count for _, count in rows)
    if not total:
        return no_data("No consent evidence exists for this scope, so no language has been recorded.")

    banner_languages = {}
    source = ctx.memo(
        "banner_languages",
        lambda: banner_events.by_language(
            ctx.db, tenant_id=ctx.tenant_id, start=ctx.period_start, end=ctx.period_end
        ),
    )
    if source.get("ok"):
        banner_languages = source["value"]

    breakdown = [
        _row("language", language or "(unset)", value=_pct(count, total), numerator=count,
             denominator=total, unit="percent",
             detail=banner_languages.get(language or "en", {}))
        for language, count in sorted(rows, key=lambda r: -r[1])
    ]
    distinct = len([lang for lang, _ in rows if lang])
    return live(
        float(distinct), numerator=distinct, denominator=23, sample_size=total,
        breakdown=breakdown,
        detail={
            "distinct_languages_used": distinct,
            "languages_offered_by_platform": 23,
            "target": "all 23 offered",
            "note": "Headline is the count of distinct languages consent was actually collected in.",
        },
    )


# ---------------------------------------------------------------------------
#  K-11 .. K-17: lifecycle
# ---------------------------------------------------------------------------
def k11_coverage(ctx: KpiContext) -> dict:
    customers_q = ctx.db.query(func.count(Customer.id))
    if ctx.scope:
        customers_q = customers_q.filter(Customer.source_app == ctx.scope)
    customers = customers_q.scalar() or 0

    # Resolve the consent-requiring purposes ONCE, as a set of ids, and use
    # that same set on both sides of the ratio.
    #
    # This is the second half of the K-11 fix and it is the one that actually
    # bounds the metric. Counting consent rows rather than (principal, purpose)
    # pairs printed 791%; fixing that alone still printed 104%, because the
    # numerator was every active consent while the denominator counted only
    # purposes that are active, current AND require consent. A consent held
    # against a retired purpose, or one whose current version dropped its
    # consent requirement, landed in the numerator with no slot in the
    # denominator to sit in. A coverage ratio that can exceed 100% is not
    # measuring coverage of anything.
    required_purpose_q = (
        ctx.db.query(Purpose.id)
        .join(PurposeVersion, PurposeVersion.purpose_id == Purpose.id)
        .filter(Purpose.is_active.is_(True), PurposeVersion.is_current.is_(True),
                PurposeVersion.requires_consent.is_(True))
    )
    if ctx.purpose_code:
        required_purpose_q = required_purpose_q.filter(Purpose.code == ctx.purpose_code)
    required_purpose_ids = {pid for (pid,) in required_purpose_q.distinct().all()}

    denominator = customers * len(required_purpose_ids)
    if not denominator:
        return no_data(
            "Coverage needs both customers and at least one consent-requiring purpose; this "
            f"scope has {customers} customer(s) and {len(required_purpose_ids)} such purpose(s)."
        )

    # Distinct (principal, purpose) PAIRS, not consent rows: a consent row is
    # per customer x purpose x data category x processing activity x
    # source_app, so one principal covered for one purpose spans several rows.
    covered_q = ctx.db.query(Consent.customer_id, Consent.purpose_id).filter(
        Consent.status.in_(ACTIVE_STATUSES),
        Consent.purpose_id.in_(required_purpose_ids),
    )
    if ctx.scope:
        covered_q = covered_q.filter(Consent.source_app == ctx.scope)
    covered = covered_q.distinct().count()
    total_rows = _consent_count(ctx, Consent.status.in_(ACTIVE_STATUSES))
    return live(
        _pct(covered, denominator), numerator=covered, denominator=denominator,
        sample_size=customers,
        detail={"customers": customers,
                "consent_required_purposes": len(required_purpose_ids),
                "covered_customer_purpose_pairs": covered,
                "active_consent_rows": total_rows,
                "note": "Numerator counts distinct (principal, purpose) pairs with an active "
                        "consent, over the same set of consent-requiring purposes the "
                        "denominator is built from; a single such pair spans several consent "
                        "rows, one per data category and processing activity. Active consents "
                        "held against purposes that are retired, or whose current version no "
                        "longer requires consent, are excluded from both sides.",
                },
    )


def k12_decision_outcomes(ctx: KpiContext) -> dict:
    from app.api.routes.decisions import decision_report

    source = ctx.memo(
        "decision_report",
        lambda: _as_dict(decision_report(
            date_from=ctx.period_start, date_to=ctx.period_end, group_by="total",
            db=ctx.db, user=_FakeScopedUser(ctx.scope),
        )),
    )
    if not source.get("ok"):
        return _source_error(source, "the decision-engine report")
    totals = source["value"]["totals"]
    total = totals.get("total", 0)
    if not total:
        return no_data("No consent decision has been evaluated in this period for this scope.")
    breakdown = [
        _row("outcome", outcome, value=_pct(totals.get(outcome, 0), total),
             numerator=totals.get(outcome, 0), denominator=total, unit="percent")
        for outcome in ("ALLOW", "DENY", "REQUIRE_CONSENT", "WITHDRAWN", "EXPIRED")
    ]
    return partial(
        float(total), (
            "The outcome-count half of K-12 only. The p95 validation-API latency half is measured "
            "by the Consent Manager API sampler and is reported under K-42; it is not recomputed here."
        ),
        numerator=totals.get("ALLOW", 0), denominator=total, sample_size=total,
        breakdown=breakdown, detail=totals,
    )


def k13_freshness(ctx: KpiContext) -> dict:
    now = datetime.now(timezone.utc)
    active = _consent_count(ctx, Consent.status.in_(ACTIVE_STATUSES))
    if not active:
        return no_data("No active consent exists for this scope, so there is no age to distribute.")
    stale = _consent_count(
        ctx, Consent.status.in_(ACTIVE_STATUSES), Consent.expires_at.isnot(None),
        Consent.expires_at <= now,
    )
    buckets = []
    edges = [(0, 30), (30, 90), (90, 180), (180, 365)]
    for low, high in edges:
        count = _consent_count(
            ctx, Consent.status.in_(ACTIVE_STATUSES), Consent.granted_at.isnot(None),
            Consent.granted_at <= now - timedelta(days=low),
            Consent.granted_at > now - timedelta(days=high),
        )
        buckets.append(_row("age", f"{low}–{high} days old", value=_pct(count, active),
                            numerator=count, denominator=active, unit="percent"))
    oldest = _consent_count(
        ctx, Consent.status.in_(ACTIVE_STATUSES), Consent.granted_at.isnot(None),
        Consent.granted_at <= now - timedelta(days=365),
    )
    buckets.append(_row("age", "over 365 days old", value=_pct(oldest, active),
                        numerator=oldest, denominator=active, unit="percent"))
    return live(
        _pct(active - stale, active), numerator=active - stale, denominator=active,
        sample_size=active, breakdown=buckets,
        detail={"active_consents": active, "past_own_validity": stale,
                "headline": "share of active consents still inside their own validity period"},
    )


def k14_expiring(ctx: KpiContext) -> dict:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=30)
    expiring = _consent_count(
        ctx, Consent.status.in_(ACTIVE_STATUSES), Consent.expires_at.isnot(None),
        Consent.expires_at > now, Consent.expires_at <= horizon,
    )
    expired = _consent_count(ctx, Consent.status == "EXPIRED")
    return live(
        float(expiring), numerator=expiring, sample_size=expiring + expired,
        breakdown=[
            _row("state", "Expiring within 30 days", value=float(expiring), numerator=expiring),
            _row("state", "Already expired", value=float(expired), numerator=expired),
        ],
        detail={"expiring_within_30_days": expiring, "expired": expired},
    )


def k15_renewal_reminders(ctx: KpiContext) -> dict:
    now = datetime.now(timezone.utc)
    due = _consent_count(
        ctx, Consent.status.in_(ACTIVE_STATUSES), Consent.expires_at.isnot(None),
        Consent.expires_at > now, Consent.expires_at <= now + timedelta(days=30),
    )
    reminders_q = ctx.db.query(func.count(Notification.id)).filter(
        Notification.event_type == "RENEWAL_REMINDER"
    )
    if ctx.scope:
        reminders_q = reminders_q.filter(Notification.source_app == ctx.scope)
    reminders = reminders_q.scalar() or 0
    if not due:
        return no_data(
            "No consent is currently inside the 30-day renewal window for this scope, so the "
            f"denominator is empty ({reminders} reminder(s) queued historically).",
            detail={"reminders_queued": reminders, "consents_in_window": 0},
        )
    return live(
        _pct(reminders, due), numerator=reminders, denominator=due, sample_size=due,
        detail={"consents_in_window": due, "reminders_queued": reminders,
                "target": "100%",
                "note": "The reminder job runs daily and skips a consent already reminded for "
                        "this same expiry, so the ratio can exceed 100% across renewal cycles."},
    )


def k16_renewal_conversion(ctx: KpiContext) -> dict:
    reminded_q = ctx.db.query(Notification.customer_id).filter(
        Notification.event_type == "RENEWAL_REMINDER"
    )
    if ctx.scope:
        reminded_q = reminded_q.filter(Notification.source_app == ctx.scope)
    reminded = {cid for (cid,) in reminded_q.all() if cid is not None}
    if not reminded:
        return no_data("No renewal reminder has been queued for this scope, so there is nothing "
                       "to convert.")
    renewed = _consent_count(
        ctx, Consent.customer_id.in_(reminded), Consent.renewed_at.isnot(None),
    )
    return live(
        _pct(renewed, len(reminded)), numerator=renewed, denominator=len(reminded),
        sample_size=len(reminded),
        detail={"principals_reminded": len(reminded), "consents_renewed": renewed},
    )


def k17_withdrawal_rate(ctx: KpiContext) -> dict:
    withdrawn = _consent_count(
        ctx, Consent.status == "WITHDRAWN",
        Consent.withdrawn_at >= ctx.period_start, Consent.withdrawn_at <= ctx.period_end,
    )
    active = _consent_count(ctx, Consent.status.in_(ACTIVE_STATUSES))
    denominator = active + withdrawn
    if not denominator:
        return no_data("No active or withdrawn consent exists for this scope.")
    return live(
        _pct(withdrawn, denominator), numerator=withdrawn, denominator=denominator,
        sample_size=denominator,
        detail={"withdrawn_in_period": withdrawn, "active_consents": active,
                "period_days": ctx.period_days},
    )


# ---------------------------------------------------------------------------
#  Aggregated from other modules
# ---------------------------------------------------------------------------
def k08_propagation_sla(ctx: KpiContext) -> dict:
    from app.services.processors import propagation_sla_metrics

    source = ctx.memo(
        "propagation_sla",
        lambda: propagation_sla_metrics(
            ctx.db, date_from=ctx.period_start, date_to=ctx.period_end, tenant_id=ctx.tenant_id
        ),
    )
    if not source.get("ok"):
        return _source_error(source, "cease-processing propagation (K-08)")
    m = source["value"]
    if not m.get("withdrawals_total"):
        return no_data("No withdrawal occurred in this period, so no propagation SLA was engaged.")

    # K-08's percentage is met / (met + breached) - the withdrawals whose SLA
    # outcome is already DECIDED - not met / all withdrawals. Pairing the
    # percentage with the wrong denominator printed "100.0%" above "0 of 8",
    # two numbers that cannot both be true.
    #
    # And when nothing is decided the owning module returns 100.0, which is
    # the right default for an alerting threshold and a false all-clear on a
    # compliance dashboard: 8 withdrawals that reached no processor at all
    # would have been reported as 8 withdrawals propagated perfectly.
    met = m.get("fully_acknowledged_within_sla") or 0
    breached = m.get("sla_breached") or 0
    decided = met + breached
    if not decided:
        return no_data(
            "No withdrawal's propagation SLA has been decided yet: of "
            f"{m.get('withdrawals_total')} withdrawal(s), "
            f"{m.get('withdrawals_without_processors')} reached no processor and "
            f"{m.get('awaiting_acknowledgement_within_sla')} are still inside their window. The "
            "owning module returns 100% for an empty set as an alerting default; that is not a "
            "finding of full compliance and is not republished here.",
            detail=m,
        )
    return live(
        m.get("k08_propagation_sla_pct"), numerator=met, denominator=decided,
        sample_size=m.get("withdrawals_total"), detail=m,
    )


def k32_processor_contracts(ctx: KpiContext) -> dict:
    from app.services.processors import contract_coverage_report

    source = ctx.memo(
        "contract_coverage",
        lambda: contract_coverage_report(ctx.db, tenant_id=ctx.tenant_id),
    )
    if not source.get("ok"):
        return _source_error(source, "processor contract coverage (K-32)")
    m = source["value"]
    if not m.get("processors_total"):
        return no_data("No processor is registered for this scope.")
    breakdown = [
        _row("processor", p.get("name", "?"), value=100.0 if p.get("covered") else 0.0,
             unit="percent", status=LIVE, detail={"gaps": p.get("gaps", [])})
        for p in m.get("processors", [])[:50]
    ]
    return live(
        m.get("coverage_pct"), numerator=m.get("processors_covered"),
        denominator=m.get("processors_total"), sample_size=m.get("processors_total"),
        breakdown=breakdown,
        detail={k: v for k, v in m.items() if k != "processors"},
    )


def k23_grievances(ctx: KpiContext) -> dict:
    from app.services.grievance import queue_metrics

    source = ctx.memo("grievance_queue", lambda: queue_metrics(ctx.db, source_app=ctx.scope))
    if not source.get("ok"):
        return _source_error(source, "the grievance queue (K-23)")
    m = source["value"]
    if not m.get("total"):
        return no_data("No grievance has been registered for this scope.")
    breakdown = [
        _row("status", status, value=float(count), numerator=count)
        for status, count in sorted((m.get("by_status") or {}).items())
    ]
    return live(
        float(m.get("overdue") or 0), numerator=m.get("overdue"), denominator=m.get("open"),
        sample_size=m.get("total"), breakdown=breakdown,
        detail=m | {"headline": "grievances open past their published response period",
                    "target": "0 overdue"},
    )


def _children(ctx: KpiContext) -> dict:
    """R1-14's own metrics. K-24/K-25 were declared unavailable when no age
    assurance existed; the guardian module now records it, so the catalogue
    reads that module rather than restating an absence that has ended."""
    from app.services.guardian import children_metrics

    # memo() already wraps the producer in {"ok", "value"} and traps the
    # exception itself - wrapping again here nested the payload and made every
    # read a KeyError.
    return ctx.memo("children_metrics",
                    lambda: children_metrics(ctx.db, tenant_id=ctx.tenant_id))


def k24_age_assurance(ctx: KpiContext) -> dict:
    source = _children(ctx)
    if not source.get("ok"):
        return _source_error(source, "age assurance (K-24)")
    m = source["value"]["K-24"]
    total = m.get("accounts") or 0
    if not total:
        return no_data("No principal account exists in this scope to assure an age for.", detail=m)
    return live(
        float(m.get("coverage_pct") or 0.0),
        numerator=m.get("accounts_with_verified_age_assurance"), denominator=total,
        sample_size=total,
        detail=m | {"headline": "accounts with a verified age assurance",
                    "target": "100% before processing"},
    )


def k25_parental_consent(ctx: KpiContext) -> dict:
    source = _children(ctx)
    if not source.get("ok"):
        return _source_error(source, "parental consent (K-25)")
    m = source["value"]["K-25"]
    children = m.get("child_accounts") or 0
    blocks = [
        _row("enforcement", "grant blocked, no parental record",
             value=float(m.get("child_consent_blocked_no_parental_record") or 0),
             numerator=m.get("child_consent_blocked_no_parental_record")),
        _row("enforcement", "prohibited purpose blocked at recording",
             value=float(m.get("child_prohibited_processing_blocked_at_recording") or 0),
             numerator=m.get("child_prohibited_processing_blocked_at_recording")),
        _row("enforcement", "prohibited purpose denied at decision",
             value=float(m.get("child_prohibited_processing_denied_at_decision") or 0),
             numerator=m.get("child_prohibited_processing_denied_at_decision")),
    ]
    if not children:
        # The enforcement counters are still real and worth surfacing; the
        # completion ratio is not, because no child account exists to complete.
        return no_data(
            "No account in this scope is identified as a child's, so parental-consent "
            "completion has no denominator. The enforcement counters below are live.",
            detail=m,
        ) | {"breakdown": blocks}
    return live(
        float(m.get("completion_pct") or 0.0),
        numerator=m.get("child_accounts_with_verified_parental_consent"), denominator=children,
        sample_size=children, breakdown=blocks,
        detail=m | {"headline": "child accounts with a verified parental consent",
                    "target": "100%"},
    )


def _breach(ctx: KpiContext) -> dict:
    from app.services.breach import breach_metrics

    return ctx.memo("breach_metrics", lambda: breach_metrics(ctx.db, source_app=ctx.scope))


def k39_breach_detect_notify(ctx: KpiContext) -> dict:
    source = _breach(ctx)
    if not source.get("ok"):
        return _source_error(source, "the breach register (K-39)")
    m = source["value"]
    if not m.get("breaches"):
        return no_data("No personal data breach is on the register for this scope.")
    k = m.get("K-39", {})
    return live(
        k.get("mean_hours_to_first_principal_notice"), sample_size=m.get("breaches"),
        detail=k, breakdown=[
            _row("clock", "Mean time to detect", value=k.get("mean_time_to_detect_hours"), unit="hours"),
            _row("clock", "Mean detect → aware", value=k.get("mean_detect_to_aware_hours"), unit="hours"),
            _row("clock", "Mean → first principal notice",
                 value=k.get("mean_hours_to_first_principal_notice"), unit="hours"),
        ],
    )


def k40_breach_board(ctx: KpiContext) -> dict:
    source = _breach(ctx)
    if not source.get("ok"):
        return _source_error(source, "the breach register (K-40)")
    m = source["value"]
    if not m.get("breaches"):
        return no_data("No personal data breach is on the register for this scope.")
    k = m.get("K-40", {})
    if not k.get("detailed_reports_filed"):
        return no_data("No detailed Board report has been filed yet, so timeliness is not yet measurable.",
                       detail=k)
    return live(
        k.get("detailed_within_deadline_pct"),
        numerator=k.get("detailed_reports_within_deadline"),
        denominator=k.get("detailed_reports_filed"),
        sample_size=k.get("detailed_reports_filed"), detail=k,
    )


def k41_breach_principals(ctx: KpiContext) -> dict:
    source = _breach(ctx)
    if not source.get("ok"):
        return _source_error(source, "the breach register (K-41)")
    m = source["value"]
    if not m.get("breaches"):
        return no_data("No personal data breach is on the register for this scope.")
    k = m.get("K-41", {})
    if not k.get("principal_notices_generated"):
        return no_data("No principal notice has been generated for a registered breach yet.", detail=k)
    return live(
        k.get("notice_delivery_rate_pct"),
        numerator=k.get("principal_notices_delivered"),
        denominator=k.get("principal_notices_generated"),
        sample_size=k.get("affected_principals_total"), detail=k,
    )


def k18_evidence_completeness(ctx: KpiContext) -> dict:
    from app.services.kpi import evidence_completeness

    source = ctx.memo("evidence_completeness", lambda: evidence_completeness(ctx.db))
    if not source.get("ok"):
        return _source_error(source, "evidence completeness (K-18)")
    m = source["value"]
    if not m.get("total_active_consents"):
        return no_data("No active consent exists, so there is no evidence to score.")
    return live(
        m.get("evidence_completeness_pct"),
        numerator=m.get("consents_with_complete_evidence"),
        denominator=m.get("total_active_consents"),
        sample_size=m.get("total_active_consents"), detail=m,
    )


def k19_ledger_integrity(ctx: KpiContext) -> dict:
    from app.core.audit_chain import verify_chain

    source = ctx.memo("verify_chain", lambda: verify_chain(ctx.db))
    if not source.get("ok"):
        return _source_error(source, "the audit chain verification (K-19)")
    m = source["value"]
    if not m.get("checked"):
        return no_data("The audit ledger holds no hash-chained entry yet.")
    broken = m.get("broken") or []
    if ctx.tenant_id is not None:
        broken = [b for b in broken if b.get("tenant_id") == ctx.tenant_id]
        tenants = 1
    else:
        tenants = m.get("tenants") or 1
    broken_tenants = len({b.get("tenant_id") for b in broken})
    return live(
        _pct(tenants - broken_tenants, tenants), numerator=tenants - broken_tenants,
        denominator=tenants, sample_size=m.get("checked"),
        detail={"entries_checked": m.get("checked"), "chains": tenants,
                "broken_links": broken, "target": "100%, verified daily",
                "mechanism": "app/core/audit_chain.py::verify_chain — the same function the "
                             "scheduled audit_chain_verify job and the evidence pack use."},
    )


def k42_cm_availability(ctx: KpiContext) -> dict:
    m = _cm_metrics(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    if not m.get("total_calls"):
        return no_data("No Consent Manager API call has been sampled in the last 24 hours.")
    return live(
        m.get("availability_pct"), denominator=m.get("total_calls"),
        sample_size=m.get("total_calls"),
        detail={k: v for k, v in m.items() if k != "per_endpoint"} | {
            "per_endpoint": m.get("per_endpoint", []), "target": "≥ 99.9%"},
    )


def k43_record_retrieval(ctx: KpiContext) -> dict:
    m = _cm_metrics(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    p95 = m.get("record_retrieval_ms_p95")
    if p95 is None:
        return no_data("No principal record retrieval has been sampled in the last 24 hours.")
    return live(
        round(p95 / 1000, 3), sample_size=m.get("total_calls"),
        detail={"record_retrieval_ms_p95": p95, "target": "≤ 60s (First Schedule Part B 4(b))"},
    )


def _cm_metrics(ctx: KpiContext):
    from app.api.routes.consent_manager import metrics as cm_metrics

    source = ctx.memo(
        "cm_metrics",
        lambda: _as_dict(cm_metrics(window_hours=24, db=ctx.db, _user=None)),
    )
    if not source.get("ok"):
        return {"__error__": _source_error(source, "the Consent Manager API metrics (K-42/K-43)")}
    return source["value"]


def k44_notification_delivery(ctx: KpiContext) -> dict:
    m = _notification_metrics(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    if not m.get("notifications_total_attempted"):
        return no_data("No notification has been dispatched yet, so there is no delivery rate.")
    breakdown = [
        _row("channel", channel, value=_pct(c["delivered"], c["attempted"]),
             numerator=c["delivered"], denominator=c["attempted"], unit="percent",
             status=LIVE if c["attempted"] else NO_DATA)
        for channel, c in sorted((m.get("by_channel") or {}).items())
    ]
    return live(
        m.get("notification_delivery_rate_pct"),
        numerator=m.get("notifications_delivered"),
        denominator=m.get("notifications_total_attempted"),
        sample_size=m.get("notifications_total_attempted"),
        breakdown=breakdown, detail={k: v for k, v in m.items() if k != "by_channel"},
    )


def k45_notification_ack(ctx: KpiContext) -> dict:
    m = _notification_metrics(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    if not m.get("notifications_delivered"):
        return no_data("No notification has been delivered yet, so none could be acknowledged.")
    return live(
        m.get("notification_ack_rate_pct"),
        numerator=m.get("notifications_acknowledged"),
        denominator=m.get("notifications_delivered"),
        sample_size=m.get("notifications_delivered"),
        detail={k: v for k, v in m.items() if k != "by_channel"},
    )


def _notification_metrics(ctx: KpiContext):
    from app.services.kpi import notification_delivery_metrics

    source = ctx.memo("notification_metrics", lambda: notification_delivery_metrics(ctx.db))
    if not source.get("ok"):
        return {"__error__": _source_error(source, "notification delivery (K-44/K-45)")}
    return source["value"]


def k10_lawful_gateway(ctx: KpiContext) -> dict:
    from app.api.routes.purposes import coverage_report

    source = ctx.memo("coverage_report", lambda: _as_dict(coverage_report(db=ctx.db, _=None)))
    if not source.get("ok"):
        return _source_error(source, "lawful-gateway coverage (K-10)")
    m = source["value"]
    if not m.get("total_activities"):
        return no_data("No active processing activity is registered.")
    breakdown = [
        _row("activity", f"{a.get('code')} — {a.get('name')}",
             value=100.0 if a.get("covered") else 0.0, unit="percent",
             detail={"gateways": a.get("gateways", []), "purpose_codes": a.get("purpose_codes", [])})
        for a in m.get("activities", [])[:100]
    ]
    return live(
        m.get("coverage_pct"), numerator=m.get("covered_activities"),
        denominator=m.get("total_activities"), sample_size=m.get("total_activities"),
        breakdown=breakdown,
        detail={k: v for k, v in m.items() if k != "activities"} | {"target": "100%"},
    )


def _retention(ctx: KpiContext) -> dict:
    from app.services.retention import retention_scan

    # audit=False: this is a dashboard read, and the audit ledger is
    # append-only. A KPI page that writes a RETENTION_SCAN_RUN row on every
    # refresh would bury the real enforcement runs an auditor is looking for.
    return ctx.memo("retention_scan", lambda: retention_scan(ctx.db, audit=False))


def k33_log_retention(ctx: KpiContext) -> dict:
    source = _retention(ctx)
    if not source.get("ok"):
        return _source_error(source, "the retention schedule (K-33)")
    m = source["value"]
    classes = m.get("classes") or []
    if not classes:
        return no_data("No record class is registered in the retention schedule.")
    meeting = sum(1 for c in classes if c.get("schedule_meets_floor"))
    breakdown = [
        _row("record_class", c.get("label") or c.get("record_class"),
             value=100.0 if c.get("schedule_meets_floor") else 0.0, unit="percent",
             detail={"floor_days": c.get("effective_floor_days"),
                     "configured_retention_days": c.get("configured_retention_days"),
                     "enforcement": c.get("enforcement")})
        for c in classes
    ]
    return live(
        _pct(meeting, len(classes)), numerator=meeting, denominator=len(classes),
        sample_size=len(classes), breakdown=breakdown,
        detail={"classes_scanned": len(classes),
                "deleted_before_floor": m.get("deleted_before_floor"),
                "violations": len(m.get("violations") or []),
                "target": "100%"},
    )


def k34_encryption(ctx: KpiContext) -> dict:
    from app.core.database import Base
    from app.core.encryption import (
        EncryptedJSON,
        EncryptedString,
        EncryptedText,
        current_key_fingerprint,
        is_encryption_enabled,
    )

    encrypted_types = (EncryptedString, EncryptedText, EncryptedJSON)
    encrypted_columns = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, encrypted_types)
    ]
    enabled = is_encryption_enabled()
    return partial(
        100.0 if enabled else 0.0,
        (
            "Reports whether field-level encryption is active and how many columns are declared "
            "with an encrypting type. It is NOT the KPI's own denominator: this build has no "
            "register classifying which columns hold personal data, and it records no key "
            "creation date, so 'key age' and 'days since rotation' cannot be answered at all."
        ),
        numerator=len(encrypted_columns) if enabled else 0,
        denominator=len(encrypted_columns), sample_size=len(encrypted_columns),
        detail={
            "encryption_enabled": enabled,
            "active_key_fingerprint": current_key_fingerprint(),
            "encrypted_columns": len(encrypted_columns),
            "key_age_days": None,
            "days_since_rotation": None,
            "target": "100%; rotation ≤ 12 months",
        },
    )


def _audit_event_counts(ctx: KpiContext, events: tuple[str, ...]) -> dict[str, int]:
    q = ctx.db.query(AuditLog.event, func.count(AuditLog.id)).filter(
        AuditLog.event.in_(events),
        AuditLog.created_at >= ctx.period_start, AuditLog.created_at <= ctx.period_end,
    )
    if ctx.scope:
        q = q.filter(AuditLog.source_app == ctx.scope)
    return {event: count for event, count in q.group_by(AuditLog.event).all()}


def _rights(ctx: KpiContext) -> dict:
    """R2-05's own metrics, computed once per request even though three KPIs
    read from it. This module republishes those numbers; it does not
    recompute them - see the module docstring."""
    from app.services.rights_requests import queue_metrics

    return ctx.memo("rights_queue", lambda: queue_metrics(ctx.db, source_app=ctx.scope))


# The audit events R2-05's register does NOT cover. A withdrawal is a consent
# operation with its own lifecycle and an objection has its own register, so
# neither is a rights *request* - but both are a data principal exercising a
# right, and K-20's drill-down reports them alongside the register so the
# screen does not imply that nobody exercised anything outside the DSAR desk.
_ADJACENT_RIGHTS_EVENTS = ("OBJECTION_RECORDED", "CONSENT_WITHDRAWN")


def k20_rights_requests(ctx: KpiContext) -> dict:
    """Access / correction / erasure / nomination counts, straight off the
    rights-request register (`rights_requests`).

    Before R2-05 landed this was assembled from audit events, which could see
    an access *export* but never a correction or a nomination request, and
    could not distinguish a request that was made from one that was answered.
    The register is now the owner and this reads it.
    """
    source = _rights(ctx)
    if not source.get("ok"):
        return _source_error(source, "the rights-request register (K-20)")
    m = source["value"]
    if not m.get("total"):
        return no_data("No data-principal rights request has been registered for this scope.")
    by_type = m.get("by_type") or {}
    labels = {
        "ACCESS": "Access (s.11)",
        "CORRECTION": "Correction (s.12(1))",
        "ERASURE": "Erasure (s.12(3))",
        "NOMINATION": "Nomination (s.14)",
    }
    breakdown = [
        _row("right", labels.get(key, key), value=float(count), numerator=count)
        for key, count in sorted(by_type.items(), key=lambda kv: -kv[1])
    ]
    adjacent = _audit_event_counts(ctx, _ADJACENT_RIGHTS_EVENTS)
    breakdown.extend(
        _row("adjacent", {"OBJECTION_RECORDED": "Objection (s.7(a))",
                          "CONSENT_WITHDRAWN": "Withdrawal (s.6(4))"}.get(event, event),
             value=float(count), numerator=count)
        for event, count in sorted(adjacent.items(), key=lambda kv: -kv[1])
    )
    return live(
        float(m["total"]), numerator=m["total"], sample_size=m["total"],
        breakdown=breakdown,
        detail={
            "by_type": by_type,
            "by_status": m.get("by_status") or {},
            "open": m.get("open"),
            "overdue": m.get("overdue"),
            "awaiting_verification": m.get("awaiting_verification"),
            "adjacent_rights_events": adjacent,
        },
    )


def k21_rights_acknowledgement(ctx: KpiContext) -> dict:
    """Median hours from receipt to acknowledgement.

    The median rather than the mean: one postal request back-entered a
    fortnight after it arrived would otherwise swamp a thousand instant portal
    acknowledgements and make a healthy desk look broken.
    """
    source = _rights(ctx)
    if not source.get("ok"):
        return _source_error(source, "the rights-request register (K-21)")
    m = source["value"]
    if not m.get("total"):
        return no_data("No data-principal rights request has been registered for this scope.")
    if not m.get("acknowledged_total"):
        return no_data(
            "No registered rights request has been acknowledged yet, so there is no "
            "acknowledgement interval to measure.",
            detail={"total": m.get("total")},
        )
    return live(
        m.get("median_acknowledgement_hours"),
        numerator=m.get("acknowledged_within_commitment"),
        denominator=m.get("acknowledged_total"),
        sample_size=m.get("acknowledged_total"),
        breakdown=[
            _row("clock", "Acknowledged within the published commitment",
                 value=_pct(m.get("acknowledged_within_commitment") or 0,
                            m.get("acknowledged_total") or 0),
                 numerator=m.get("acknowledged_within_commitment"),
                 denominator=m.get("acknowledged_total"), unit="percent"),
            _row("clock", "Median hours to acknowledge",
                 value=m.get("median_acknowledgement_hours"), unit="hours"),
        ],
        detail={
            "acknowledged_total": m.get("acknowledged_total"),
            "acknowledged_within_commitment": m.get("acknowledged_within_commitment"),
            "median_acknowledgement_hours": m.get("median_acknowledgement_hours"),
            "headline": "median hours from receipt to acknowledgement",
        },
    )


def k22_rights_turnaround(ctx: KpiContext) -> dict:
    """% of rights requests closed within the period published to the
    principal, measured request by request against that request's own
    snapshotted `due_at` - not against today's configuration."""
    source = _rights(ctx)
    if not source.get("ok"):
        return _source_error(source, "the rights-request register (K-22)")
    m = source["value"]
    if not m.get("total"):
        return no_data("No data-principal rights request has been registered for this scope.")
    if not m.get("closed_total"):
        return no_data(
            "No registered rights request has been closed yet, so turnaround is not yet "
            "measurable.",
            detail={"open": m.get("open"), "overdue": m.get("overdue")},
        )
    return live(
        m.get("on_time_closure_rate"),
        numerator=m.get("closed_within_period"),
        denominator=m.get("closed_total"),
        sample_size=m.get("closed_total"),
        breakdown=[
            _row("outcome", "Fulfilled", value=float(m.get("fulfilled_total") or 0),
                 numerator=m.get("fulfilled_total")),
            _row("outcome", "Refused", value=float(m.get("rejected_total") or 0),
                 numerator=m.get("rejected_total")),
            _row("clock", "Still open past the published period",
                 value=float(m.get("overdue") or 0), numerator=m.get("overdue"),
                 denominator=m.get("open")),
        ],
        detail={
            "closed_total": m.get("closed_total"),
            "closed_within_period": m.get("closed_within_period"),
            "average_days_to_closure": m.get("average_days_to_closure"),
            "by_rejection_reason": m.get("by_rejection_reason") or {},
            "overdue": m.get("overdue"),
            "target": "100% within the published period (<= 90 days)",
        },
    )


def k35_auth_hygiene(ctx: KpiContext) -> dict:
    counts = _audit_event_counts(ctx, ("LOGIN", "LOGIN_FAILED"))
    logins = counts.get("LOGIN", 0)
    failed = counts.get("LOGIN_FAILED", 0)
    if not (logins or failed):
        return no_data("No authentication event falls in this period for this scope.")
    return partial(
        _pct(failed, logins + failed),
        (
            "Failed-login rate only. MFA enrolment and stale-session counts are the other two "
            "thirds of this KPI and neither is recorded in this build (H-02)."
        ),
        numerator=failed, denominator=logins + failed, sample_size=logins + failed,
        breakdown=[
            _row("event", "Successful logins", value=float(logins), numerator=logins),
            _row("event", "Failed logins", value=float(failed), numerator=failed),
        ],
        detail={"logins": logins, "failed_logins": failed, "mfa_enrolment_pct": None,
                "stale_sessions": None},
    )


def k36_pii_access(ctx: KpiContext) -> dict:
    events = ("CONSENT_VIEWED", "CONSENT_TEXT_VIEWED", "CONSENT_EVIDENCE_VIEWED",
              "PRINCIPAL_RECORD_VIEWED", "PRINCIPAL_RECORD_EXPORTED", "AUDIT_EXPORTED")
    counts = _audit_event_counts(ctx, events)
    total = sum(counts.values())
    if not total:
        return no_data("No personal-data read or export event falls in this period for this scope.")
    actor_q = ctx.db.query(AuditLog.actor_username, func.count(AuditLog.id)).filter(
        AuditLog.event.in_(events),
        AuditLog.created_at >= ctx.period_start, AuditLog.created_at <= ctx.period_end,
    )
    if ctx.scope:
        actor_q = actor_q.filter(AuditLog.source_app == ctx.scope)
    actors = actor_q.group_by(AuditLog.actor_username).order_by(func.count(AuditLog.id).desc()).limit(20).all()
    return partial(
        float(total),
        (
            "Counts of personal-data read and export events per actor, from the audit ledger. "
            "The 'anomalies flagged' half of this KPI needs a baseline and an alerting rule "
            "(H-05), neither of which exists here, so nothing below is marked anomalous."
        ),
        numerator=total, sample_size=total,
        breakdown=[
            _row("actor", actor or "(unattributed)", value=float(count), numerator=count)
            for actor, count in actors
        ],
        detail=counts | {"period_days": ctx.period_days},
    )


_ERASURE_MISSING = (
    "The retention and erasure engine (R1-06 / G-01..G-04) is not present in this build, so "
    "there is no per-record retention clock, no legal-hold flag and no erasure job to measure."
)


def _erasure(ctx: KpiContext):
    """R1-06's own metrics function. Imported lazily and by name so that a
    build without the erasure engine degrades to a stated UNAVAILABLE rather
    than an import error at module load - and so that the day it lands, these
    four KPIs start answering without anyone editing this file. The converse
    of the evidence pack's bug: a KPI must not keep reporting UNAVAILABLE
    after its register exists."""
    try:
        from app.services.erasure import erasure_metrics
    except ImportError:
        return {"__error__": unavailable(_ERASURE_MISSING, "R1-06 (retention and erasure engine)")}
    source = ctx.memo("erasure_metrics", lambda: erasure_metrics(ctx.db))
    if not source.get("ok"):
        return {"__error__": _source_error(source, "the erasure engine (K-28..K-31)")}
    return source["value"]


def k28_records_past_retention(ctx: KpiContext) -> dict:
    m = _erasure(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    backlog = m.get("erasure_backlog")
    return live(
        float(backlog or 0), numerator=backlog, sample_size=m.get("jobs_executed"),
        breakdown=[
            _row("status", status, value=float(count), numerator=count)
            for status, count in sorted((m.get("backlog_by_status") or {}).items())
        ],
        detail={"erasure_backlog": backlog, "backlog_by_status": m.get("backlog_by_status"),
                "legal_holds_active": m.get("legal_holds_active"), "target": "0",
                "note": "Records whose retention ceiling has passed and whose erasure is still "
                        "in flight. A record under an active legal hold is not counted as "
                        "overdue - the hold outranks the ceiling."},
    )


def k29_erasure_turnaround(ctx: KpiContext) -> dict:
    m = _erasure(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    if not m.get("jobs_executed"):
        return no_data("No erasure job has been executed yet, so there is no turnaround to measure.")
    median_hours = m.get("median_tat_hours")
    if median_hours is None:
        return no_data("Erasure jobs have executed but none carries both a proposal and an "
                       "execution timestamp, so no turnaround is measurable.")
    return live(
        round(median_hours / 24, 2), sample_size=m.get("jobs_executed"),
        detail={"median_tat_hours": median_hours, "jobs_executed": m.get("jobs_executed"),
                "target": "≤ policy"},
    )


def k30_pre_erasure_notices(ctx: KpiContext) -> dict:
    m = _erasure(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    executed = m.get("jobs_executed") or 0
    if not executed:
        # The upstream function returns 100.0 for an empty set, which is the
        # right default for an alerting threshold and the wrong one for a
        # compliance dashboard: "no erasure has happened" is not "every
        # erasure was noticed correctly".
        return no_data(
            "No erasure has been executed yet, so no pre-erasure notice was due. The owning "
            "module reports 100% for an empty set as an alerting default; that is not a finding "
            "of full compliance and is not republished here."
        )
    return live(
        m.get("notice_compliance_pct"),
        numerator=m.get("executed_with_compliant_notice"), denominator=executed,
        sample_size=executed,
        detail={"executed_with_compliant_notice": m.get("executed_with_compliant_notice"),
                "jobs_executed": executed, "target": "100% (law: ≥48h, R.8(2))"},
    )


def k31_inactivity_breaches(ctx: KpiContext) -> dict:
    m = _erasure(ctx)
    if isinstance(m, dict) and m.get("__error__"):
        return m["__error__"]
    breaches = m.get("inactivity_clock_breaches")
    if breaches is None:
        return no_data("The inactivity scan produced no result.")
    return live(
        float(breaches), numerator=breaches,
        detail={"inactivity_clock_breaches": breaches,
                "legal_holds_active": m.get("legal_holds_active"), "target": "0",
                "note": "Principals past the Third Schedule period (R.8(1)) with neither an "
                        "erasure in flight nor a legal hold."},
    )


_RECONSENT_MISSING = (
    "The material-change / re-consent engine (R1-09 / P-01) is not present in this build, so no "
    "consent carries a 're-consent required' state and K-09 has no denominator."
)


def k09_reconsent_rate(ctx: KpiContext) -> dict:
    """R1-09's own metrics function. Imported lazily by name so a build
    without the re-consent engine degrades to a stated UNAVAILABLE instead of
    failing to import - and so the day it lands, this KPI starts answering."""
    try:
        from app.services.material_change import re_consent_metrics
    except ImportError:
        return unavailable(_RECONSENT_MISSING, "P-01 (re-consent state on a material change)")
    source = ctx.memo("re_consent_metrics", lambda: re_consent_metrics(ctx.db))
    if not source.get("ok"):
        return _source_error(source, "the re-consent engine (K-09)")
    m = source["value"]
    if not m.get("consents_flagged"):
        return no_data(
            "No material change has flagged a consent for re-consent, so there is nothing to "
            "re-consent to. The engine is present and running; the denominator is empty.",
            detail={k: v for k, v in m.items() if k != "generated_at"},
        )
    return live(
        m.get("re_consent_rate_pct"),
        numerator=m.get("fresh_consents"), denominator=m.get("consents_flagged"),
        sample_size=m.get("campaigns"),
        breakdown=[
            _row("state", "Consents still blocked awaiting re-consent",
                 value=float(m.get("consents_blocked_now") or 0),
                 numerator=m.get("consents_blocked_now")),
            _row("state", "Material changes published",
                 value=float(m.get("material_changes") or 0), numerator=m.get("material_changes")),
        ],
        detail={k: v for k, v in m.items() if k != "generated_at"},
    )


def k27_dpo_contact(ctx: KpiContext) -> dict:
    q = ctx.db.query(Organization).filter(Organization.is_active.is_(True))
    if ctx.tenant_id is not None:
        q = q.filter(Organization.id == ctx.tenant_id)
    organizations = q.all()
    if not organizations:
        return no_data("No active tenant is registered for this scope.")
    with_contact = [o for o in organizations if (o.dpo_email or o.dpo_phone)]
    return partial(
        _pct(len(with_contact), len(organizations)),
        (
            "Measures whether each tenant has a DPO contact on file, which is what the public "
            "notice and rights endpoints render from. It does not verify that every notice "
            "actually delivered and every rights response actually sent carried it — that would "
            "need per-delivery capture (A-05/J-01)."
        ),
        numerator=len(with_contact), denominator=len(organizations),
        sample_size=len(organizations),
        breakdown=[
            _row("tenant", o.code, value=100.0 if (o.dpo_email or o.dpo_phone) else 0.0,
                 unit="percent",
                 detail={"dpo_name": o.dpo_name or None, "has_email": bool(o.dpo_email),
                         "has_phone": bool(o.dpo_phone)})
            for o in organizations
        ],
        detail={"target": "100%"},
    )


def k26_legacy_notice(ctx: KpiContext) -> dict:
    """R2-11/A-08: pre-Act consents notified / total pre-Act consents.

    Aggregates `app/services/legacy_notice.py::legacy_notice_metrics` rather
    than recomputing the cohort here - the definition of "pre-Act" is one
    query (`coalesce(renewed_at, granted_at, created_at) < cutoff`) and it
    lives with the campaign that acts on it, so a screen and a KPI can never
    disagree about who was owed a notice.

    The cut-off is that module's engineering default, not a per-request
    parameter: a KPI whose denominator moved with whatever date the caller
    passed would not be a metric. `detail.cutoff` says which date was used so
    the number is checkable, and the coverage note says why this is PARTIAL:
    the numerator counts a notice that *reached* the principal, which under
    the current transport means the mail server accepted it, not that she
    read it. `principals_acknowledged` is the honest floor for that.
    """
    from app.services import legacy_notice as legacy_notice_service

    source = ctx.memo(
        "legacy_notice_metrics",
        lambda: legacy_notice_service.legacy_notice_metrics(ctx.db, source_app=ctx.scope),
    )
    if not source.get("ok"):
        return _source_error(source, "the legacy-notice campaign (K-26)")
    source = source["value"]
    total = source["pre_act_consents"]
    if not total:
        return no_data(
            "No consent in this scope was last affirmed before "
            f"{source['cutoff']}, so there is no s.5(2) cohort to notify — which is not the "
            "same as having notified everybody.",
            detail={"cutoff": source["cutoff"], "campaigns": source["campaigns"]},
        )
    return partial(
        source["legacy_notice_delivery_pct"],
        (
            "Counts a consent as notified when its principal has a LEGACY_NOTICE that reached "
            "her (SENT/DELIVERED/ACKNOWLEDGED). The interim transport reports no delivery "
            "callback, so 'reached' means the channel accepted the message, not that she opened "
            "it; `principals_acknowledged` is the number who confirmed it in the portal."
        ),
        numerator=source["consents_notified"], denominator=total, sample_size=total,
        detail={
            "cutoff": source["cutoff"],
            "pre_act_principals": source["pre_act_principals"],
            "principals_notified": source["principals_notified"],
            "principals_acknowledged": source["principals_acknowledged"],
            "campaigns": source["campaigns"],
            "target": "100%",
        },
    )


# ---------------------------------------------------------------------------
#  Small shims for reading a route function directly
# ---------------------------------------------------------------------------
class _FakeScopedUser:
    """`app/api/routes/decisions.py::decision_report` derives its tenant
    filter from `get_org_scope(user)`, which reads `user.role.name`. Calling
    the route function directly - rather than reimplementing its query, which
    is the divergence this whole module exists to avoid - means handing it
    something with that shape. It carries a role name and nothing else; it is
    never persisted and authorises nothing (the caller was already authorised
    by the route that built the KpiContext)."""

    class _Role:
        def __init__(self, name: str):
            self.name = name

    def __init__(self, scope: Optional[str]):
        from app.core.rbac import ORG_SCOPE_MAP

        role_name = next((r for r, s in ORG_SCOPE_MAP.items() if s == scope), "")
        self.role = self._Role(role_name)


def _as_dict(value) -> dict:
    """Route functions return Pydantic models; every caller here wants a
    plain dict."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


# ---------------------------------------------------------------------------
#  The catalogue
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KpiSpec:
    id: str
    name: str
    formula: str
    domain: str
    unit: str
    target: str
    statutory_ref: str
    scope_support: str = TENANT
    computed_by: Optional[str] = None
    supports_trend: bool = False
    supports_breakdown: bool = False
    compute: Optional[Callable[[KpiContext], dict]] = None
    # UNAVAILABLE specs: why, and what would unblock them.
    unavailable_reason: Optional[str] = None
    unblocked_by: Optional[str] = None


D_QUALITY = "Consent quality"
D_LIFECYCLE = "Consent lifecycle"
D_COVERAGE = "Coverage and lawful basis"
D_EVIDENCE = "Evidence and integrity"
D_RIGHTS = "Rights and grievances"
D_CHILDREN = "Children"
D_RETENTION = "Retention and erasure"
D_PROCESSORS = "Processors and sharing"
D_SECURITY = "Security"
D_BREACH = "Breach"
D_NOTIFY = "Notifications"
D_CM = "Consent Manager"


CATALOGUE: tuple[KpiSpec, ...] = (
    KpiSpec("K-01", "Opt-in rate per purpose",
            "opt-ins ÷ (opt-ins + opt-outs) per purpose (decision-based, preferred); "
            "opt-ins ÷ notices displayed carried alongside",
            D_QUALITY, "percent", "trend; no target", "s.6(1)",
            computed_by="app/services/kpi_catalogue.py + app/services/banner_events.py",
            supports_trend=True, supports_breakdown=True, compute=k01_optin_rate),
    KpiSpec("K-02", "Decline / reject-all rate and no-choice rate",
            "reject-all ÷ decisions; (notices − decisions) ÷ notices",
            D_QUALITY, "percent", "must be measurable", 's.6(1) "free"',
            computed_by="app/services/banner_events.py",
            supports_trend=True, supports_breakdown=True, compute=k02_reject_and_no_choice),
    KpiSpec("K-03", "Partial-consent rate",
            "granular decisions granting other than everything offered ÷ sessions with a decision",
            D_QUALITY, "percent", "—", 's.6(1) "specific"',
            computed_by="app/services/banner_events.py",
            supports_trend=True, compute=k03_partial_consent),
    KpiSpec("K-04", "Time-to-decision",
            "median seconds from notice shown to affirmative act, measured between two "
            "server-stamped rows",
            D_QUALITY, "seconds", "—", "s.6(10) evidence",
            computed_by="app/services/banner_events.py",
            supports_trend=True, compute=k04_time_to_decision),
    KpiSpec("K-05", "Notice-version acceptance distribution",
            "active consents by the notice version they pinned",
            D_EVIDENCE, "percent", "100% of active consents map to a retained notice version",
            "s.6(10), Part B 3(b)",
            computed_by="app/services/kpi_catalogue.py",
            supports_breakdown=True, compute=k05_notice_version_distribution),
    KpiSpec("K-06", "Language distribution",
            "consent evidence by the language the notice was shown in",
            D_QUALITY, "count", "all 23 offered", "s.5(3), s.6(3)",
            computed_by="app/services/kpi_catalogue.py",
            supports_breakdown=True, compute=k06_language_distribution),
    KpiSpec("K-07", "Withdrawal ease parity",
            "steps/clicks to withdraw ÷ steps to grant",
            D_QUALITY, "ratio", "≤ 1.0", "s.6(4), R.3(c)(i)",
            unavailable_reason=(
                "Nothing records how many steps a withdrawal took. Banner events record that a "
                "decision happened, not the interaction path that produced it."),
            unblocked_by="UI step instrumentation on the grant and withdraw flows"),
    KpiSpec("K-08", "Cease-processing propagation SLA",
            "% withdrawals acknowledged by every processor within the configured SLA",
            D_PROCESSORS, "percent", "100%", "s.6(6)",
            computed_by="app/services/processors.py::propagation_sla_metrics",
            supports_breakdown=True, compute=k08_propagation_sla),
    KpiSpec("K-09", "Re-consent rate after material change",
            'fresh consents ÷ consents flagged "re-consent required"',
            D_LIFECYCLE, "percent", "trend", "s.6(1), BRD §4.1.3", scope_support=PLATFORM_ONLY,
            computed_by="app/services/material_change.py::re_consent_metrics",
            supports_breakdown=True, compute=k09_reconsent_rate),
    KpiSpec("K-10", "Lawful-gateway coverage",
            "processing activities with consent or an s.7 clause ÷ all activities",
            D_COVERAGE, "percent", "100%", "s.4", scope_support=PLATFORM_ONLY,
            computed_by="app/api/routes/purposes.py::coverage_report",
            supports_breakdown=True, compute=k10_lawful_gateway),
    KpiSpec("K-11", "Consent coverage of processing",
            "distinct (principal, purpose) pairs with an active consent ÷ "
            "(customers × consent-requiring purposes)",
            D_COVERAGE, "percent", "trend", "s.4",
            computed_by="app/services/kpi_catalogue.py",
            supports_trend=True, compute=k11_coverage),
    KpiSpec("K-12", "Decision-engine outcomes",
            "count of ALLOW / DENY / REQUIRE_CONSENT / WITHDRAWN / EXPIRED per period",
            D_COVERAGE, "count", "—", "BRD §4.1.2",
            computed_by="app/api/routes/decisions.py::decision_report",
            supports_trend=True, supports_breakdown=True, compute=k12_decision_outcomes),
    KpiSpec("K-13", "Consent freshness",
            "age distribution of active consents; share still inside their own validity",
            D_LIFECYCLE, "percent", "—", "BRD §4.1.4",
            computed_by="app/services/kpi_catalogue.py",
            supports_breakdown=True, compute=k13_freshness),
    KpiSpec("K-14", "Expiring ≤30 days / expired", "counts",
            D_LIFECYCLE, "count", "—", "BRD §4.1.4",
            computed_by="app/services/kpi_catalogue.py",
            supports_trend=True, supports_breakdown=True, compute=k14_expiring),
    KpiSpec("K-15", "Renewal reminder delivery",
            "reminders queued ÷ consents expiring in the 30-day window",
            D_LIFECYCLE, "percent", "100%", "BRD §4.1.4",
            computed_by="app/services/kpi_catalogue.py", compute=k15_renewal_reminders),
    KpiSpec("K-16", "Renewal conversion", "principals renewed ÷ principals reminded",
            D_LIFECYCLE, "percent", "trend", "BRD §4.1.4",
            computed_by="app/services/kpi_catalogue.py", compute=k16_renewal_conversion),
    KpiSpec("K-17", "Withdrawal rate", "withdrawals in period ÷ (active + withdrawn)",
            D_LIFECYCLE, "percent", "trend", "s.6(4)",
            computed_by="app/services/kpi_catalogue.py",
            supports_trend=True, compute=k17_withdrawal_rate),
    KpiSpec("K-18", "Evidence completeness",
            "% consent events with notice hash, content hash and any required affirmative reference",
            D_EVIDENCE, "percent", "100%", "s.6(10)", scope_support=PLATFORM_ONLY,
            computed_by="app/services/kpi.py::evidence_completeness",
            supports_trend=True, compute=k18_evidence_completeness),
    KpiSpec("K-19", "Ledger integrity check",
            "% audit hash-chains verifying intact",
            D_EVIDENCE, "percent", "100%, daily", "BRD §4.7.1",
            computed_by="app/core/audit_chain.py::verify_chain", compute=k19_ledger_integrity),
    KpiSpec("K-20", "Rights requests by type",
            "access / correction / erasure / nomination counts",
            D_RIGHTS, "count", "—", "ss.11–14",
            computed_by="app/services/rights_requests.py::queue_metrics",
            supports_breakdown=True, compute=k20_rights_requests),
    KpiSpec("K-21", "Rights request acknowledgement time", "median hours to acknowledge",
            D_RIGHTS, "hours", "≤ 72h (policy)", "R.14",
            computed_by="app/services/rights_requests.py::queue_metrics",
            supports_breakdown=True, compute=k21_rights_acknowledgement),
    KpiSpec("K-22", "Rights request turnaround",
            "% closed within the published period",
            D_RIGHTS, "percent", "100% within ≤90 days (law)", "s.13(2), R.14(3)",
            computed_by="app/services/rights_requests.py::queue_metrics",
            supports_breakdown=True, compute=k22_rights_turnaround),
    KpiSpec("K-23", "Grievance backlog and escalations",
            "grievances open past their published response period; escalated to the DPO",
            D_RIGHTS, "count", "0 overdue", "s.13, R.14(3)",
            computed_by="app/services/grievance.py::queue_metrics",
            supports_breakdown=True, compute=k23_grievances),
    KpiSpec("K-24", "Age-assurance coverage", "accounts with verified age ÷ all accounts",
            D_CHILDREN, "percent", "100% before processing", "s.9(1)",
            computed_by="app/services/guardian.py::children_metrics",
            supports_breakdown=False, compute=k24_age_assurance),
    KpiSpec("K-25", "Parental-consent completion and child-purpose blocks",
            "verified parental consents ÷ child accounts; count of auto-denied tracking purposes",
            D_CHILDREN, "percent", "100% / all", "s.9, R.10",
            computed_by="app/services/guardian.py::children_metrics",
            supports_breakdown=True, compute=k25_parental_consent),
    KpiSpec("K-26", "Legacy-notice delivery", "pre-Act consents notified ÷ total",
            D_COVERAGE, "percent", "100%", "s.5(2)",
            computed_by="app/services/legacy_notice.py::legacy_notice_metrics",
            supports_breakdown=False, compute=k26_legacy_notice),
    KpiSpec("K-27", "DPO contact present",
            "% notices and rights responses carrying the DPO contact",
            D_RIGHTS, "percent", "100%", "s.6(3), R.9",
            computed_by="app/services/kpi_catalogue.py",
            supports_breakdown=True, compute=k27_dpo_contact),
    KpiSpec("K-28", "Records past retention",
            "erasure jobs due or in flight whose retention ceiling has passed, excluding "
            "anything under an active legal hold",
            D_RETENTION, "count", "0", "s.8(7)", scope_support=PLATFORM_ONLY,
            computed_by="app/services/erasure.py::erasure_metrics",
            supports_breakdown=True, compute=k28_records_past_retention),
    KpiSpec("K-29", "Erasure turnaround",
            "median days from an erasure becoming due to its execution",
            D_RETENTION, "days", "≤ policy", "s.8(7), s.12(3)", scope_support=PLATFORM_ONLY,
            computed_by="app/services/erasure.py::erasure_metrics", compute=k29_erasure_turnaround),
    KpiSpec("K-30", "Pre-erasure notices sent", "notices ÷ executed erasures, sent ≥48h before",
            D_RETENTION, "percent", "100% (law: ≥48h)", "R.8(2)", scope_support=PLATFORM_ONLY,
            computed_by="app/services/erasure.py::erasure_metrics", compute=k30_pre_erasure_notices),
    KpiSpec("K-31", "Inactivity-clock breaches",
            "principals past the Third-Schedule period without erasure or legal hold",
            D_RETENTION, "count", "0", "R.8(1)", scope_support=PLATFORM_ONLY,
            computed_by="app/services/erasure.py::erasure_metrics", compute=k31_inactivity_breaches),
    KpiSpec("K-32", "Processor contract coverage", "processors with a valid contract ÷ all processors",
            D_PROCESSORS, "percent", "100%", "s.8(2), R.6(1)(f)",
            computed_by="app/services/processors.py::contract_coverage_report",
            supports_breakdown=True, compute=k32_processor_contracts),
    KpiSpec("K-33", "Audit/log retention compliance",
            "% record classes whose configured schedule meets its statutory retention floor",
            D_RETENTION, "percent", "100%", "R.6(1)(e), R.8(3), Part B 4(c)",
            scope_support=PLATFORM_ONLY,
            computed_by="app/services/retention.py::retention_scan",
            supports_breakdown=True, compute=k33_log_retention),
    KpiSpec("K-34", "Encryption coverage and key age",
            "% PII columns encrypted; key age; days since rotation",
            D_SECURITY, "percent", "100%; rotation ≤ 12 months", "R.6(1)(a)",
            scope_support=PLATFORM_ONLY,
            computed_by="app/core/encryption.py", compute=k34_encryption),
    KpiSpec("K-35", "Authentication hygiene",
            "failed-login rate, MFA enrolment %, stale sessions",
            D_SECURITY, "percent", "MFA 100% staff", "R.6(1)(b)",
            computed_by="app/services/kpi_catalogue.py (audit ledger)",
            supports_breakdown=True, compute=k35_auth_hygiene),
    KpiSpec("K-36", "Privileged / PII access events",
            "personal-data reads and exports per actor per day",
            D_SECURITY, "count", "reviewed weekly", "R.6(1)(c)",
            computed_by="app/services/kpi_catalogue.py (audit ledger)",
            supports_breakdown=True, compute=k36_pii_access),
    KpiSpec("K-37", "Backup success and restore-test recency",
            "% successful backups; days since last restore drill",
            D_SECURITY, "percent", "100%; ≤ 90 days", "R.6(1)(d)",
            unavailable_reason=(
                "Backups run outside this application and no drill result is recorded in it. "
                "`tests/restore_drill.py` is an operator script whose outcome is never persisted."),
            unblocked_by="H-06 (backup/restore drill register)"),
    KpiSpec("K-38", "Open security findings by severity", "vulnerability tracker",
            D_SECURITY, "count", "0 critical", "R.6(1)(g)",
            unavailable_reason="No vulnerability tracker is integrated with this platform.",
            unblocked_by="H-09 (security finding register)"),
    KpiSpec("K-39", "Breach time-to-detect / time-to-notify principals", "hours",
            D_BREACH, "hours", '"without delay" (law)', "R.7(1)",
            computed_by="app/services/breach.py::breach_metrics",
            supports_breakdown=True, compute=k39_breach_detect_notify),
    KpiSpec("K-40", "Breach Board-notification timeliness",
            "initial: hours; detailed report: ≤ 72h",
            D_BREACH, "percent", "100% within 72h (law)", "R.7(2)",
            computed_by="app/services/breach.py::breach_metrics", compute=k40_breach_board),
    KpiSpec("K-41", "Affected principals per breach and notice delivery rate", "counts",
            D_BREACH, "percent", "100% delivered", "R.7(1)",
            computed_by="app/services/breach.py::breach_metrics", compute=k41_breach_principals),
    KpiSpec("K-42", "Consent-manager API availability and latency", "uptime %, p95",
            D_CM, "percent", "≥ 99.9%", "R.4", scope_support=PLATFORM_ONLY,
            computed_by="app/api/routes/consent_manager.py::metrics",
            supports_breakdown=True, compute=k42_cm_availability),
    KpiSpec("K-43", "Record-retrieval time for principal export",
            "seconds to produce a machine-readable record (p95)",
            D_CM, "seconds", "≤ 60s", "Part B 4(b)", scope_support=PLATFORM_ONLY,
            computed_by="app/api/routes/consent_manager.py::metrics", compute=k43_record_retrieval),
    KpiSpec("K-44", "Notification delivery rate", "delivered ÷ attempted by channel",
            D_NOTIFY, "percent", "≥ 99%", "BRD §4.4", scope_support=PLATFORM_ONLY,
            computed_by="app/services/kpi.py::notification_delivery_metrics",
            supports_trend=True, supports_breakdown=True, compute=k44_notification_delivery),
    KpiSpec("K-45", "Notification acknowledgement rate", "acknowledged ÷ delivered",
            D_NOTIFY, "percent", "trend", "BRD §4.4.1", scope_support=PLATFORM_ONLY,
            computed_by="app/services/kpi.py::notification_delivery_metrics",
            supports_trend=True, compute=k45_notification_ack),
    KpiSpec("K-46", "Tenant-scope violations", "requests where caller tenant ≠ target tenant",
            D_SECURITY, "count", "0", "R.6(1)(b)",
            unavailable_reason=(
                "Cross-tenant attempts are refused by app/services/tenancy.py::resolve_customer, "
                "which by design cannot distinguish 'belongs to another tenant' from 'does not "
                "exist' — so no event marks one, and there is nothing to count."),
            unblocked_by="H-04 (a distinct refusal event for a cross-tenant identifier match)"),
    KpiSpec("K-47", "Decision mix",
            "share of accept-all / reject-all / granular decisions per banner version",
            D_QUALITY, "percent", "granular option used; reject measurable",
            's.6(1) "specific", "free"',
            computed_by="app/services/banner_events.py",
            supports_trend=True, supports_breakdown=True, compute=k47_decision_mix),
    KpiSpec("K-48", "Tags or cookies firing before consent",
            "non-essential scripts/cookies observed before a decision, per site scan",
            D_QUALITY, "count", "0", "s.6(1), BRD §4.2",
            unavailable_reason=(
                "Each demo site already runs a client-side scanner (consentGate.ts's "
                "scanForUngatedTags / startTagScanMonitor), but its findings stay in the browser "
                "— there is no server sink, so nothing is aggregated here."),
            unblocked_by="A server sink for the existing client-side tag-scan report"),
    KpiSpec("K-49", "Consent-sync lag and failure rate",
            "time and error rate pushing consent state to fiduciary/processor endpoints",
            D_PROCESSORS, "seconds", "p95 ≤ 60s; failures retried to 0", "s.6(6), BRD §4.4.2",
            unavailable_reason=(
                "Processor alert acknowledgement latency is measured under K-08, but that is the "
                "processor's response time, not this platform's push latency, and no per-push "
                "send/complete timestamp pair is recorded."),
            unblocked_by="O-03 (consent-sync push telemetry)"),
)

BY_ID = {spec.id: spec for spec in CATALOGUE}
DOMAINS = tuple(dict.fromkeys(spec.domain for spec in CATALOGUE))


# ---------------------------------------------------------------------------
#  Evaluation
# ---------------------------------------------------------------------------
_PLATFORM_ONLY_REASON = (
    "This KPI's computation is owned by a module that reports platform-wide and cannot be "
    "filtered to one tenant. Showing it to a tenant-scoped role would show that tenant another "
    "tenant's aggregate, and reimplementing it with a filter here would create a second, "
    "divergent answer to one compliance question. It is withheld at tenant scope rather than "
    "guessed at."
)


def evaluate(spec: KpiSpec, ctx: KpiContext) -> dict:
    """Produce one KPI's result dict, fully populated and never throwing."""
    base = {
        "id": spec.id, "name": spec.name, "formula": spec.formula, "domain": spec.domain,
        "unit": spec.unit, "target": spec.target, "statutory_ref": spec.statutory_ref,
        "computed_by": spec.computed_by,
        "supports_trend": spec.supports_trend, "supports_breakdown": spec.supports_breakdown,
        "value": None, "display": "—", "numerator": None, "denominator": None,
        "sample_size": None, "coverage": None, "reason": None, "unblocked_by": None,
        "breakdown": [], "detail": {},
    }

    if spec.compute is None:
        base.update(unavailable(
            spec.unavailable_reason or "No data source exists for this KPI in this build.",
            spec.unblocked_by or "—",
        ))
        base["display"] = "—"
        return base

    if spec.scope_support == PLATFORM_ONLY and ctx.scope:
        base.update(unavailable(
            _PLATFORM_ONLY_REASON,
            f"Tenant scoping in {spec.computed_by or 'the owning module'}",
        ))
        return base

    try:
        result = spec.compute(ctx)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("KPI %s failed to compute", spec.id)
        result = unavailable(
            f"This KPI could not be computed this request: {type(exc).__name__}: {exc}",
            "Fix the owning module; the dashboard reports the failure rather than a zero.",
        )

    base.update(result)
    # A LIVE status with no number is a contradiction; treat it as the empty
    # sample it actually is rather than rendering a blank card as a figure.
    if base["status"] == LIVE and base["value"] is None:
        base["status"] = NO_DATA
        base["reason"] = base.get("reason") or "The computation produced no value for this scope and period."
    base["display"] = _fmt(base["value"], spec.unit)
    return base


def build_catalogue(
    db: Session,
    *,
    scope: Optional[str],
    tenant_id: Optional[int],
    period_start: datetime,
    period_end: datetime,
    scope_locked: bool = False,
    purpose_code: Optional[str] = None,
    language: Optional[str] = None,
) -> dict:
    ctx = KpiContext(
        db=db, scope=scope, tenant_id=tenant_id, period_start=period_start,
        period_end=period_end, purpose_code=purpose_code, language=language,
    )
    kpis = [evaluate(spec, ctx) for spec in CATALOGUE]
    counts = {
        "total": len(kpis),
        "live": sum(1 for k in kpis if k["status"] == LIVE),
        "no_data": sum(1 for k in kpis if k["status"] == NO_DATA),
        "partial": sum(1 for k in kpis if k["status"] == PARTIAL),
        "unavailable": sum(1 for k in kpis if k["status"] == UNAVAILABLE),
    }
    return {
        "generated_at": datetime.now(timezone.utc),
        "scope": scope,
        "scope_label": scope or "All tenants",
        "scope_locked": scope_locked,
        "period_days": ctx.period_days,
        "period_start": period_start,
        "period_end": period_end,
        "purpose_filter": purpose_code,
        "language_filter": language,
        "counts": counts,
        "domains": list(DOMAINS),
        "kpis": kpis,
        "honesty_note": HONESTY_NOTE,
    }


# ---------------------------------------------------------------------------
#  Trends
# ---------------------------------------------------------------------------
_BANNER_TREND_KPIS = {"K-01", "K-02", "K-03", "K-04", "K-47"}
_SNAPSHOT_TREND_KPIS = {"K-18": "evidence_completeness_pct",
                        "K-44": "notification_delivery_rate_pct",
                        "K-45": "notification_ack_rate_pct"}


def build_trend(
    db: Session,
    *,
    kpi_id: str,
    scope: Optional[str],
    tenant_id: Optional[int],
    period_start: datetime,
    period_end: datetime,
    group_by: str = "day",
) -> dict:
    spec = BY_ID[kpi_id]
    out = {
        "kpi_id": spec.id, "name": spec.name, "unit": spec.unit, "group_by": group_by,
        "scope_label": scope or "All tenants", "status": LIVE, "points": [],
        "computed_by": spec.computed_by, "reason": None,
    }
    if not spec.supports_trend:
        out["status"] = UNAVAILABLE
        out["reason"] = (
            "This KPI is computed point-in-time by the module that owns it; there is no "
            "historical series to plot without recomputing it a second way."
        )
        return out
    if spec.scope_support == PLATFORM_ONLY and scope:
        out["status"] = UNAVAILABLE
        out["reason"] = _PLATFORM_ONLY_REASON
        return out

    if kpi_id in _BANNER_TREND_KPIS:
        out["points"] = _banner_trend(db, kpi_id, tenant_id, period_start, period_end, group_by)
    elif kpi_id in _SNAPSHOT_TREND_KPIS:
        out["points"] = _snapshot_trend(db, _SNAPSHOT_TREND_KPIS[kpi_id], period_start, period_end)
    elif kpi_id == "K-12":
        out["points"] = _decision_trend(db, scope, period_start, period_end, group_by)
    else:
        out["points"] = _consent_trend(db, kpi_id, scope, period_start, period_end, group_by)

    if not out["points"]:
        out["status"] = NO_DATA
        out["reason"] = "No observation falls in this period for this scope."
    return out


def _point(period: datetime, value, numerator=None, denominator=None) -> dict:
    return {
        "period": period.date().isoformat(), "period_start": period, "value": value,
        "numerator": numerator, "denominator": denominator,
        "status": LIVE if value is not None else NO_DATA,
    }


def _banner_trend(db, kpi_id, tenant_id, start, end, group_by) -> list[dict]:
    from app.services import banner_events

    points = []
    for bucket in banner_events.trend(
        db, tenant_id=tenant_id, start=start, end=end, group_by=group_by
    ):
        shown = bucket["impression_sessions"]
        decided = bucket["decision_sessions"]
        if kpi_id == "K-02":
            points.append(_point(bucket["period_start"], _pct(bucket["REJECT_ALL"], decided),
                                 bucket["REJECT_ALL"], decided))
        elif kpi_id == "K-47":
            points.append(_point(bucket["period_start"], _pct(bucket["GRANULAR"], decided),
                                 bucket["GRANULAR"], decided))
        elif kpi_id == "K-01":
            granted = bucket["ACCEPT_ALL"] + bucket["GRANULAR"]
            points.append(_point(bucket["period_start"], _pct(granted, decided), granted, decided))
        elif kpi_id == "K-03":
            points.append(_point(bucket["period_start"], _pct(bucket["GRANULAR"], decided),
                                 bucket["GRANULAR"], decided))
        else:  # K-04 plots how many decisions were timed, not a duration per bucket
            points.append(_point(bucket["period_start"], float(decided), decided, shown))
    return points


def _snapshot_trend(db, metric_key: str, start, end) -> list[dict]:
    """Read the hourly `kpi_snapshots` the rollup job already writes rather
    than recomputing a historical value the platform never actually held."""
    rows = (
        db.query(KpiSnapshot)
        .filter(KpiSnapshot.captured_at >= start, KpiSnapshot.captured_at <= end)
        .order_by(KpiSnapshot.captured_at)
        .all()
    )
    return [
        _point(row.captured_at, (row.metrics or {}).get(metric_key))
        for row in rows
        if (row.metrics or {}).get(metric_key) is not None
    ]


def _decision_trend(db, scope, start, end, group_by) -> list[dict]:
    from app.api.routes.decisions import decision_report

    report = _as_dict(decision_report(
        date_from=start, date_to=end, group_by=group_by if group_by != "total" else "day",
        db=db, user=_FakeScopedUser(scope),
    ))
    points = []
    for bucket in report.get("buckets", []):
        total = bucket.get("total", 0)
        period = bucket.get("period")
        period_dt = datetime.fromisoformat(str(period)) if period else start
        if period_dt.tzinfo is None:
            period_dt = period_dt.replace(tzinfo=timezone.utc)
        points.append(_point(period_dt, float(total), bucket.get("ALLOW", 0), total))
    return points


def _consent_trend(db, kpi_id, scope, start, end, group_by) -> list[dict]:
    """K-11, K-14 and K-17 all plot a consent count per bucket; which column
    is bucketed is the only difference."""
    column = {
        "K-11": Consent.granted_at,
        "K-14": Consent.expires_at,
        "K-17": Consent.withdrawn_at,
    }.get(kpi_id, Consent.created_at)
    bucket = func.date_trunc(group_by if group_by != "total" else "day", column).label("bucket")
    q = db.query(bucket, func.count(Consent.id)).filter(
        column.isnot(None), column >= start, column <= end
    )
    if scope:
        q = q.filter(Consent.source_app == scope)
    rows = q.group_by(bucket).order_by(bucket).all()
    return [_point(period, float(count), count) for period, count in rows]
