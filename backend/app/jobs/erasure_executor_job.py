"""R1-06/G-01, G-04: execute the erasures that are due.

The one background job in this platform that destroys personal data, and it is
hedged accordingly. It acts on a job ONLY when all of the following hold, each
re-checked inside `execute_erasure_job` rather than trusted from the query:

  * the job is NOTIFIED or BLOCKED (never PROPOSED - a clock's inference that
    nobody authorised is not executed by a background pass, however long it
    has been sitting there);
  * `authorised_by` is set, i.e. a named actor took responsibility - either a
    staff user through POST /erasure/jobs/{ref}/authorise, or the data
    principal herself by withdrawing consent or having her s.12(3) request
    approved;
  * the R.8(2) pre-erasure notice was actually sent and its period elapsed;
  * no legal hold covers the principal.

A job blocked by a hold stays BLOCKED and is retried on the next pass, so
releasing the hold is all it takes to let a deferred erasure proceed. A job
that raises is marked FAILED with the error on the row; nothing partial is
committed, since each job executes in one transaction.
"""
from sqlalchemy.orm import Session

from app.services.erasure import execute_due_erasure_jobs


def run(db: Session) -> dict:
    return execute_due_erasure_jobs(db, actor_username="scheduler")
