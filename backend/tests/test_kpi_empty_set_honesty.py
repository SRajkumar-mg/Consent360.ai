"""Lane H: regression tests for eight compliance KPIs that reported a
flattering figure for an empty data set (`x if n else 100.0`, and K-45's
`... else 0.0` - the same defect pointing the other way), plus a ninth
instance of the same pattern found alongside K-42 while fixing this.

Zero-data is not full compliance. `services/breach.py` (K-39..K-41) and
`services/grievance.py` (K-23) already return None for an empty set; these
eight/nine now follow the same convention rather than inventing a third one.

Isolation: six of these nine values come from functions that take no tenant
or date-range parameter at all (`evidence_completeness`,
`notification_delivery_metrics` x2, `coverage_report`, the Consent Manager
`metrics` route x2, `erasure_metrics`) - they are unconditionally
platform-wide (see app/services/kpi_catalogue.py's module docstring). By the
time this file runs, hundreds of earlier tests in the suite have already
created consents, notifications, processing activities, API-call samples and
erasure jobs in the shared `consent_platform_test` database, so there is no
slice of it left that is provably empty. The only way to observe the true
n=0 case for those is a dedicated, disposable database - created and dropped
here, per this lane's standing instruction never to write to the shared
database used elsewhere."""
from __future__ import annotations

import os

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api.routes.consent_manager import metrics as cm_metrics
from app.api.routes.purposes import coverage_report
from app.core.database import Base
from app.models import entities  # noqa: F401 - registers every table on Base.metadata
from app.services.erasure import erasure_metrics
from app.services.kpi import evidence_completeness, notification_delivery_metrics
from app.services.processors import contract_coverage_report, propagation_sla_metrics
from tests.conftest import TEST_DATABASE_URL

_EMPTY_DB_NAME = "consent_platform_test_kpi_empty_set"


def _admin_connect(url):
    admin_url = url.set(database="postgres")
    conn = psycopg2.connect(
        host=admin_url.host, port=admin_url.port,
        user=admin_url.username, password=admin_url.password, dbname="postgres",
    )
    conn.autocommit = True
    return conn


def _drop(base_url):
    conn = _admin_connect(base_url)
    cur = conn.cursor()
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(_EMPTY_DB_NAME)))
    cur.close()
    conn.close()


@pytest.fixture(scope="module")
def empty_db():
    """A Postgres database carrying the full application schema with zero
    rows in every table - never the `consent_platform_test` database the
    rest of the suite's `db` fixture points at."""
    base_url = make_url(TEST_DATABASE_URL)
    empty_url = base_url.set(database=_EMPTY_DB_NAME)

    _drop(base_url)
    conn = _admin_connect(base_url)
    cur = conn.cursor()
    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_EMPTY_DB_NAME)))
    cur.close()
    conn.close()

    engine = create_engine(str(empty_url))
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        _drop(base_url)


# --------------------------------------------------------------------------- #
#  K-08: services/processors.py::propagation_sla_metrics
#  Served directly at GET /processors/reports/propagation-sla.
# --------------------------------------------------------------------------- #
def test_k08_propagation_sla_reports_no_figure_when_nothing_is_decided(empty_db):
    result = propagation_sla_metrics(empty_db)
    assert result["withdrawals_total"] == 0
    assert result["fully_acknowledged_within_sla"] == 0
    assert result["sla_breached"] == 0
    assert result["k08_propagation_sla_pct"] is None, (
        "no withdrawal's propagation SLA outcome is decided yet (met + breached == 0); "
        "the module must not assert 100% propagated"
    )


# --------------------------------------------------------------------------- #
#  K-32: services/processors.py::contract_coverage_report
#  Served directly at GET /processors/reports/contract-coverage.
# --------------------------------------------------------------------------- #
def test_k32_contract_coverage_reports_no_figure_when_no_processor_is_registered(empty_db):
    result = contract_coverage_report(empty_db)
    assert result["processors_total"] == 0
    assert result["coverage_pct"] is None, (
        "an empty processor register is unmeasured coverage, not 100% covered"
    )


# --------------------------------------------------------------------------- #
#  K-18: services/kpi.py::evidence_completeness
# --------------------------------------------------------------------------- #
def test_k18_evidence_completeness_reports_no_figure_with_zero_active_consents(empty_db):
    result = evidence_completeness(empty_db)
    assert result["total_active_consents"] == 0
    assert result["evidence_completeness_pct"] is None


