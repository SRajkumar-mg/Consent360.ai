"""R2-07: recording notice impressions, and the five KPIs only they unlock.

`app/models/analytics.py` explains why the table exists and why it holds no
personal data. This module explains how it is counted, because for K-02,
K-03 and K-47 the counting rule *is* the metric.

**Everything in the funnel is counted per session, and a session's decision
is its FIRST one.** A principal who accepts, then reopens the preference
centre an hour later and rejects, is one person who was shown one notice and
made one first choice. Counting decision *rows* instead would let a tenant
whose preference centre is easy to reach look worse than one whose is
unreachable, which inverts what s.6(4) wants to encourage; it would also make
K-02's "(notices - decisions) / notices" produce a negative number as soon as
anyone changed their mind twice. Later decisions are not discarded - they are
reported separately as `subsequent_decisions` - they are just not what the
funnel ratios are computed over.

**"No choice" is an absence and is derived here, never posted.** A session
with an impression and no decision row ignored the banner. This is the one
figure a client physically cannot report about itself (a script that has
stopped running cannot tell you it stopped), which is exactly why K-02 says
it "must be measurable" - it is the number that shows whether declining was
a real option or whether the banner simply nagged until it got a click.

**Decisions arriving with no impression are counted apart.** A beacon
blocked on impression but not on decision would otherwise push K-02's
no-choice rate negative. They are surfaced as `decisions_without_impression`
so the gap is visible rather than smoothed away.
"""
from __future__ import annotations

import hashlib
import statistics
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.analytics import BannerEvent

