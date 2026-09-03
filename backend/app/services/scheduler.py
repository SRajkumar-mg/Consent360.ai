"""Background scheduler and jobs framework (R1-07).

Uses APScheduler (in-process) to run scheduled jobs (expiry, token cleanup,
renewal reminders, KPI rollup) fully outside the request path. Each job is
wrapped so a single job's failure is isolated and recorded in scheduler_runs.

This module is wired into the FastAPI lifespan so the jobs start on app
startup. APScheduler is chosen because no message broker (Redis/RabbitMQ)
is provisioned in this deployment; see backend/app/main.py for the lifespan.
"""

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from sqlalchemy.orm import Session

from app.models.entities import SchedulerRun

logger = logging.getLogger("consent360.scheduler")

_scheduler = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Job wrapper — record a scheduler_runs row per run, isolate failures
# --------------------------------------------------------------------------- #

def run_job(job_name: str, fn, *args, **kwargs) -> SchedulerRun:
    """Run a job function and record the outcome in scheduler_runs.

    Never lets an exception propagate to the scheduler process.
    """
    run = SchedulerRun(job_name=job_name, status="running")
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        db.add(run)
        db.commit()
        db.refresh(run)
        result = fn(db, *args, **kwargs)
        processed = result if isinstance(result, int) else 0
        run.status = "success"
        run.processed_count = processed
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", job_name)
        run.status = "failed"
        run.error_count = 1
        run.error_detail = str(exc)[:1000]
    finally:
        run.finished_at = utcnow()
        db.add(run)
        db.commit()
        db.close()
    return run


def job_wrapper(job_name: str):
    """Decorator that wraps a job function so callers get `run_job` behaviour."""
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            return run_job(job_name, fn, *args, **kwargs)
        return wrapped
    return decorator


# --------------------------------------------------------------------------- #
#  Job implementations
# --------------------------------------------------------------------------- #

@job_wrapper("expire_consents")
def expire_consents_job(db: Session, actor_username: str = "system") -> int:
    """Expire consents whose expires_at is in the past. Runs every 15 min."""
    from app.services.consent import expire_consents
    return expire_consents(db, actor_username=actor_username, source_app="SYSTEM")


@job_wrapper("renewal_reminders")
def renewal_reminders_job(db: Session, days_ahead: int = 30) -> int:
    """Identify consents expiring in the next `days_ahead` days.

    This task only *identifies* the set. Sending the actual reminder depends on
    the notification service (task R3-06), which does not exist yet — so this
    just counts them and records the count in scheduler_runs.
    """
    from app.models.entities import Consent
    now = utcnow()
    horizon = now + timedelta(days=days_ahead)
    count = (
        db.query(Consent)
        .filter(
            Consent.status.in_(["GRANTED", "ACTIVE", "RENEWED", "UPDATED"]),
            Consent.expires_at.isnot(None),
            Consent.expires_at > now,
            Consent.expires_at <= horizon,
        )
        .count()
    )
    # TODO(R3-06): once the notification service exists, iterate the affected
    # principals and trigger a REMINDER notification here.
    return count


@job_wrapper("context_token_cleanup")
def context_token_cleanup_job(db: Session) -> int:
    """Purge expired consent_context rows. Runs hourly.

    Also hashes tokens going forward (done at context creation in
    services/context.py via the token_hash column).
    """
    from app.models.entities import ConsentContext
    now = utcnow()
    expired = (
        db.query(ConsentContext)
        .filter(ConsentContext.expires_at < now)
        .all()
    )
    count = len(expired)
    for row in expired:
        db.delete(row)
    db.commit()
    return count


@job_wrapper("kpi_rollup")
def kpi_rollup_job(db: Session) -> int:
    """Stub KPI rollup job. Later KPI work (R2-07) populates real aggregation."""
    return 0


# --------------------------------------------------------------------------- #
#  APScheduler wiring
# --------------------------------------------------------------------------- #

def start_scheduler() -> None:
    """Start the APScheduler BackgroundScheduler and register all jobs."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(
            expire_consents_job, IntervalTrigger(minutes=15),
            id="expire_consents", replace_existing=True,
        )
        _scheduler.add_job(
            renewal_reminders_job, IntervalTrigger(days=1),
            id="renewal_reminders", replace_existing=True,
        )
        _scheduler.add_job(
            context_token_cleanup_job, IntervalTrigger(hours=1),
            id="context_token_cleanup", replace_existing=True,
        )
        _scheduler.add_job(
            kpi_rollup_job, IntervalTrigger(hours=1),
            id="kpi_rollup", replace_existing=True,
        )
        _scheduler.start()
        logger.info("APScheduler background scheduler started with %d jobs", 4)
    except ImportError:
        logger.warning(
            "APScheduler not installed — scheduled jobs disabled. "
            "Install with: pip install apscheduler"
        )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def run_all_jobs_once() -> list[dict]:
    """Run every job synchronously once (handy for tests / manual triggers)."""
    results = []
    for name, fn in (
        ("expire_consents", expire_consents_job),
        ("renewal_reminders", renewal_reminders_job),
        ("context_token_cleanup", context_token_cleanup_job),
        ("kpi_rollup", kpi_rollup_job),
    ):
        run = run_job(name, fn)
        results.append({"job": name, "status": run.status, "processed": run.processed_count})
    return results
