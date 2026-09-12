import atexit
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests

DB_PATH = os.environ.get("DATABASE_PATH", "health.db")
REQUEST_TIMEOUT = (3, 5)  # Connect and read inactivity timeouts, in seconds.
LOGGER = logging.getLogger(__name__)
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_checker_lock = threading.Lock()
_checker_thread = None
_stop_event = threading.Event()


def resolve_env(value):
    """Resolve known ${VAR_NAME} placeholders, leaving missing ones unchanged."""
    return ENV_PATTERN.sub(lambda match: os.environ.get(match[1], match[0]), value)


@contextmanager
def database():
    """Commit or roll back the transaction, and always close the connection."""
    with closing(sqlite3.connect(DB_PATH, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            yield conn


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                is_up INTEGER NOT NULL,
                status_code INTEGER,
                latency_ms REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_seconds REAL
            )
        """)


def _record_check(conn, service, is_up, status_code, latency_ms, checked_at):
    conn.execute(
        "INSERT INTO checks (service, checked_at, is_up, status_code, latency_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (service, checked_at, int(is_up), status_code, latency_ms),
    )


def record_check(service, is_up, status_code, latency_ms, checked_at):
    with database() as conn:
        _record_check(conn, service, is_up, status_code, latency_ms, checked_at)


def _last_status(conn, service):
    row = conn.execute(
        "SELECT is_up FROM checks WHERE service = ? "
        "ORDER BY checked_at DESC, id DESC LIMIT 1",
        (service,),
    ).fetchone()
    return bool(row["is_up"]) if row else None


def get_last_status(service):
    with database() as conn:
        return _last_status(conn, service)


def _open_incident(conn, service, started_at):
    conn.execute(
        "INSERT INTO incidents (service, started_at) SELECT ?, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM incidents WHERE service = ? AND ended_at IS NULL)",
        (service, started_at, service),
    )


def open_incident(service, started_at):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _open_incident(conn, service, started_at)


def _close_incident(conn, service, ended_at):
    row = conn.execute(
        "SELECT id, started_at FROM incidents "
        "WHERE service = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC, id DESC LIMIT 1",
        (service,),
    ).fetchone()
    if row:
        started = datetime.fromisoformat(row["started_at"])
        # A backward wall-clock adjustment must not produce negative downtime.
        ended = max(datetime.fromisoformat(ended_at), started)
        conn.execute(
            "UPDATE incidents SET ended_at = ?, duration_seconds = ? "
            "WHERE id = ? AND ended_at IS NULL",
            (ended.isoformat(), (ended - started).total_seconds(), row["id"]),
        )


def close_incident(service, ended_at):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _close_incident(conn, service, ended_at)


def get_incidents(service, limit=3):
    with database() as conn:
        rows = conn.execute(
            "SELECT started_at, ended_at, duration_seconds FROM incidents "
            "WHERE service = ? ORDER BY started_at DESC, id DESC LIMIT ?",
            (service, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def _save_result(service, is_up, status_code, latency_ms, checked_at):
    # Serialize state transitions across connections; failure rolls back both
    # the sample and incident update. Network requests happen outside this lock.
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = _last_status(conn, service)
        _record_check(conn, service, is_up, status_code, latency_ms, checked_at)
        if is_up:
            _close_incident(conn, service, checked_at)
        else:
            _open_incident(conn, service, checked_at)
    return previous


def send_telegram_alert(message):
    """Optional best-effort notification; never log credentials or response bodies."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        with closing(requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )) as response:
            response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("event=telegram_alert_failed error=%s", type(exc).__name__)


def check_once(service):
    name = service["name"]
    start = time.monotonic()
    error = "none"
    status_code = None
    try:
        url = resolve_env(service["url"])
        with closing(requests.get(url, timeout=REQUEST_TIMEOUT, stream=True)) as response:
            status_code = response.status_code
            # Preserve the project's reachability semantics, including 4xx.
            is_up = status_code < 500
            if not is_up:
                error = "HTTPError"
    except (requests.RequestException, ValueError, TypeError) as exc:
        is_up = False
        error = type(exc).__name__
    latency_ms = (time.monotonic() - start) * 1000
    LOGGER.info(
        "event=health_check target=%s status=%s latency_ms=%.3f error=%s",
        json.dumps(name), "up" if is_up else "down", latency_ms, error,
    )
    now = datetime.now(timezone.utc).isoformat()
    previous = _save_result(name, is_up, status_code, latency_ms, now)
    # First check establishes a baseline; alerts only follow committed transitions.
    if previous is not None and previous != is_up:
        if is_up:
            send_telegram_alert(f"\u2705 RECOVERED: {name} is back up")
        else:
            send_telegram_alert(f"\U0001F534 DOWN: {name} is unreachable")


def run_checks_forever(services, interval, stop_event=None):
    stop_event = stop_event if stop_event is not None else _stop_event
    while not stop_event.is_set():
        for service in services:
            if stop_event.is_set():
                break
            try:
                check_once(service)
            except Exception as exc:  # noqa: BLE001 - isolate every target; redact exception details below
                # Contain malformed config, storage failures, and unexpected bugs
                # to this target. Exception strings may contain secret URLs.
                name = service.get("name", "unknown") if isinstance(service, dict) else "unknown"
                LOGGER.error(
                    "event=checker_error target=%s status=unknown latency_ms=unknown error=%s",
                    json.dumps(name), type(exc).__name__,
                )
        stop_event.wait(interval)
    LOGGER.info("event=checker_stopped")


def start_background_checker(services, interval):
    global _checker_thread, _stop_event
    if interval <= 0:
        raise ValueError("Check interval must be positive")
    with _checker_lock:
        if _checker_thread is not None and _checker_thread.is_alive():
            return _checker_thread
        _stop_event = threading.Event()
        _checker_thread = threading.Thread(
            target=run_checks_forever,
            args=(services, interval, _stop_event),
            name="probes-checker",
            daemon=True,
        )
        _checker_thread.start()
        return _checker_thread


def stop_background_checker(timeout=25):
    with _checker_lock:
        _stop_event.set()
        thread = _checker_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
        if thread.is_alive():
            LOGGER.warning("event=checker_stop_timeout")
            return False
    return True


atexit.register(stop_background_checker)
