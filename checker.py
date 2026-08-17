import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests

DB_PATH = "health.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            is_up INTEGER NOT NULL,
            status_code INTEGER,
            latency_ms REAL
        )
        """
    )
    conn.commit()
    conn.close()


def record_check(service, is_up, status_code, latency_ms):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO checks (service, checked_at, is_up, status_code, latency_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (service, datetime.now(timezone.utc).isoformat(), int(is_up), status_code, latency_ms),
    )
    conn.commit()
    conn.close()


def check_once(service):
    name, url = service["name"], service["url"]
    start = time.time()
    try:
        resp = requests.get(url, timeout=10)
        latency_ms = (time.time() - start) * 1000
        # Treat anything under 500 as "up" - a 404 means the server answered,
        # which is what matters for a keep-alive/uptime check.
        is_up = resp.status_code < 500
        record_check(name, is_up, resp.status_code, latency_ms)
    except requests.RequestException:
        latency_ms = (time.time() - start) * 1000
        record_check(name, False, None, latency_ms)


def run_checks_forever(services, interval):
    init_db()
    while True:
        for svc in services:
            check_once(svc)
        time.sleep(interval)


def start_background_checker(services, interval):
    thread = threading.Thread(
        target=run_checks_forever, args=(services, interval), daemon=True
    )
    thread.start()
