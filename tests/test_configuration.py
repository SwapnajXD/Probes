import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_app(tmp_path, interval=None):
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(tmp_path / "nested" / "data" / "health.db")
    env.pop("CHECK_INTERVAL_SECONDS", None)
    if interval is not None:
        env["CHECK_INTERVAL_SECONDS"] = interval
    # Fresh imports exercise environment loading without mutating the checker
    # used by other tests. No background thread or outbound requests are started.
    script = """
import os
import sqlite3
from contextlib import closing
import checker

started = []
checker.start_background_checker = lambda services, interval: started.append(interval)
import app

assert checker.DB_PATH == os.environ['DATABASE_PATH'] == app.DB_PATH
expected_interval = int(os.environ.get('CHECK_INTERVAL_SECONDS', app.config['check_interval_seconds']))
assert started == [expected_interval]
checker.record_check('test_target', True, 200, 1, '2026-01-01T00:00:00+00:00')
checker.init_db()
with closing(sqlite3.connect(checker.DB_PATH)) as conn:
    assert conn.execute('SELECT COUNT(*) FROM checks').fetchone()[0] == 1
client = app.app.test_client()
assert client.get('/health/ready').status_code == 200
assert client.get('/api/status').status_code == 200
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("interval", [None, "60"])
def test_database_path_and_interval_configuration(tmp_path, interval):
    result = run_app(tmp_path, interval)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "nested" / "data" / "health.db").is_file()


@pytest.mark.parametrize("interval", ["0", "-1", "abc", "1.5", ""])
def test_invalid_interval_fails_before_checker_starts(tmp_path, interval):
    result = run_app(tmp_path, interval)
    assert result.returncode != 0
    assert "CHECK_INTERVAL_SECONDS must be a positive integer" in result.stderr
    assert not (tmp_path / "nested").exists()
