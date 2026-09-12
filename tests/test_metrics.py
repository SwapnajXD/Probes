import pytest

import checker


def test_metrics_exposition_format_and_values(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [
        {"name": "healthy", "url": "https://private.example/up"},
        {"name": "offline", "url": "https://private.example/down"},
    ])
    checker.record_check("healthy", True, 200, 12.5, "2026-01-01T00:00:00+00:00")
    checker.record_check("offline", False, None, 5000.0, "2026-01-01T00:00:00+00:00")
    response = app_module.app.test_client().get("/metrics")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert response.get_data(as_text=True) == (
        "# HELP service_up Latest target reachability: 1 for up, 0 for down or not yet checked.\n"
        "# TYPE service_up gauge\n"
        'service_up{service="healthy"} 1\n'
        'service_up{service="offline"} 0\n'
        "# HELP service_latency_ms Latest target check latency in milliseconds.\n"
        "# TYPE service_latency_ms gauge\n"
        'service_latency_ms{service="healthy"} 12.5\n'
        'service_latency_ms{service="offline"} 5000.0\n'
    )


@pytest.mark.parametrize(("name", "escaped"), [
    ('API "primary"', r'API \"primary\"'),
    ("region\\api", r"region\\api"),
    ("first\nsecond", r"first\nsecond"),
    ('api\\"\n東京', r'api\\\"\n東京'),
    ('x"} 1\nfake_metric{secret="token', r'x\"} 1\nfake_metric{secret=\"token'),
])
def test_labels_escape_special_characters_without_injecting_samples(
    app_module, monkeypatch, name, escaped
):
    monkeypatch.setattr(app_module, "SERVICES", [
        {"name": name, "url": "https://private.example/?token=secret"},
    ])
    checker.record_check(name, True, 200, 1, "2026-01-01T00:00:00+00:00")
    text = app_module.app.test_client().get("/metrics").get_data(as_text=True)
    samples = [line for line in text.split("\n") if line and not line.startswith("#")]
    assert samples == [
        f'service_up{{service="{escaped}"}} 1',
        f'service_latency_ms{{service="{escaped}"}} 1.0',
    ]
    assert "private.example" not in text


def test_unchecked_target_preserves_zero_status_and_omits_latency(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [{"name": "new", "url": "https://example.com"}])
    text = app_module.app.test_client().get("/metrics").get_data(as_text=True)
    assert 'service_up{service="new"} 0\n' in text
    assert "service_latency_ms{" not in text


def test_empty_configuration_has_metadata_without_samples(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [])
    response = app_module.app.test_client().get("/metrics")
    assert response.status_code == 200
    lines = response.get_data(as_text=True).splitlines()
    assert len(lines) == 4
    assert all(line.startswith("# ") for line in lines)


def test_metrics_refresh_after_recovery(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICES", [{"name": "svc", "url": "https://example.com"}])
    client = app_module.app.test_client()
    checker.record_check("svc", False, 503, 100, "2026-01-01T00:00:00+00:00")
    assert 'service_up{service="svc"} 0\n' in client.get("/metrics").get_data(as_text=True)
    checker.record_check("svc", True, 200, 10, "2026-01-01T00:01:00+00:00")
    text = client.get("/metrics").get_data(as_text=True)
    assert 'service_up{service="svc"} 1\n' in text
    assert 'service_latency_ms{service="svc"} 10.0\n' in text
