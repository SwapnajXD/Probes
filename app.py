import sqlite3
from datetime import datetime, timedelta, timezone

import yaml
from flask import Flask, Response, jsonify, render_template

from checker import DB_PATH, init_db, start_background_checker

app = Flask(__name__)

with open("services.yaml") as f:
    config = yaml.safe_load(f)

SERVICES = config["services"]
CHECK_INTERVAL = config.get("check_interval_seconds", 120)

init_db()
start_background_checker(SERVICES, CHECK_INTERVAL)


def get_status():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    results = []

    for svc in SERVICES:
        latest = conn.execute(
            "SELECT * FROM checks WHERE service = ? ORDER BY checked_at DESC LIMIT 1",
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

        results.append(
            {
                "name": svc["name"],
                "url": svc["url"],
                "is_up": bool(latest["is_up"]) if latest else None,
                "status_code": latest["status_code"] if latest else None,
                "latency_ms": round(latest["latency_ms"], 1)
                if latest and latest["latency_ms"] is not None
                else None,
                "checked_at": latest["checked_at"] if latest else None,
                "uptime_pct": round((up / total) * 100, 1) if total else None,
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
    lines = []
    for svc in get_status():
        up_value = 1 if svc["is_up"] else 0
        lines.append(f'service_up{{service="{svc["name"]}"}} {up_value}')
        if svc["latency_ms"] is not None:
            lines.append(
                f'service_latency_ms{{service="{svc["name"]}"}} {svc["latency_ms"]}'
            )
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
