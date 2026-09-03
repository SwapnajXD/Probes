import types

import requests

import checker


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _service(name="Svc", url="https://example.com"):
    return {"name": name, "url": url}


# ---- check_once: first-ever check (no baseline) ----

def test_first_check_up_opens_no_incident_and_no_alert(db, monkeypatch):
    monkeypatch.setattr(checker.requests, "get", lambda *a, **k: FakeResponse(200))
    alert_calls = []
    monkeypatch.setattr(checker, "send_telegram_alert", lambda msg: alert_calls.append(msg))

    checker.check_once(_service())

    assert checker.get_last_status("Svc") is True
    assert checker.get_incidents("Svc") == []
    assert alert_calls == []  # no alert on first-ever check, even if it were down


def test_first_check_down_opens_incident_but_still_no_alert(db, monkeypatch):
    monkeypatch.setattr(checker.requests, "get", lambda *a, **k: FakeResponse(500))
    alert_calls = []
    monkeypatch.setattr(checker, "send_telegram_alert", lambda msg: alert_calls.append(msg))

    checker.check_once(_service())

    assert checker.get_last_status("Svc") is False
    incidents = checker.get_incidents("Svc")
    assert len(incidents) == 1
    assert incidents[0]["ended_at"] is None  # still open
    # Deliberate design choice: no alert on the very first check, since
    # there's no prior state to compare against - just a baseline.
    assert alert_calls == []


# ---- check_once: real transitions ----

def test_transition_up_to_down_opens_incident_and_alerts(db, monkeypatch):
    checker.record_check("Svc", True, 200, 10.0, "2026-01-01T00:00:00+00:00")

    monkeypatch.setattr(checker.requests, "get", lambda *a, **k: FakeResponse(503))
    alert_calls = []
    monkeypatch.setattr(checker, "send_telegram_alert", lambda msg: alert_calls.append(msg))

    checker.check_once(_service())

    assert checker.get_last_status("Svc") is False
    incidents = checker.get_incidents("Svc")
    assert len(incidents) == 1
    assert incidents[0]["ended_at"] is None
    assert len(alert_calls) == 1
    assert "DOWN" in alert_calls[0]
    assert "Svc" in alert_calls[0]


def test_transition_down_to_up_closes_incident_and_alerts(db, monkeypatch):
    checker.record_check("Svc", False, None, 0.2, "2026-01-01T00:00:00+00:00")
    checker.open_incident("Svc", "2026-01-01T00:00:00+00:00")

    monkeypatch.setattr(checker.requests, "get", lambda *a, **k: FakeResponse(200))
    alert_calls = []
    monkeypatch.setattr(checker, "send_telegram_alert", lambda msg: alert_calls.append(msg))

    checker.check_once(_service())

    assert checker.get_last_status("Svc") is True
    incidents = checker.get_incidents("Svc")
    assert incidents[0]["ended_at"] is not None  # now closed
    assert len(alert_calls) == 1
    assert "RECOVERED" in alert_calls[0]


def test_no_transition_up_to_up_does_not_alert_or_touch_incidents(db, monkeypatch):
    checker.record_check("Svc", True, 200, 10.0, "2026-01-01T00:00:00+00:00")

    monkeypatch.setattr(checker.requests, "get", lambda *a, **k: FakeResponse(200))
    alert_calls = []
    monkeypatch.setattr(checker, "send_telegram_alert", lambda msg: alert_calls.append(msg))

    checker.check_once(_service())

    assert checker.get_incidents("Svc") == []
    assert alert_calls == []


def test_no_transition_down_to_down_does_not_duplicate_incident_or_alert(db, monkeypatch):
    checker.record_check("Svc", False, None, 0.2, "2026-01-01T00:00:00+00:00")
    checker.open_incident("Svc", "2026-01-01T00:00:00+00:00")

    monkeypatch.setattr(checker.requests, "get", lambda *a, **k: FakeResponse(500))
    alert_calls = []
    monkeypatch.setattr(checker, "send_telegram_alert", lambda msg: alert_calls.append(msg))

    checker.check_once(_service())

    # Still exactly one incident, not a second one opened on top of it
    assert len(checker.get_incidents("Svc")) == 1
    assert alert_calls == []


def test_request_exception_is_treated_as_down_not_a_crash(db, monkeypatch):
    def raise_connection_error(*a, **k):
        raise requests.exceptions.ConnectionError("simulated network failure")

    monkeypatch.setattr(checker.requests, "get", raise_connection_error)

    # Should not raise - this is the core promise of the design:
    # bad/unreachable URLs degrade gracefully, they don't crash the checker loop.
    checker.check_once(_service())

    assert checker.get_last_status("Svc") is False


def test_check_once_resolves_env_vars_in_url_before_requesting(db, monkeypatch):
    monkeypatch.setenv("TEST_TARGET", "https://real-target.example.com")

    captured_urls = []

    def fake_get(url, timeout=10):
        captured_urls.append(url)
        return FakeResponse(200)

    monkeypatch.setattr(checker.requests, "get", fake_get)

    checker.check_once(_service(url="${TEST_TARGET}/health"))

    assert captured_urls == ["https://real-target.example.com/health"]


# ---- send_telegram_alert: safety guarantees ----

def test_telegram_alert_noops_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    calls = []
    monkeypatch.setattr(checker.requests, "post", lambda *a, **k: calls.append((a, k)))

    checker.send_telegram_alert("test message")

    assert calls == []  # never even attempted the HTTP call


def test_telegram_alert_calls_api_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append({"url": url, "data": data})
        return FakeResponse(200)

    monkeypatch.setattr(checker.requests, "post", fake_post)

    checker.send_telegram_alert("service is down")

    assert len(calls) == 1
    assert "fake-token" in calls[0]["url"]
    assert calls[0]["data"]["chat_id"] == "12345"
    assert calls[0]["data"]["text"] == "service is down"


def test_telegram_alert_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def raise_error(*a, **k):
        raise requests.exceptions.ConnectionError("simulated failure")

    monkeypatch.setattr(checker.requests, "post", raise_error)

    # A failed alert must never take down the checker loop - this is the
    # entire point of the try/except in send_telegram_alert.
    checker.send_telegram_alert("this should not crash anything")
