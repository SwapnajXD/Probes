from datetime import datetime, timedelta, timezone

import checker


def test_api_status_returns_json_with_unknown_initial_state(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [{"name": "new", "url": "${TEST_API_URL}"}])
    monkeypatch.setenv("TEST_API_URL", "https://example.com/health")
    response = app_module.app.test_client().get("/api/status")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_json() == [{
        "name": "new",
        "url": "https://example.com/health",
        "is_up": None,
        "status_code": None,
        "latency_ms": None,
        "checked_at": None,
        "uptime_pct": None,
        "incidents": [],
    }]


def test_api_returns_latest_state_and_24_hour_uptime(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [{"name": "svc", "url": "https://example.com"}])
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)

    class Clock:
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr(app_module, "datetime", Clock)
    checker.record_check("svc", False, 503, 100, (now - timedelta(hours=25)).isoformat())
    checker.record_check("svc", True, 200, 20, (now - timedelta(hours=2)).isoformat())
    checker.record_check("svc", False, None, 30.123, now.isoformat())
    # An older row inserted later must not replace the latest result.
    checker.record_check("svc", True, 200, 10, (now - timedelta(hours=1)).isoformat())
    result = app_module.app.test_client().get("/api/status").get_json()[0]
    assert result["is_up"] is False
    assert result["status_code"] is None
    assert result["latency_ms"] == 30.1
    assert result["checked_at"] == now.isoformat()
    assert result["uptime_pct"] == 66.7


def test_api_breaks_equal_timestamp_ties_consistently_with_checker(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [{"name": "svc", "url": "https://example.com"}])
    timestamp = datetime.now(timezone.utc).isoformat()
    checker.record_check("svc", True, 200, 1, timestamp)
    checker.record_check("svc", False, 503, 2, timestamp)
    result = app_module.app.test_client().get("/api/status").get_json()[0]
    assert result["is_up"] is checker.get_last_status("svc") is False
    assert result["status_code"] == 503


def test_api_reports_recent_incidents_for_correct_target(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [
        {"name": "svc", "url": "https://example.com"},
        {"name": "other", "url": "https://example.org"},
    ])
    for day in range(1, 5):
        checker.open_incident("svc", f"2026-01-0{day}T00:00:00+00:00")
        checker.close_incident("svc", f"2026-01-0{day}T00:05:30+00:00")
    checker.open_incident("svc", "2026-01-05T00:00:00+00:00")
    results = app_module.app.test_client().get("/api/status").get_json()
    assert results[0]["incidents"] == [
        {"started_at": "2026-01-05T00:00:00+00:00", "ended_at": None, "duration": "ongoing"},
        {"started_at": "2026-01-04T00:00:00+00:00", "ended_at": "2026-01-04T00:05:30+00:00", "duration": "5m"},
        {"started_at": "2026-01-03T00:00:00+00:00", "ended_at": "2026-01-03T00:05:30+00:00", "duration": "5m"},
    ]
    assert results[1]["incidents"] == []


def test_api_returns_empty_json_list_without_targets(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [])
    response = app_module.app.test_client().get("/api/status")
    assert response.status_code == 200
    assert response.get_json() == []
