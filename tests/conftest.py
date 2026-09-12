import importlib.util
from pathlib import Path

import pytest

import checker


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point checker at a fresh, isolated SQLite file for each test,
    instead of the real health.db - so tests never touch real data
    and never leak state between each other."""
    db_file = tmp_path / "test_health.db"
    monkeypatch.setattr(checker, "DB_PATH", str(db_file))
    checker.init_db()
    return str(db_file)


@pytest.fixture
def app_module(db, monkeypatch):
    # Real isolated storage, with no background checks or outbound requests.
    monkeypatch.setattr(checker, "start_background_checker", lambda *args: None)
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("route_test_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config["TESTING"] = True
    return module
