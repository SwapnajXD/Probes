from datetime import datetime, timedelta, timezone

import checker


def test_get_last_status_returns_none_when_never_checked(db):
    assert checker.get_last_status("NewService") is None


def test_record_check_then_get_last_status_returns_latest(db):
    checker.record_check("Svc", True, 200, 45.0, "2026-01-01T00:00:00+00:00")
    assert checker.get_last_status("Svc") is True

    checker.record_check("Svc", False, None, 0.3, "2026-01-01T00:05:00+00:00")
    assert checker.get_last_status("Svc") is False


def test_get_last_status_orders_by_checked_at_not_insertion_order(db):
    # Insert an "older" check AFTER a "newer" one, out of chronological order,
    # to make sure the query really orders by checked_at and not just
    # "last row inserted".
    checker.record_check("Svc", True, 200, 10.0, "2026-01-01T00:10:00+00:00")
    checker.record_check("Svc", False, None, 0.1, "2026-01-01T00:05:00+00:00")
    assert checker.get_last_status("Svc") is True


def test_open_incident_creates_ongoing_row(db):
    checker.open_incident("Svc", "2026-01-01T00:00:00+00:00")
    incidents = checker.get_incidents("Svc")
    assert len(incidents) == 1
    assert incidents[0]["started_at"] == "2026-01-01T00:00:00+00:00"
    assert incidents[0]["ended_at"] is None
    assert incidents[0]["duration_seconds"] is None


def test_close_incident_computes_correct_duration(db):
    checker.open_incident("Svc", "2026-01-01T00:00:00+00:00")
    checker.close_incident("Svc", "2026-01-01T00:05:30+00:00")

    incidents = checker.get_incidents("Svc")
    assert incidents[0]["ended_at"] == "2026-01-01T00:05:30+00:00"
    assert incidents[0]["duration_seconds"] == 330.0  # 5 min 30 sec


def test_close_incident_does_nothing_if_no_open_incident(db):
    # No incident was ever opened - this should not raise, and should
    # leave incident history empty rather than fabricating a row.
    checker.close_incident("Svc", "2026-01-01T00:05:00+00:00")
    assert checker.get_incidents("Svc") == []


def test_get_incidents_respects_limit_and_recency_order(db):
    for i in range(5):
        started = f"2026-01-0{i+1}T00:00:00+00:00"
        checker.open_incident("Svc", started)
        checker.close_incident("Svc", started)  # closes immediately, fine for this test

    incidents = checker.get_incidents("Svc", limit=3)
    assert len(incidents) == 3
    # Most recent (Jan 5) should come first
    assert incidents[0]["started_at"] == "2026-01-05T00:00:00+00:00"