MODULE_REF = "app/services/banner_events.py"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_session(session_id: str) -> str:
    """SHA-256 of the client's nonce.

    Not salted, and that is deliberate: the whole value of this column is
    that two rows from one banner session hash alike so the impression can
    be joined to the decision that followed it. A per-row salt would destroy
    the join; a fixed secret salt would add nothing, because the input is
    already a random nonce with no meaning outside this table."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
#  Ingest
# ---------------------------------------------------------------------------
def record_event(
    db: Session,
    *,
    tenant_id: int,
    source_app: str,
    event_type: str,
    decision: Optional[str],
    surface: str,
    banner_version: str,
    language: str,
    page_ref: Optional[str],
    session_id: str,
    purposes_offered: list[str],
    purposes_granted: list[str],
    occurred_at: Optional[datetime] = None,
) -> BannerEvent:
    """Store one event and, for a decision, derive its time-to-decision from
    the impression already on file.

    The elapsed time is measured between two rows this server timestamped,
    never from a duration the client reported about itself - see the module
    docstring of `app/models/analytics.py`. When no impression row exists the
    column stays NULL and K-04 reports a smaller sample; it does not guess.
    """
    now = occurred_at or utcnow()
    session_ref = hash_session(session_id)

    time_to_decision_ms: Optional[int] = None
    if event_type == "DECISION":
        shown_at = (
            db.query(func.min(BannerEvent.occurred_at))
            .filter(
                BannerEvent.session_ref == session_ref,
                BannerEvent.event_type == "NOTICE_SHOWN",
                BannerEvent.tenant_id == tenant_id,
            )
            .scalar()
        )
        if shown_at is not None:
            if shown_at.tzinfo is None:
                shown_at = shown_at.replace(tzinfo=timezone.utc)
            delta_ms = int((now - shown_at).total_seconds() * 1000)
            # A negative delta means the two rows disagree about time (clock
            # skew across workers, or a replayed beacon). Recording it would
            # drag K-04's median toward a number no principal experienced.
            if delta_ms >= 0:
                time_to_decision_ms = delta_ms

    event = BannerEvent(
        tenant_id=tenant_id,
        source_app=source_app,
        event_type=event_type,
        decision=decision,
        surface=surface,
        banner_version=banner_version,
        language=language,
        page_ref=page_ref,
        session_ref=session_ref,
        purposes_offered=list(purposes_offered),
        purposes_granted=list(purposes_granted),
        time_to_decision_ms=time_to_decision_ms,
        occurred_at=now,
    )
    db.add(event)
    db.commit()
    return event


# ---------------------------------------------------------------------------
#  Aggregation
# ---------------------------------------------------------------------------
def _window(
    stmt: Select,
    *,
    tenant_id: Optional[int],
    start: datetime,
    end: datetime,
    language: Optional[str],
    surface: Optional[str],
) -> Select:
    stmt = stmt.where(BannerEvent.occurred_at >= start, BannerEvent.occurred_at <= end)
    if tenant_id is not None:
        stmt = stmt.where(BannerEvent.tenant_id == tenant_id)
    if language:
        stmt = stmt.where(BannerEvent.language == language)
    if surface:
        stmt = stmt.where(BannerEvent.surface == surface)
    return stmt


def funnel(
    db: Session,
    *,
    tenant_id: Optional[int] = None,
    start: datetime,
    end: datetime,
    language: Optional[str] = None,
    surface: Optional[str] = None,
) -> dict:
    """The session-level funnel behind K-02, K-03, K-04 and K-47."""
    impression_sessions = db.execute(
        _window(
            select(func.count(func.distinct(BannerEvent.session_ref))).where(
                BannerEvent.event_type == "NOTICE_SHOWN"
            ),
            tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
        )
    ).scalar() or 0

    # DISTINCT ON (session_ref) ORDER BY session_ref, occurred_at: one row
    # per session, the earliest decision it made. Postgres-only, like
    # app/api/routes/decisions.py's date_trunc buckets already are.
    first_decisions = db.execute(
        _window(
            select(
                BannerEvent.session_ref,
                BannerEvent.decision,
                BannerEvent.purposes_offered,
                BannerEvent.purposes_granted,
                BannerEvent.time_to_decision_ms,
                BannerEvent.language,
                BannerEvent.banner_version,
                BannerEvent.surface,
            )
            .distinct(BannerEvent.session_ref)
            .where(BannerEvent.event_type == "DECISION"),
            tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
        ).order_by(BannerEvent.session_ref, BannerEvent.occurred_at.asc(), BannerEvent.id.asc())
    ).all()

    total_decision_rows = db.execute(
        _window(
            select(func.count(BannerEvent.id)).where(BannerEvent.event_type == "DECISION"),
            tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
        )
    ).scalar() or 0

    impression_refs = set(
        db.execute(
            _window(
                select(BannerEvent.session_ref).distinct().where(
                    BannerEvent.event_type == "NOTICE_SHOWN"
                ),
                tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
            )
        ).scalars().all()
    )

    mix: dict[str, int] = {"ACCEPT_ALL": 0, "REJECT_ALL": 0, "GRANULAR": 0, "DISMISSED": 0}
    by_version: dict[str, dict[str, int]] = {}
    partial_sessions = 0
    granular_with_offer = 0
    durations: list[int] = []
    decided_with_impression = 0

    for row in first_decisions:
        decision = row.decision or "DISMISSED"
        mix[decision] = mix.get(decision, 0) + 1
        version_bucket = by_version.setdefault(
            row.banner_version or "(unversioned)",
            {"ACCEPT_ALL": 0, "REJECT_ALL": 0, "GRANULAR": 0, "DISMISSED": 0, "total": 0},
        )
        version_bucket[decision] = version_bucket.get(decision, 0) + 1
        version_bucket["total"] += 1

        offered = set(row.purposes_offered or [])
        granted = set(row.purposes_granted or [])
        if offered:
            granular_with_offer += 1
            # K-03 counts sessions that actually exercised per-purpose choice.
            #
            # Two conditions, and the first one is the load-bearing correction:
            # the decision must be GRANULAR. A "reject all" still leaves the
            # strictly-necessary category on, so its granted set IS a strict
            # subset of the offered set - and counting it as partial consent
            # overstated this metric by more than a factor of two on real
            # traffic, in the flattering direction, by reporting people who
            # refused everything they could refuse as people who made a
            # nuanced choice. Whether a purpose is mandatory is not something
            # the banner tells us, so the decision type is the honest
            # discriminator: someone who pressed a blanket button did not
            # choose a subset, whatever the resulting set looks like.
            #
            # Second, the granular choice has to have landed somewhere other
            # than "everything" - a user who opens the toggles and ticks them
            # all has consented to everything, and only the control they used
            # was granular. That distinction is K-47's job, not this one's.
            if (row.decision or "") == "GRANULAR" and granted != offered:
                partial_sessions += 1
        if row.time_to_decision_ms is not None:
            durations.append(row.time_to_decision_ms)
        if row.session_ref in impression_refs:
            decided_with_impression += 1

    decision_sessions = len(first_decisions)
    no_choice_sessions = max(impression_sessions - decided_with_impression, 0)

    return {
        "impression_sessions": impression_sessions,
        "decision_sessions": decision_sessions,
        "decided_with_impression": decided_with_impression,
        "decisions_without_impression": decision_sessions - decided_with_impression,
        "no_choice_sessions": no_choice_sessions,
        "subsequent_decisions": max(total_decision_rows - decision_sessions, 0),
        "mix": mix,
        "mix_by_banner_version": by_version,
        "partial_sessions": partial_sessions,
        "sessions_with_offered_purposes": granular_with_offer,
        "durations_ms": durations,
    }


def duration_stats(durations: list[int]) -> dict:
    """K-04. Median is the register's own choice of statistic and is kept as
    the headline; p90 rides along because a median that looks fine can still
    hide a tail of principals who were made to hunt for the reject control."""
    if not durations:
        return {"samples": 0, "median_seconds": None, "p90_seconds": None, "min_seconds": None, "max_seconds": None}
    ordered = sorted(durations)
    p90_index = max(0, min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1)))))
    return {
        "samples": len(ordered),
        "median_seconds": round(statistics.median(ordered) / 1000, 2),
        "p90_seconds": round(ordered[p90_index] / 1000, 2),
        "min_seconds": round(ordered[0] / 1000, 2),
        "max_seconds": round(ordered[-1] / 1000, 2),
    }


def _unnest_sessions(
    db: Session,
    column,
    *,
    event_type: str,
    tenant_id: Optional[int],
    start: datetime,
    end: datetime,
    language: Optional[str],
    surface: Optional[str],
) -> dict[str, int]:
    """Distinct sessions per purpose code, expanding the JSONB array in SQL.

    Folding this in Python would mean pulling every row in the period into
    the API process to count strings; `jsonb_array_elements_text` keeps it
    in the database, which is where a metric over a high-volume table
    belongs."""
    expanded = _window(
        select(
            func.jsonb_array_elements_text(column).label("code"),
            BannerEvent.session_ref.label("session_ref"),
        ).where(BannerEvent.event_type == event_type),
        tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
    ).subquery()
    rows = db.execute(
        select(expanded.c.code, func.count(func.distinct(expanded.c.session_ref))).group_by(expanded.c.code)
    ).all()
    return {code: count for code, count in rows}


def optin_by_purpose(
    db: Session,
    *,
    tenant_id: Optional[int] = None,
    start: datetime,
    end: datetime,
    language: Optional[str] = None,
    surface: Optional[str] = None,
) -> dict[str, dict]:
    """K-01's impression-based half: per purpose, sessions that granted it
    over sessions that were offered it.

    The register itself marks this the *less* trustworthy of K-01's two
    formulas ("bots and ad-blockers inflate impressions"), so the dashboard
    shows the decision-based ratio as the KPI's value and carries this one
    alongside it. Both are reported; neither is silently substituted for the
    other."""
    offered = _unnest_sessions(
        db, BannerEvent.purposes_offered, event_type="NOTICE_SHOWN",
        tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
    )
    granted = _unnest_sessions(
        db, BannerEvent.purposes_granted, event_type="DECISION",
        tenant_id=tenant_id, start=start, end=end, language=language, surface=surface,
    )
    out: dict[str, dict] = {}
    for code in sorted(set(offered) | set(granted)):
        denom = offered.get(code, 0)
        num = granted.get(code, 0)
        out[code] = {
            "offered_sessions": denom,
            "granted_sessions": num,
            "optin_rate_pct": round((num / denom) * 100, 2) if denom else None,
        }
    return out


def by_language(
    db: Session,
    *,
    tenant_id: Optional[int] = None,
    start: datetime,
    end: datetime,
) -> dict[str, dict]:
    """The language axis for K-06 and for every banner KPI's drill-down: a
    reject rate that collapses in one language is a dark pattern that an
    all-languages average hides."""
    rows = db.execute(
        _window(
            select(
                BannerEvent.language,
                BannerEvent.event_type,
                BannerEvent.decision,
                func.count(func.distinct(BannerEvent.session_ref)),
            ),
            tenant_id=tenant_id, start=start, end=end, language=None, surface=None,
        ).group_by(BannerEvent.language, BannerEvent.event_type, BannerEvent.decision)
    ).all()

    out: dict[str, dict] = {}
    for language, event_type, decision, count in rows:
        bucket = out.setdefault(
            language or "en",
            {"impression_sessions": 0, "decision_sessions": 0,
             "ACCEPT_ALL": 0, "REJECT_ALL": 0, "GRANULAR": 0, "DISMISSED": 0},
        )
        if event_type == "NOTICE_SHOWN":
            bucket["impression_sessions"] += count
        else:
            bucket["decision_sessions"] += count
            if decision in bucket:
                bucket[decision] += count
    return out


def trend(
    db: Session,
    *,
    tenant_id: Optional[int] = None,
    start: datetime,
    end: datetime,
    group_by: str = "day",
) -> list[dict]:
    """Per-bucket impression/decision counts and the decision mix, for the
    trend line on every banner-derived KPI. `date_trunc` matches how
    `app/api/routes/decisions.py` already buckets its own report, so the two
    trend axes line up."""
    bucket = func.date_trunc(group_by, BannerEvent.occurred_at).label("bucket")
    rows = db.execute(
        _window(
            select(
                bucket,
                BannerEvent.event_type,
                BannerEvent.decision,
                func.count(func.distinct(BannerEvent.session_ref)),
            ),
            tenant_id=tenant_id, start=start, end=end, language=None, surface=None,
        ).group_by(bucket, BannerEvent.event_type, BannerEvent.decision).order_by(bucket)
    ).all()

    buckets: dict[datetime, dict] = {}
    for period, event_type, decision, count in rows:
        entry = buckets.setdefault(
            period,
            {"period_start": period, "impression_sessions": 0, "decision_sessions": 0,
             "ACCEPT_ALL": 0, "REJECT_ALL": 0, "GRANULAR": 0, "DISMISSED": 0},
        )
        if event_type == "NOTICE_SHOWN":
            entry["impression_sessions"] += count
        else:
            entry["decision_sessions"] += count
            if decision in entry:
                entry[decision] += count
    return [buckets[k] for k in sorted(buckets)]


def has_any_events(db: Session, *, tenant_id: Optional[int] = None) -> bool:
    """Whether this build has ever seen a banner event at all.

    The dashboard needs this to tell two very different situations apart: a
    tenant whose banners are instrumented but quiet this period (NO_DATA for
    the period, and a longer period would show something), and a platform
    where nothing has ever emitted an event (the instrumentation is not
    deployed). Reporting the second as the first would claim an integration
    exists that does not."""
    q = select(func.count(BannerEvent.id))
    if tenant_id is not None:
        q = q.where(BannerEvent.tenant_id == tenant_id)
    return (db.execute(q).scalar() or 0) > 0
