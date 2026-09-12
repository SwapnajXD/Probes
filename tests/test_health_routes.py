import sqlite3
from contextlib import closing

import pytest

import checker


def test_live_returns_ok(app_module):
    response = app_module.app.test_client().get("/health/live")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_ready_returns_ok_after_initialization(app_module):
    response = app_module.app.test_client().get("/health/ready")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_startup_and_readiness_change_after_initialization(app_module, monkeypatch):
    client = app_module.app.test_client()
    real_init_db = app_module.init_db
    observed = []

    def initialize_storage():
        observed.append(client.get("/health/startup").status_code)
        observed.append(client.get("/health/ready").status_code)
        observed.append(client.get("/health/live").status_code)
        real_init_db()

    monkeypatch.setattr(app_module, "init_db", initialize_storage)
    app_module.initialize_app()

    assert observed == [503, 503, 200]
    assert client.get("/health/startup").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_failed_initialization_keeps_startup_and_readiness_unhealthy(
    app_module, monkeypatch
):
    def fail_initialization():
        raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(app_module, "init_db", fail_initialization)
    app_module.initialize_app()
    client = app_module.app.test_client()
    assert client.get("/health/startup").status_code == 503
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.get_json() == {"status": "not_ready"}
    assert client.get("/health/live").status_code == 200


def test_missing_database_fails_readiness_without_creating_file(
    app_module, monkeypatch, tmp_path
):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(app_module, "DB_PATH", str(missing))
    client = app_module.app.test_client()
    assert client.get("/health/ready").status_code == 503
    assert not missing.exists()
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/startup").status_code == 200


@pytest.mark.parametrize("table", ["checks", "incidents"])
def test_missing_required_table_fails_readiness(app_module, table):
    with closing(sqlite3.connect(app_module.DB_PATH)) as conn:
        conn.execute(f"DROP TABLE {table}")
        conn.commit()
    assert app_module.app.test_client().get("/health/ready").status_code == 503


def test_locked_database_fails_readiness_then_recovers(app_module):
    client = app_module.app.test_client()
    with closing(sqlite3.connect(app_module.DB_PATH)) as conn:
        conn.execute("BEGIN EXCLUSIVE")
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200
        conn.rollback()
    assert client.get("/health/ready").status_code == 200


def test_external_target_failure_does_not_affect_health(app_module):
    checker.record_check("offline_target", False, None, 10, "2026-01-01T00:00:00+00:00")
    checker.open_incident("offline_target", "2026-01-01T00:00:00+00:00")
    client = app_module.app.test_client()
    for endpoint in ("live", "ready", "startup"):
        assert client.get(f"/health/{endpoint}").status_code == 200
