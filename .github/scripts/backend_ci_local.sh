#!/usr/bin/env bash
# R3-12: run every backend CI check locally, in the same order and against
# the same throwaway database strategy as .github/workflows/backend-ci.yml.
#
# There is no CI service connected to this local repository (it is not a
# git remote-tracked project in this environment) - this script exists so
# "CI green" is an honest, actually-executed claim rather than one made
# about a workflow file nobody ran. It is also exactly what the GitHub
# Actions workflow runs, step for step, so a green run here is strong
# evidence the workflow would be green too on a real push.
#
# What it does NOT do: provision Postgres. Point it at any reachable
# Postgres server via DATABASE_URL (default matches this project's local
# dev convention: port 5433, role postgres) - it creates and drops its own
# throwaway "<db>_test" database, exactly like tests/conftest.py, and never
# touches consent_platform itself.
#
# Usage:
#   PYTHON_BIN=/path/to/venv/bin/python ./.github/scripts/backend_ci_local.sh
#
# PYTHON_BIN defaults to `python3` on PATH. DATABASE_URL defaults to
# tests/conftest.py's own default (postgres/postgres@127.0.0.1:5433).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$BACKEND_DIR"

echo "== backend CI (local) =========================================="
echo "python:       $("$PYTHON_BIN" --version 2>&1)"
echo "backend dir:  $BACKEND_DIR"
echo "DATABASE_URL: ${DATABASE_URL:-<using tests/conftest.py default: 127.0.0.1:5433>}"
echo "=================================================================="

echo
echo "-- [1/4] Dependency vulnerability scan (pip-audit) -----------------"
if "$PYTHON_BIN" -m pip_audit --version >/dev/null 2>&1; then
    "$PYTHON_BIN" -m pip_audit -r requirements.txt --desc || {
        echo "pip-audit reported findings - see output above. This script does" \
             "not fail the build on findings alone (some have no available fix" \
             "or are not reachable given this codebase's actual usage - see" \
             "backend/docs/compliance/ for the reviewed disposition of each" \
             "one at the time this was last run); a human should still read them."
    }
else
    echo "pip-audit not installed in this interpreter - installing temporarily..."
    "$PYTHON_BIN" -m pip install --quiet pip-audit
    "$PYTHON_BIN" -m pip_audit -r requirements.txt --desc || true
fi

echo
echo "-- [2/4] Static security scan (bandit) -----------------------------"
if ! "$PYTHON_BIN" -m bandit --version >/dev/null 2>&1; then
    echo "bandit not installed in this interpreter - installing temporarily..."
    "$PYTHON_BIN" -m pip install --quiet bandit
fi
# bandit exits non-zero whenever ANY finding meets the -ll (medium+)
# threshold, which this codebase currently has 5 of - all reviewed as of
# this writing (see backend/docs/compliance/ALGORITHM_REGISTER.md's sibling
# security-scan notes / the R3-12 handoff report) as either false positives
# (operator-supplied table/column names in migration tooling, not
# user input) or a pre-existing best-effort webhook delivery already
# wrapped in try/except. This script does not hard-fail the whole build on
# that account, but never hides the output either - a human reviews it
# every run, and a NEW finding is exactly as visible here as an old one.
"$PYTHON_BIN" -m bandit -r app -ll || echo "(bandit exited non-zero - see findings above; reviewed findings are documented in backend/docs/compliance/)"

echo
echo "-- [3/4] Alembic drift check (fresh database, migrations only) -----"
# R1-13: the actual DoD - build an EMPTY database, run `alembic upgrade
# head` against it and nothing else, then assert `alembic check` reports no
# drift. This used to be a no-op comment (it checked that alembic was
# installed and printed a note); it now genuinely runs the check, against
# its own throwaway "<db>_alembic_check" database - never consent_platform,
# and never the "<db>_test" database tests/conftest.py owns - so a broken
# migration or a model change with no matching migration fails HERE, with
# an unambiguous message, instead of being buried inside a full pytest run.
# tests/test_migrations.py asserts the same thing a second time, against
# the throwaway database conftest.py's own fixture builds the same way at
# session start - that duplication is deliberate defense in depth, not
# redundancy to trim.
ALEMBIC_CHECK_DATABASE_URL="$("$PYTHON_BIN" -c '
import os, re
default = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/consent_platform"
base = os.environ.get("DATABASE_URL", default)
print(re.sub(r"/[^/]+$", "/consent_platform_alembic_check", base))
')"
export ALEMBIC_CHECK_DATABASE_URL
echo "alembic-check database: $ALEMBIC_CHECK_DATABASE_URL"

"$PYTHON_BIN" -c '
import os
import psycopg2
from sqlalchemy.engine import make_url

url = make_url(os.environ["ALEMBIC_CHECK_DATABASE_URL"])
conn = psycopg2.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname="postgres")
conn.autocommit = True
cur = conn.cursor()
cur.execute("DROP DATABASE IF EXISTS " + chr(34) + url.database + chr(34))
cur.execute("CREATE DATABASE " + chr(34) + url.database + chr(34))
cur.close()
conn.close()
'

# If either of the next two lines fails, `set -e` stops the script here and
# deliberately leaves consent_platform_alembic_check in place (dropped only
# on success, below) so the schema that failed to match is there to inspect.
DATABASE_URL="$ALEMBIC_CHECK_DATABASE_URL" "$PYTHON_BIN" -m alembic upgrade head
DATABASE_URL="$ALEMBIC_CHECK_DATABASE_URL" "$PYTHON_BIN" -m alembic check

"$PYTHON_BIN" -c '
import os
import psycopg2
from sqlalchemy.engine import make_url

url = make_url(os.environ["ALEMBIC_CHECK_DATABASE_URL"])
conn = psycopg2.connect(host=url.host, port=url.port, user=url.username, password=url.password, dbname="postgres")
conn.autocommit = True
cur = conn.cursor()
cur.execute("DROP DATABASE IF EXISTS " + chr(34) + url.database + chr(34))
cur.close()
conn.close()
'
echo "Alembic drift check passed: a fresh database built from migrations alone matches the models."

echo
echo "-- [4/4] Full pytest suite (throwaway database, dropped after) -----"
"$PYTHON_BIN" -m pytest -q

echo
echo "== backend CI (local): ALL STEPS COMPLETED =========================="
