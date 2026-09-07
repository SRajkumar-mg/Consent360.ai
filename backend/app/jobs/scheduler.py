import logging
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.entities import SchedulerRun

logger = logging.getLogger("app.jobs")


class JobRunner:
    """Wraps a job function with a scheduler_runs audit row and error handling."""

    def run(self, job_name: str, job_fn: Callable[[Session], dict]) -> SchedulerRun:
        from app.services.tenancy import platform_tenant_id

        db = SessionLocal()
        run = SchedulerRun(
            job_name=job_name, started_at=datetime.now(timezone.utc), status="RUNNING", counts={},
            tenant_id=platform_tenant_id(db),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        try:
            counts = job_fn(db) or {}
            run.status = "SUCCESS"
            run.counts = counts
        except Exception as exc:  # noqa: BLE001 - job failures must never crash the scheduler thread
            db.rollback()
            run.status = "FAILED"
            run.error = str(exc)
            logger.exception("Scheduler job %s failed", job_name)
        finally:
            self._finalise(db, run)
        return run

    def _finalise(self, db: Session, run: SchedulerRun) -> None:
        """Persist the final state of a `scheduler_runs` row and always close
        the session -- even if persisting it fails. This commit can itself
        raise (a job's `counts` are not JSON-serialisable, a transient DB
        error, ...); an unguarded failure here used to skip `db.close()`
        entirely (leaking the session) and leave the row stuck at
        `status="RUNNING"` with nothing recorded -- exactly the failure this
        table exists to surface. On failure we roll back, discard whatever
        the job returned (it may be the reason the commit failed) in favour
        of `status="ERROR"` plus the exception text, and retry once; if even
        that retry fails, we give up on persisting but still close the
        session.
        """
        run.finished_at = datetime.now(timezone.utc)
        try:
            try:
                db.add(run)
                db.commit()
            except Exception as exc:  # noqa: BLE001 - finalisation must not crash the scheduler thread
                db.rollback()
                logger.exception("Scheduler job %s failed to record its final state", run.job_name)
                run.status = "ERROR"
                run.error = str(exc)
                run.counts = {}
                try:
                    db.add(run)
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception(
                        "Scheduler job %s could not persist its ERROR final state either", run.job_name
                    )
            try:
                # Reload the committed values so the object stays readable
                # after `db.close()` detaches it below (a *failed* refresh
                # here just means the in-memory instance may have expired
                # attributes; it must never be treated as a reason to
                # overwrite an already-committed status).
                db.refresh(run)
            except Exception:
                logger.exception("Scheduler job %s could not refresh its final state after commit", run.job_name)
        finally:
            db.close()


job_runner = JobRunner()
_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from app.jobs.audit_chain_job import run as audit_chain_run
    from app.jobs.context_token_cleanup_job import run as context_token_cleanup_run
    from app.jobs.erasure_executor_job import run as erasure_executor_run
    from app.jobs.erasure_retention_scan_job import run as erasure_retention_scan_run
    from app.jobs.expire_consents_job import run as expire_consents_run
    from app.jobs.inactivity_scan_job import run as inactivity_scan_run
    from app.jobs.pre_erasure_notice_job import run as pre_erasure_notice_run
    from app.jobs.kpi_rollup_job import run as kpi_rollup_run
    from app.jobs.notification_dispatch_job import run as notification_dispatch_run
    from app.jobs.grievance_escalation_job import run as grievance_escalation_run
    from app.jobs.processor_alert_job import run as processor_alert_run
    from app.jobs.renewal_reminders_job import run as renewal_reminders_run
    from app.jobs.retention_scan_job import run as retention_scan_run

    # Every job also gets an immediate first run at `now` (rather than only
    # after its first full interval elapses, which is APScheduler's default
    # for an "interval" trigger). Without this, R1-07's daily/hourly jobs
    # (audit_chain_verify, kpi_rollup, ...) never produce a single
    # `scheduler_runs` row across a dev session or a container's uptime that
    # is shorter than their interval - which is exactly how this gap first
    # went unnoticed (17 minutes of uptime, zero rows).
    now = datetime.now(timezone.utc)

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        lambda: job_runner.run("expire_consents", expire_consents_run),
        "interval", minutes=15, id="expire_consents", replace_existing=True, next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("renewal_reminders", renewal_reminders_run),
        "interval", hours=24, id="renewal_reminders", replace_existing=True, next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("context_token_cleanup", context_token_cleanup_run),
        "interval", hours=1, id="context_token_cleanup", replace_existing=True, next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("kpi_rollup", kpi_rollup_run),
        "interval", hours=1, id="kpi_rollup", replace_existing=True, next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("audit_chain_verify", audit_chain_run),
        "interval", hours=24, id="audit_chain_verify", replace_existing=True, next_run_time=now,
    )
    # R3-06: dispatches PENDING notifications (with retry/backoff) - runs
    # frequently since a queued notification should not sit unsent for long,
    # and a failed attempt's own backoff (see
    # app/services/notifications.py::_backoff_minutes) is what actually
    # paces retries, not this interval.
    sched.add_job(
        lambda: job_runner.run("notification_dispatch", notification_dispatch_run),
        "interval", minutes=5, id="notification_dispatch", replace_existing=True, next_run_time=now,
    )
    # R3-07: delivers queued processor instructions and escalates the ones
    # whose acknowledgement SLA has elapsed. The interval must stay well below
    # the shortest ack SLA any processor is configured with (default 24h), or
    # K-08's escalations fire long after the breach they report.
    sched.add_job(
        lambda: job_runner.run("processor_alert_dispatch", processor_alert_run),
        "interval", minutes=15, id="processor_alert_dispatch", replace_existing=True,
        next_run_time=now,
    )
    # R1-10: re-checks the retention floors daily and leaves a dated trail.
    # Read-only by design - enforcement is an authorised, actor-attributed act
    # (POST /retention/enforce), never something a background job does.
    sched.add_job(
        lambda: job_runner.run("retention_scan", retention_scan_run),
        "interval", hours=24, id="retention_scan", replace_existing=True, next_run_time=now,
    )
    # R2-06: escalates grievances past the tenant's published response period
    # to the DPO. Hourly is well inside the shortest period a tenant can set
    # (grievance_response_days is capped at 90 by a DB check constraint but may
    # be far shorter), so an overdue item is never sitting unescalated for long.
    sched.add_job(
        lambda: job_runner.run("grievance_escalation", grievance_escalation_run),
        "interval", hours=1, id="grievance_escalation", replace_existing=True,
        next_run_time=now,
    )
    # R1-06: the erasure engine's four jobs. The first two only ever PROPOSE
    # work - an unauthorised proposal is inert - and the fourth is the one
    # background job in this platform that can destroy personal data, which is
    # why it acts exclusively on jobs a named actor has already authorised and
    # whose R.8(2) notice period has elapsed. See each job module's docstring.
    #
    # Daily is right for the two clocks (they measure in days and years) and
    # hourly for the notice and executor sweeps: the notice must go out well
    # inside the 48-hour window it opens, and an erasure that became due an
    # hour ago should not wait a day.
    sched.add_job(
        lambda: job_runner.run("erasure_retention_scan", erasure_retention_scan_run),
        "interval", hours=24, id="erasure_retention_scan", replace_existing=True,
        next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("inactivity_scan", inactivity_scan_run),
        "interval", hours=24, id="inactivity_scan", replace_existing=True, next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("pre_erasure_notices", pre_erasure_notice_run),
        "interval", hours=1, id="pre_erasure_notices", replace_existing=True, next_run_time=now,
    )
    sched.add_job(
        lambda: job_runner.run("erasure_executor", erasure_executor_run),
        "interval", hours=1, id="erasure_executor", replace_existing=True, next_run_time=now,
    )
    sched.start()
    logger.info("Scheduler started with jobs: %s", [j.id for j in sched.get_jobs()])
    _scheduler = sched
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
