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
