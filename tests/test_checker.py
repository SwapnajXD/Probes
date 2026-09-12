import logging
import runpy
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

import checker


class Response:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("private token in response URL")


@pytest.fixture(autouse=True)
def no_real_alerts(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


@pytest.mark.parametrize("error", [
    requests.ConnectTimeout("secret URL"),
    requests.ReadTimeout("secret URL"),
    requests.ConnectionError(socket.gaierror("DNS lookup failed")),
    requests.exceptions.SSLError("secret URL"),
    requests.exceptions.InvalidURL("secret URL"),
    requests.exceptions.TooManyRedirects("secret URL"),
])
def test_network_errors_record_down_without_leaking_details(db, monkeypatch, caplog, error):
    def fail(*args, **kwargs):
        assert kwargs["timeout"] == (3, 5)
        raise error

    monkeypatch.setattr(checker.requests, "get", fail)
    with caplog.at_level(logging.INFO, logger="checker"):
        checker.check_once({"name": "portfolio_api", "url": "https://private.example/token"})
    assert checker.get_last_status("portfolio_api") is False
    assert len(checker.get_incidents("portfolio_api")) == 1
    assert 'target="portfolio_api" status=down latency_ms=' in caplog.text
    assert f"error={type(error).__name__}" in caplog.text
    assert "secret URL" not in caplog.text
    assert "private.example" not in caplog.text


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com", "http://", "${UNSET_PROBES_URL}"])
def test_real_url_validation_does_not_crash(db, url, monkeypatch):
    monkeypatch.delenv("UNSET_PROBES_URL", raising=False)
    checker.check_once({"name": "invalid", "url": url})
    assert checker.get_last_status("invalid") is False


def test_latency_uses_monotonic_clock_and_closes_response(db, monkeypatch, caplog):
    response = Response()
    monkeypatch.setattr(checker.requests, "get", lambda *args, **kwargs: response)
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr(checker.time, "monotonic", lambda: next(ticks))
    with caplog.at_level(logging.INFO, logger="checker"):
        checker.check_once({"name": "healthy", "url": "https://example.com"})
    with checker.database() as conn:
        row = conn.execute("SELECT * FROM checks").fetchone()
        assert row["latency_ms"] == 125
        assert row["is_up"] == 1
    assert response.closed
    assert "status=up latency_ms=125.000 error=none" in caplog.text


def test_concurrent_failures_open_only_one_incident(db, monkeypatch):
    barrier = threading.Barrier(4)

    def fail(*args, **kwargs):
        barrier.wait(timeout=5)
        return Response(503)

    monkeypatch.setattr(checker.requests, "get", fail)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(checker.check_once, [{"name": "same", "url": "https://example.com"}] * 4))
    assert len(checker.get_incidents("same")) == 1
    with checker.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 4


def test_incident_failure_rolls_back_check_and_suppresses_alert(db, monkeypatch):
    checker.record_check("svc", True, 200, 1, "2026-01-01T00:00:00+00:00")
    with checker.database() as conn:
        conn.execute("""
            CREATE TRIGGER fail_incident BEFORE INSERT ON incidents
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END
        """)
    alerts = []
    monkeypatch.setattr(checker, "send_telegram_alert", alerts.append)
    monkeypatch.setattr(checker.requests, "get", lambda *args, **kwargs: Response(503))
    with pytest.raises(sqlite3.IntegrityError):
        checker.check_once({"name": "svc", "url": "https://example.com"})
    assert checker.get_last_status("svc") is True
    assert checker.get_incidents("svc") == []
    with checker.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 1
    assert alerts == []


def test_recovery_closes_only_matching_active_incident(db, monkeypatch):
    checker.open_incident("svc", "2026-01-01T00:00:00+00:00")
    checker.close_incident("svc", "2026-01-01T00:01:00+00:00")
    checker.open_incident("svc", "2026-01-01T01:00:00+00:00")
    checker.open_incident("other", "2026-01-01T01:00:00+00:00")

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 1, 1, 1, 5, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(checker, "datetime", type("Clock", (Clock,), {
        "fromisoformat": staticmethod(datetime.fromisoformat),
    }))
    monkeypatch.setattr(checker.requests, "get", lambda *args, **kwargs: Response())
    checker.check_once({"name": "svc", "url": "https://example.com"})
    incidents = checker.get_incidents("svc")
    assert incidents[0]["duration_seconds"] == 330
    assert incidents[1]["duration_seconds"] == 60
    assert checker.get_incidents("other")[0]["ended_at"] is None


