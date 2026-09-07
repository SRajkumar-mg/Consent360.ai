"""Regression test for the root-logger correlation filter bug.

`app.core.utils` configures a log format that includes `%(request_id)s`.
Before the fix, the filter that filled in `request_id` was attached to the
root *logger* object (`logging.getLogger().addFilter(...)`), which only runs
for records logged directly on the root logger. Records emitted by any
*named* logger (e.g. `logging.getLogger("app.jobs")`, which the
scheduler/job modules use) propagate straight to root's handlers without
ever passing through root's own `.filter()`, so `record.request_id` was
never set and any formatter referencing `%(request_id)s` raised
`KeyError: 'request_id'` inside `Handler.emit()`. `Handler.emit()` swallows
that into `Handler.handleError()`, which prints a "--- Logging error ---"
traceback straight to `sys.stderr` on every such log line -- exactly the
path this task's scheduler jobs use to report failures (`logger.exception(...)`
in `app/jobs/scheduler.py`).

The fix (`app/core/utils.py`) installs a `logging.setLogRecordFactory` that
stamps every LogRecord -- regardless of which logger created it -- with
`request_id` up front, so no formatter ever sees a record missing the
attribute.

This test attaches its own handler/formatter directly to a named logger
rather than inspecting `logging.getLogger().handlers`: under this test
suite, `conftest.py`'s autouse fixture runs real Alembic migrations
in-process, and Alembic's `env.py` calls `logging.config.fileConfig(...)`,
which (if it runs first) configures the root logger's handlers itself and
makes `app.core.utils`'s own `logging.basicConfig()` a no-op (per the stdlib
docs, `basicConfig()` does nothing once the root logger already has
handlers). That import-order race is a test-environment artifact, not part
of the bug or the fix, so the test sidesteps it by supplying a fresh
handler/formatter pair -- the same reproduction shape, without depending on
what ended up attached to root.
"""
import io
import logging

from app.core.utils import get_request_id


def test_named_logger_does_not_raise_logging_error(capsys):
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s [%(request_id)s] %(name)s: %(message)s"))

    logger = logging.getLogger("app.jobs.test_logging_regression")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.error("synthetic failure message for logging regression test")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True

    formatted = buffer.getvalue()
    # `Handler.handleError()` always looks up `sys.stderr` dynamically (not a
    # cached stream reference), so pytest's `capsys` reliably intercepts it
    # regardless of the global --capture mode or import-order quirks above.
    stderr_output = capsys.readouterr().err

    assert "--- Logging error ---" not in stderr_output

    expected_request_id = get_request_id()
    assert f"[{expected_request_id}]" in formatted
    assert "synthetic failure message for logging regression test" in formatted
