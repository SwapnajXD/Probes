import logging
import os
import signal
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from flask import Flask, Response, jsonify, render_template

from checker import (
    DB_PATH,
    get_incidents,
    init_db,
    resolve_env,
    start_background_checker,
    stop_background_checker,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


def format_duration(seconds):
    if seconds is None:
        return "ongoing"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"

app = Flask(__name__)
app.extensions["initialized"] = False

with open("services.yaml") as f:
    config = yaml.safe_load(f)

SERVICES = config["services"]
try:
    CHECK_INTERVAL = int(
        os.environ.get("CHECK_INTERVAL_SECONDS", config.get("check_interval_seconds", 120))
    )
except (TypeError, ValueError):
    raise ValueError("CHECK_INTERVAL_SECONDS must be a positive integer") from None
if CHECK_INTERVAL <= 0:
    raise ValueError("CHECK_INTERVAL_SECONDS must be a positive integer")


def initialize_app():
    """Mark startup complete only after storage and the checker initialize."""
    app.extensions["initialized"] = False
    try:
        init_db()
    except (sqlite3.Error, OSError) as exc:
        app.logger.error("event=initialization_failed error=%s", type(exc).__name__)
        return
    start_background_checker(SERVICES, CHECK_INTERVAL)
    app.extensions["initialized"] = True


@app.get("/health/live")
def live():
    return {"status": "ok"}, 200


@app.get("/health/startup")
def startup():
    if not app.extensions["initialized"]:
        return {"status": "starting"}, 503
    return {"status": "ok"}, 200


@app.get("/health/ready")
def ready():
    if not app.extensions["initialized"]:
        return {"status": "not_ready"}, 503
    try:
        # Read-only mode prevents a probe from creating a missing database.
        uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
            conn.execute("SELECT id FROM checks LIMIT 1").fetchone()
            conn.execute("SELECT id FROM incidents LIMIT 1").fetchone()
    except sqlite3.Error:
        return {"status": "not_ready"}, 503
    return {"status": "ok"}, 200


def get_status():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    results = []

    for svc in SERVICES:
        latest = conn.execute(
            "SELECT * FROM checks WHERE service = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
            (svc["name"],),
        ).fetchone()

        total = conn.execute(
            "SELECT COUNT(*) AS c FROM checks WHERE service = ? AND checked_at >= ?",
            (svc["name"], since),
        ).fetchone()["c"]
        up = conn.execute(
            "SELECT COUNT(*) AS c FROM checks WHERE service = ? AND checked_at >= ? AND is_up = 1",
            (svc["name"], since),
        ).fetchone()["c"]

        incidents = [
            {
                "started_at": inc["started_at"],
                "ended_at": inc["ended_at"],
                "duration": format_duration(inc["duration_seconds"]),
            }
            for inc in get_incidents(svc["name"], limit=3)
        ]

        results.append(
            {
                "name": svc["name"],
                "url": resolve_env(svc["url"]),
                "is_up": bool(latest["is_up"]) if latest else None,
                "status_code": latest["status_code"] if latest else None,
                "latency_ms": round(latest["latency_ms"], 1)
                if latest and latest["latency_ms"] is not None
                else None,
                "checked_at": latest["checked_at"] if latest else None,
                "uptime_pct": round((up / total) * 100, 1) if total else None,
                "incidents": incidents,
            }
        )

    conn.close()
    return results


@app.route("/")
def dashboard():
    return render_template("index.html", services=get_status())


@app.route("/api/status")
def api_status():
    return jsonify(get_status())


@app.route("/metrics")
def metrics():
    services = get_status()
    lines = [
        "# HELP service_up Latest target reachability: 1 for up, 0 for down or not yet checked.",
        "# TYPE service_up gauge",
    ]
    for svc in services:
        up_value = 1 if svc["is_up"] else 0
        label = escape_metric_label(svc["name"])
        lines.append(f'service_up{{service="{label}"}} {up_value}')
    lines.extend([
        "# HELP service_latency_ms Latest target check latency in milliseconds.",
        "# TYPE service_latency_ms gauge",
    ])
    for svc in services:
        if svc["latency_ms"] is not None:
            label = escape_metric_label(svc["name"])
            lines.append(
                f'service_latency_ms{{service="{label}"}} {svc["latency_ms"]}'
            )
    return Response(
        "\n".join(lines) + "\n",
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )


def escape_metric_label(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


initialize_app()


if __name__ == "__main__":
    def shutdown(signum, frame):
        stop_background_checker()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    app.run(host="0.0.0.0", port=8080)