def test_backward_clock_cannot_create_negative_downtime(db):
    checker.open_incident("svc", "2026-01-01T01:00:00+00:00")
    checker.close_incident("svc", "2026-01-01T00:59:00+00:00")
    incident = checker.get_incidents("svc")[0]
    assert incident["duration_seconds"] == 0
    assert incident["ended_at"] == incident["started_at"]


def test_loop_continues_after_bad_config_and_storage_failure(db, monkeypatch, caplog):
    stop = threading.Event()
    checked = []
    real_check = checker.check_once
    monkeypatch.setattr(checker.requests, "get", lambda *args, **kwargs: Response())

    def check(service):
        if service.get("name") == "storage_failure":
            raise sqlite3.OperationalError("private database path")
        real_check(service)
        checked.append(service["name"])
        stop.set()

    monkeypatch.setattr(checker, "check_once", check)
    checker.run_checks_forever([
        {}, {"name": "storage_failure"}, {"name": "healthy", "url": "https://example.com"},
    ], 60, stop)
    assert checked == ["healthy"]
    assert checker.get_last_status("healthy") is True
    assert "error=KeyError" in caplog.text
    assert "error=OperationalError" in caplog.text
    assert "private database path" not in caplog.text


def test_failed_target_does_not_block_next_target(db, monkeypatch):
    stop = threading.Event()

    def get(url, **kwargs):
        if url.endswith("/down"):
            raise requests.ConnectTimeout()
        stop.set()
        return Response()

    monkeypatch.setattr(checker.requests, "get", get)
    checker.run_checks_forever([
        {"name": "down", "url": "https://example.com/down"},
        {"name": "up", "url": "https://example.com/up"},
    ], 60, stop)
    assert checker.get_last_status("down") is False
    assert checker.get_last_status("up") is True


def test_stop_interrupts_long_interval_and_start_is_idempotent(monkeypatch):
    checked = threading.Event()
    monkeypatch.setattr(checker, "check_once", lambda service: checked.set())
    thread = checker.start_background_checker([{"name": "test"}], 3600)
    try:
        assert checked.wait(2)
        assert checker.start_background_checker([], 3600) is thread
        assert checker.stop_background_checker(timeout=2)
        assert not thread.is_alive()
    finally:
        checker.stop_background_checker(timeout=2)


def test_shutdown_finishes_current_check_and_skips_remaining_targets(db, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    checked = []

    def get(url, **kwargs):
        checked.append(url)
        entered.set()
        assert release.wait(3)
        return Response()

    monkeypatch.setattr(checker.requests, "get", get)
    thread = checker.start_background_checker([
        {"name": "first", "url": "https://example.com/first"},
        {"name": "second", "url": "https://example.com/second"},
    ], 3600)
    try:
        assert entered.wait(2)
        assert checker.stop_background_checker(timeout=0) is False
        release.set()
        assert checker.stop_background_checker(timeout=2)
        assert not thread.is_alive()
        assert checked == ["https://example.com/first"]
        assert checker.get_last_status("first") is True
        assert checker.get_last_status("second") is None
    finally:
        release.set()
        checker.stop_background_checker(timeout=2)


def test_gunicorn_exit_hook_stops_checker(monkeypatch):
    thread = checker.start_background_checker([], 3600)
    try:
        hooks = runpy.run_path(str(Path(__file__).resolve().parents[1] / "gunicorn.conf.py"))
        hooks["worker_exit"](None, None)
        assert not thread.is_alive()
    finally:
        checker.stop_background_checker(timeout=2)


def test_telegram_http_failure_is_safe_and_redacted(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "private_chat")
    response = Response(401)
    monkeypatch.setattr(checker.requests, "post", lambda *args, **kwargs: response)
    checker.send_telegram_alert("test")
    assert response.closed
    assert "event=telegram_alert_failed error=HTTPError" in caplog.text
    assert "private_token" not in caplog.text
    assert "private_chat" not in caplog.text
    assert "private token in response URL" not in caplog.text


def test_transition_alert_uses_target_name_without_url(db, monkeypatch):
    checker.record_check("svc", True, 200, 1, "2026-01-01T00:00:00+00:00")
    alerts = []
    monkeypatch.setattr(checker, "send_telegram_alert", alerts.append)
    monkeypatch.setattr(checker.requests, "get", lambda *args, **kwargs: Response(503))
    checker.check_once({"name": "svc", "url": "https://secret.example/token"})
    assert len(alerts) == 1
    assert "svc" in alerts[0]
    assert "secret.example" not in alerts[0]
