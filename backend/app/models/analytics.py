"""R2-07: banner impression and decision instrumentation.

Five KPIs in the register (K-01 impression half, K-02, K-03, K-04, K-47) are
marked "data not captured" for the same single reason: nothing anywhere
records that a notice was *shown*. Every other consent metric starts from a
`Consent` row, which by definition only exists once somebody acted. A
platform that can only see the people who said yes cannot measure whether
saying no was a real option - which is exactly what s.6(1)'s "free" and
"specific" require it to be able to show.

`banner_events` is that missing denominator, and it is deliberately the
thinnest table that can produce it.

**No personal data. Not "masked" personal data - none.** This is the only
write path in the platform that a wholly unauthenticated browser can reach
(a cookie banner is shown before anyone logs in, so it cannot be otherwise),
and it is written on nearly every page view rather than on a deliberate act.
Both facts argue for the same thing: it stores no IP, no user agent, no
customer id, no email, no external id, and no free text. Compare
`consent_evidence`, which stores IP and user agent on purpose - that is a
record of one identified principal's own affirmative act, retained as proof
*for* them under s.6(10). An impression row proves nothing about any
individual and is aggregated before it is ever read, so carrying an
identifier would be collection without a purpose.

**`session_ref` is a hash, and joining is the whole point.** K-02's
no-choice rate is (notices shown - decisions) / notices shown and K-04 is
the elapsed time between the two, so an impression must be linkable to the
decision that followed it. The client mints a random nonce per banner
session; the server stores only SHA-256 of it. Equality still joins, so
every metric works, but the value on disk cannot be correlated with any
nonce held elsewhere, and a site that mistakenly passed something
identifying here would have hashed it rather than published it.

**`time_to_decision_ms` is computed server-side or not at all.** The client
knows when it painted the banner and could simply report the duration, but
K-04 is evidence about how much time a principal was given to decide, and a
number the measured party supplies about itself is not evidence. The
decision row's arrival time is differenced against the stored impression
row's own `occurred_at`; when no impression row exists (the beacon was
blocked, or the banner predates this build) the column stays NULL and the
KPI reports a smaller sample rather than a guess.

Defined in its own module, not `entities.py`, so this lane could be built
alongside the others without contending for that file; every foreign-key
target is named by STRING for the same reason - nothing here imports
`entities.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# What kind of thing happened. Kept deliberately small: this table answers
# "was a notice shown, and was it acted on", nothing else.
BANNER_EVENT_TYPES = ("NOTICE_SHOWN", "DECISION")

# How the principal disposed of the notice.
#
# There is no NO_CHOICE member on purpose. "No choice" is the *absence* of a
# decision row for an impression, and it has to stay an absence: if a client
# were allowed to post NO_CHOICE it would only ever do so for the sessions
# where its own script was still running, i.e. the ones that did not really
# ignore the banner, and K-02's ignore rate would silently measure something
# else. It is derived, never reported.
BANNER_DECISIONS = ("ACCEPT_ALL", "REJECT_ALL", "GRANULAR", "DISMISSED")

# Where the notice appeared. K-47 asks for the decision mix "per banner
# version"; in practice a tenant runs more than one consent surface at once
# (a cookie banner, a signup-time consent gate, a preference centre) and
# mixing them would compare a first-visit banner against a deliberate visit
# to the preference page.
BANNER_SURFACES = ("COOKIE_BANNER", "CONSENT_GATE", "PREFERENCE_CENTRE", "NOTICE_PAGE")


class BannerEvent(Base):
    """One notice impression, or one decision taken on one."""

    __tablename__ = "banner_events"
    __table_args__ = (
        # The shape of every KPI query here: one tenant, one period.
        Index("ix_banner_events_tenant_occurred", "tenant_id", "occurred_at"),
        # The impression -> decision join behind K-02's ignore rate and K-04.
        Index("ix_banner_events_session_type", "session_ref", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    # The tenant code as the client sent it, kept alongside tenant_id for the
    # same reason every other table here keeps it: it is what the demo sites
    # and integrations speak, and the dashboard filters on it.
    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # NULL on an impression row; one of BANNER_DECISIONS on a decision row.
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    surface: Mapped[str] = mapped_column(String(32), default="COOKIE_BANNER", index=True)

    # Which build of the banner, and which language it was rendered in.
    # `language` is the K-06 axis and the reason the dashboard can answer
    # "is the reject option as reachable in Tamil as in English".
    banner_version: Mapped[str] = mapped_column(String(32), default="", index=True)
    language: Mapped[str] = mapped_column(String(8), default="en", index=True)

    # Path only, already run through app.core.access_log._normalize_path by
    # the ingest route: no query string (which is where a site would put a
    # tracking id or an email), and every identifier-looking segment
    # collapsed to {id}. Nullable because a client need not send one.
    page_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # SHA-256 of a client-minted random nonce. See the module docstring.
    session_ref: Mapped[str] = mapped_column(String(64), nullable=False)

    # Purpose codes, never names or descriptions: `purposes_offered` is
    # K-01's per-purpose denominator and `purposes_granted` its numerator,
    # and the two together are what make K-03's "chose a subset" decidable
    # without inspecting any consent row.
    purposes_offered: Mapped[list] = mapped_column(JSONB, default=list)
    purposes_granted: Mapped[list] = mapped_column(JSONB, default=list)

    # Server-computed. See the module docstring on why the client's own
    # number is not accepted.
    time_to_decision_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
