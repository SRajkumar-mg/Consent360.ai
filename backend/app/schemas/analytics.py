"""R2-07: banner-event ingest and the compliance KPI catalogue.

The ingest schema is deliberately strict about *shape* because the endpoint
behind it is unauthenticated (see `app/models/analytics.py`): every string is
length-capped, every list is size-capped, purpose codes must match a code
pattern, and there is no free-text field at all. A validator that rejects a
malformed beacon costs a browser nothing and is the only thing standing
between an open write path and an unbounded row.

The read schemas exist to make one distinction impossible to lose: a KPI
whose value is **zero** and a KPI whose value is **unknown** are different
answers, and the second one is not allowed to render as the first. Every
value is `Optional[float]` and travels with a `status`:

* ``LIVE``        - computed from real data with a real denominator.
* ``NO_DATA``     - instrumented and queried; the sample was empty, so the
                    ratio is undefined. ``value`` is null, never 0.
* ``PARTIAL``     - computed, but over less than the KPI's full definition.
                    ``coverage`` says exactly what is and is not counted.
* ``UNAVAILABLE`` - this build has no data source for it. ``value`` is null
                    and ``unblocked_by`` names the gap-register item that
                    would supply it.

The vocabulary is `app/services/evidence_pack.py`'s on purpose. That module
already had to solve this problem for a regulator-facing export, was already
bitten once by a section that kept reporting UNAVAILABLE after it became
available, and a reader who has learned what PARTIAL means in the pack should
not have to learn a second dialect on the dashboard.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
#  Ingest
# ---------------------------------------------------------------------------

# A purpose code as every other surface in this platform spells one. No
# spaces, no punctuation beyond the separators actually in use, so a client
# cannot smuggle a sentence - or an email address - into what is meant to be
# an opaque code and have it stored.
_PURPOSE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

MAX_PURPOSES = 50


class BannerEventIn(BaseModel):
    """One notice impression, or one decision on one.

    Note what is NOT here: no customer id, no email, no external id, no IP,
    no user agent, no free text. The route does not read them from the
    request either. See `app/models/analytics.py` for why.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["NOTICE_SHOWN", "DECISION"]
    decision: Optional[Literal["ACCEPT_ALL", "REJECT_ALL", "GRANULAR", "DISMISSED"]] = None
    surface: Literal["COOKIE_BANNER", "CONSENT_GATE", "PREFERENCE_CENTRE", "NOTICE_PAGE"] = "COOKIE_BANNER"

    banner_version: str = Field(default="", max_length=32)
    language: str = Field(default="en", max_length=8)

    # Path only. The route re-normalises it anyway (query string dropped,
    # identifier-looking segments collapsed); the cap here is so a caller
    # cannot make the route do that work on a megabyte of string.
    page_path: Optional[str] = Field(default=None, max_length=512)

    # A client-minted random nonce, hashed before storage. Long enough to be
    # a real nonce, short enough not to be a payload.
    session_id: str = Field(min_length=8, max_length=128)

    purposes_offered: list[str] = Field(default_factory=list, max_length=MAX_PURPOSES)
    purposes_granted: list[str] = Field(default_factory=list, max_length=MAX_PURPOSES)

    @field_validator("purposes_offered", "purposes_granted")
    @classmethod
    def _codes_only(cls, value: list[str]) -> list[str]:
        for code in value:
            if not _PURPOSE_CODE.match(code or ""):
                raise ValueError(
                    "purpose codes must match [A-Za-z0-9_.:-]{1,64} - this field carries "
                    "codes, never labels or free text"
                )
        # Order carries no meaning and duplicates would double-count a purpose
        # in K-01's denominator, so normalise once here rather than in four
        # separate aggregation queries.
        return sorted(set(value))

    @field_validator("language")
    @classmethod
    def _language_tag(cls, value: str) -> str:
        if value and not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$", value):
            raise ValueError("language must be a BCP-47-style tag such as 'en', 'hi' or 'kok'")
        return value or "en"

    @model_validator(mode="after")
    def _coherent(self) -> "BannerEventIn":
        if self.event_type == "DECISION" and not self.decision:
            raise ValueError("a DECISION event must say which decision was taken")
        if self.event_type == "NOTICE_SHOWN":
            if self.decision:
                raise ValueError("an impression is not a decision; omit `decision`")
            if self.purposes_granted:
                raise ValueError("an impression grants nothing; omit `purposes_granted`")
        extra = set(self.purposes_granted) - set(self.purposes_offered)
        if extra:
            # A grant for a purpose the banner never offered would inflate
            # K-01's numerator past its own denominator. Whatever produced it
            # is wrong; refusing is better than storing a ratio above 1.
            raise ValueError(
                f"purposes_granted contains {sorted(extra)} which purposes_offered does not list"
            )
        return self


