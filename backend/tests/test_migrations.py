import os
import subprocess
import sys
from pathlib import Path


def test_alembic_check_reports_no_drift():
    backend_dir = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(backend_dir),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
