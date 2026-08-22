import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests

DB_PATH = "health.db"

# Matches ${VAR_NAME} inside a URL string in services.yaml
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_env(value):
    """Replace ${VAR_NAME} in a string with os.environ['VAR_NAME'].
    Leaves it untouched (still a literal ${VAR_NAME}) if the env var
    isn't set - that way an unconfigured service just fails its check
    instead of crashing the app."""
    def _sub(match):
        return os.environ.get(match.group(1), match.group(0))
    return ENV_PATTERN.sub(_sub, value)


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds REAL
        )
        """
    )
    conn.commit()
    conn.close()


def record_check(service, is_up, status_code, latency_ms, checked_at):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO checks (service, checked_at, is_up, status_code, latency_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (service, checked_at, int(is_up), status_code, latency_ms),
    )
    conn.commit()
    conn.close()


def get_last_status(service):
    """Returns True/False for the most recent check, or None if this
    service has never been checked before (first run)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT is_up FROM checks WHERE service = ? ORDER BY checked_at DESC LIMIT 1",
        (service,),
    ).fetchone()
    conn.close()
    return bool(row["is_up"]) if row else None


def open_incident(service, started_at):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO incidents (service, started_at, ended_at, duration_seconds) "
        "VALUES (?, ?, NULL, NULL)",
        (service, started_at),
    )
    conn.commit()
    conn.close()


def close_incident(service, ended_at):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, started_at FROM incidents "
        "WHERE service = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (service,),
    ).fetchone()
    if row:
        started = datetime.fromisoformat(row["started_at"])
        ended = datetime.fromisoformat(ended_at)
        duration = (ended - started).total_seconds()
        conn.execute(
            "UPDATE incidents SET ended_at = ?, duration_seconds = ? WHERE id = ?",
            (ended_at, duration, row["id"]),
        )
        conn.commit()
    conn.close()


def get_incidents(service, limit=3):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT started_at, ended_at, duration_seconds FROM incidents "
        "WHERE service = ? ORDER BY started_at DESC LIMIT ?",
        (service, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def send_telegram_alert(message):
    """No-ops silently if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID aren't set,
    so alerting is fully optional. Never raises - a failed alert should
    never take down the checker loop."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=5,
        )
    except requests.RequestException:
        pass


def check_once(service):
    name = service["name"]
    url = resolve_env(service["url"])
    previous = get_last_status(name)

    start = time.time()
    try:
        resp = requests.get(url, timeout=10)
        latency_ms = (time.time() - start) * 1000
        # Treat anything under 500 as "up" - a 404 means the server answered,
        # which is what matters for a keep-alive/uptime check.
        is_up = resp.status_code < 500
        status_code = resp.status_code
    except requests.RequestException:
        latency_ms = (time.time() - start) * 1000
        is_up = False
        status_code = None

    now = datetime.now(timezone.utc).isoformat()
    record_check(name, is_up, status_code, latency_ms, now)

    if previous is None:
        # First check ever for this service - just establish a baseline.
        # No alert on startup, but still open an incident if it's down
        # from the very first check, so downtime is tracked from now.
        if not is_up:
            open_incident(name, now)
        return

    if is_up and not previous:
        close_incident(name, now)
        send_telegram_alert(f"\u2705 RECOVERED: {name} is back up ({url})")
    elif not is_up and previous:
        open_incident(name, now)
        send_telegram_alert(f"\U0001F534 DOWN: {name} is unreachable ({url})")


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