class BannerEventAck(BaseModel):
    """Deliberately says almost nothing. An unauthenticated caller needs to
    know the beacon was accepted and nothing else - no row id, no counts, no
    tenant detail that would turn this into a read endpoint."""

    recorded: bool = True
    event_type: str


# ---------------------------------------------------------------------------
#  KPI catalogue
# ---------------------------------------------------------------------------

KpiStatus = Literal["LIVE", "NO_DATA", "PARTIAL", "UNAVAILABLE"]


class KpiBreakdownRow(BaseModel):
    """One row of a KPI's drill-down: one purpose, one language, one channel,
    one processing activity - whatever that KPI's dimension is."""

    dimension: str
    label: str
    value: Optional[float] = None
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    unit: str = "count"
    status: KpiStatus = "LIVE"
    detail: dict = Field(default_factory=dict)


class KpiTrendPoint(BaseModel):
    period: str
    period_start: datetime
    value: Optional[float] = None
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    status: KpiStatus = "LIVE"


class KpiOut(BaseModel):
    id: str
    name: str
    formula: str
    domain: str
    unit: str
    target: str
    statutory_ref: str

    status: KpiStatus
    value: Optional[float] = None
    # Pre-formatted for display so the number and its unit cannot drift apart
    # between the four places a dashboard shows them. "—" when there is no
    # value; never "0".
    display: str = "—"
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    sample_size: Optional[int] = None

    # PARTIAL only: what is and is not counted.
    coverage: Optional[str] = None
    # NO_DATA / UNAVAILABLE only: why there is no number.
    reason: Optional[str] = None
    # UNAVAILABLE only: the gap-register item that would supply the data.
    unblocked_by: Optional[str] = None
    # Which module produced this figure. The dashboard aggregates; it never
    # recomputes a KPI a specialised module already owns, and this field is
    # the receipt for that claim - two screens showing one KPI must be able
    # to prove they read the same function.
    computed_by: Optional[str] = None

    supports_trend: bool = False
    supports_breakdown: bool = False
    breakdown: list[KpiBreakdownRow] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)


class KpiCounts(BaseModel):
    total: int
    live: int
    no_data: int
    partial: int
    unavailable: int


class KpiCatalogueOut(BaseModel):
    generated_at: datetime
    scope: Optional[str] = None
    scope_label: str
    scope_locked: bool = False
    period_days: int
    period_start: datetime
    period_end: datetime
    purpose_filter: Optional[str] = None
    language_filter: Optional[str] = None
    counts: KpiCounts
    domains: list[str] = Field(default_factory=list)
    kpis: list[KpiOut] = Field(default_factory=list)
    honesty_note: str


class KpiTrendOut(BaseModel):
    kpi_id: str
    name: str
    unit: str
    status: KpiStatus
    group_by: str
    scope_label: str
    points: list[KpiTrendPoint] = Field(default_factory=list)
    reason: Optional[str] = None
    computed_by: Optional[str] = None