# --------------------------------------------------------------------------- #
#  K-44 / K-45: services/kpi.py::notification_delivery_metrics - one function,
#  two rates, opposite-signed pre-fix defaults (100.0 and 0.0).
#  Served directly at GET /notifications/metrics.
# --------------------------------------------------------------------------- #
def test_k44_delivery_rate_reports_no_figure_with_zero_attempts(empty_db):
    result = notification_delivery_metrics(empty_db)
    assert result["notifications_total_attempted"] == 0
    assert result["notification_delivery_rate_pct"] is None


def test_k45_ack_rate_reports_no_figure_with_zero_delivered(empty_db):
    result = notification_delivery_metrics(empty_db)
    assert result["notifications_delivered"] == 0
    assert result["notification_ack_rate_pct"] is None, (
        "the pre-fix default here was 0.0, not 100.0 - the same false-assurance defect, inverted"
    )


# --------------------------------------------------------------------------- #
#  K-10: api/routes/purposes.py::coverage_report
#  Served directly at GET /purposes/coverage-report. Calling the route
#  function directly with db=/_=None is the same convention
#  app/services/kpi_catalogue.py::k10_lawful_gateway already uses.
# --------------------------------------------------------------------------- #
def test_k10_lawful_gateway_coverage_reports_no_figure_with_zero_activities(empty_db):
    result = coverage_report(db=empty_db, _=None)
    assert result.total_activities == 0
    assert result.coverage_pct is None


# --------------------------------------------------------------------------- #
#  K-42, and the ninth instance found beside it: api/routes/consent_manager.py
#  ::metrics. Served directly at GET /consent-manager/metrics.
# --------------------------------------------------------------------------- #
def test_k42_cm_availability_reports_no_figure_with_zero_calls(empty_db):
    result = cm_metrics(window_hours=24, db=empty_db, _user=None)
    assert result.total_calls == 0
    assert result.availability_pct is None


def test_ninth_instance_cm_error_rate_reports_no_figure_with_zero_calls(empty_db):
    """Not one of the eight this task named, but the same pattern, found
    sitting beside K-42's availability_pct in the very same return
    statement, guarded by the same `if total`, and defaulting to 0.0 for an
    empty sample - the same shape as K-45's defect. Fixed alongside K-42
    since it shares the source, the schema and the direct HTTP route."""
    result = cm_metrics(window_hours=24, db=empty_db, _user=None)
    assert result.total_calls == 0
    assert result.error_rate_pct is None


# --------------------------------------------------------------------------- #
#  K-30: services/erasure.py::erasure_metrics
#  Served directly at GET /erasure/metrics.
# --------------------------------------------------------------------------- #
def test_k30_pre_erasure_notice_compliance_reports_no_figure_with_zero_executions(empty_db):
    result = erasure_metrics(empty_db)
    assert result["jobs_executed"] == 0
    assert result["notice_compliance_pct"] is None


# --------------------------------------------------------------------------- #
#  General invariant: none of the percentages this fix touches can ever
#  exceed 100 - checked on the empty database (vacuously true; every value is
#  None) and again on the shared suite database, whose hundreds of prior
#  tests give each function a real, non-empty denominator wherever the
#  platform-wide function has one to find.
# --------------------------------------------------------------------------- #
def _touched_percentages(session):
    values = {}
    values["K-08 k08_propagation_sla_pct"] = propagation_sla_metrics(session)["k08_propagation_sla_pct"]
    values["K-32 coverage_pct"] = contract_coverage_report(session)["coverage_pct"]
    values["K-18 evidence_completeness_pct"] = evidence_completeness(session)["evidence_completeness_pct"]
    notif = notification_delivery_metrics(session)
    values["K-44 notification_delivery_rate_pct"] = notif["notification_delivery_rate_pct"]
    values["K-45 notification_ack_rate_pct"] = notif["notification_ack_rate_pct"]
    values["K-10 coverage_pct"] = coverage_report(db=session, _=None).coverage_pct
    cm = cm_metrics(window_hours=720, db=session, _user=None)
    values["K-42 availability_pct"] = cm.availability_pct
    values["K-42 error_rate_pct (ninth instance)"] = cm.error_rate_pct
    values["K-30 notice_compliance_pct"] = erasure_metrics(session)["notice_compliance_pct"]
    return values


def test_no_percentage_this_fix_touches_ever_exceeds_100_on_empty_data(empty_db):
    for label, value in _touched_percentages(empty_db).items():
        assert value is None, f"{label} is {value!r} on an empty database - expected None"


def test_no_percentage_this_fix_touches_ever_exceeds_100_on_real_data(db):
    for label, value in _touched_percentages(db).items():
        if value is None:
            continue
        assert 0.0 <= value <= 100.0, f"{label} is {value!r}, not a valid percentage"
