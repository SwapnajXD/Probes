# Probes

**A self-hosted service health dashboard** — pings your backends on a schedule, tracks uptime and
downtime incidents with real duration, exposes Prometheus-compatible metrics, and doubles as a
free way to keep low-traffic personal projects from spinning down on free-tier hosts.

🔗 **Live demo:** [probes-794f.onrender.com](https://probes-794f.onrender.com)
📄 **Full technical case study:** [docs/CASE-STUDY.md](docs/CASE-STUDY.md) — the debugging story,
architecture decisions, and what's intentionally left unfinished

---

## What it does

- **Background health checks** — a scheduler thread pings each configured service on a timer and
  records the result
- **Real incident tracking** — not just "is it up right now," but discrete downtime incidents with
  open/close timestamps and computed duration, stored in SQLite
- **Prometheus-compatible `/metrics`** — plugs straight into an existing Prometheus + Grafana stack
- **Environment-variable config resolution** — `${VAR_NAME}` syntax in `services.yaml` keeps
  secrets and per-environment URLs out of committed config
- **Optional Telegram alerting** on status-change transitions, safe no-op when unconfigured
- **Keeps itself and other free-tier apps alive** — the same periodic-ping mechanism that monitors
  other services also prevents *this* app (and whatever it's watching) from idling out

## Screenshot

The dashboard shows each monitored service with live status, uptime %, latency, and recent
incident history:

```
health@dashboard:~$
auto-refreshing · last 24h uptime

● Portfolio                                    100.0%
  https://swapnaj.vercel.app
  status: 200   latency: 35.7 ms   checked: just now

● Cloud Sentinel API                             0.0%
  status: unreachable
  ↳ down since 09:00:10 · ongoing
```

## Tech stack

| Layer | Choice |
|---|---|
| App | Python, Flask, gunicorn |
| Storage | SQLite |
| Containerization | Docker |
| Orchestration (learning deployment) | Kubernetes on Amazon EKS |
| Production deployment | Render |
| Monitoring integration | Prometheus `/metrics` scrape target + Grafana Alerting |

## Project structure

```
Probes/
├── app.py                 # Flask app: dashboard, JSON API, /metrics endpoint
├── checker.py              # Background thread: health checks, incident tracking, alerting
├── services.yaml            # List of services to monitor (edit this)
├── requirements.txt
├── requirements-dev.txt     # Adds pytest, for running tests
├── Dockerfile
├── static/style.css
├── templates/index.html
├── tests/                   # 22 pytest tests covering checker.py's real logic
│   ├── conftest.py
│   ├── test_resolve_env.py
│   ├── test_incidents.py
│   └── test_check_once.py
├── k8s/                     # Kubernetes manifests (used for the AWS EKS learning deployment)
│   ├── deployment.yaml
│   ├── service.yaml
│   └── ingress.yaml
└── docs/
    └── CASE-STUDY.md        # Full technical writeup, incident debugging, design decisions
```

## Running it locally

```bash
python3 -m venv .venv
source .venv/bin/activate      # fish shell: source .venv/bin/activate.fish
pip install -r requirements.txt
python3 app.py
```

Then visit `http://localhost:8080`.

## Running it with Docker

```bash
docker build -t probes:local .
docker run -d -p 8080:8080 --name probes probes:local
curl http://localhost:8080/api/status
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

22 tests cover the actual logic that matters most: incident open/close transitions, uptime
computation, environment-variable URL resolution, graceful handling of unreachable services, and
the guarantee that a failed Telegram alert never crashes the checker loop. Each test runs against
an isolated temporary database, not `health.db`.

## Configuration

Edit `services.yaml` to add or change monitored services. URLs support `${VAR_NAME}` substitution
from environment variables:

```yaml
check_interval_seconds: 240

services:
  - name: "My API"
    url: "${MY_API_URL}/health"
```

| Environment variable | Required | Purpose |
|---|---|---|
| `PORT` | Only on Render | Port to bind to (Render-specific; defaults to `8080` otherwise) |
| `TELEGRAM_BOT_TOKEN` | No | Enables in-app Telegram alerts on status change |
| `TELEGRAM_CHAT_ID` | No | Destination chat for Telegram alerts |
| *(custom `${VAR}` names used in `services.yaml`)* | No | Resolved from the environment; unresolved vars fail their check gracefully rather than crashing |

## Deploying to Kubernetes

The manifests in `k8s/` were used for a hands-on AWS EKS deployment (see the
[case study](docs/CASE-STUDY.md) for the full walkthrough, including a real Kubernetes networking
incident that came up during setup):

```bash
kubectl apply -f k8s/deployment.yaml -f k8s/service.yaml -f k8s/ingress.yaml
```

These assume an `nginx` Ingress controller is installed and a `probes-secrets` Kubernetes Secret
exists for the environment variables above.

## Known limitations (left intentional, not overlooked)

- **No persistent storage in the Kubernetes deployment** — `health.db` lives on the pod's local
  disk. A pod restart wipes incident history. Documented and deliberately left this way to
  demonstrate the problem before reaching for a `PersistentVolumeClaim`. See the case study.
- **Single gunicorn worker by design** — the background checker thread runs once per process;
  scaling workers would duplicate pings. See the comment in `Dockerfile`.
- **In-app Telegram alerting is dormant** — real alerting is intentionally handled by an external
  Prometheus/Grafana stack instead, not hardcoded into the app. The code path still exists as a
  documented fallback.

## License

MIT — see [LICENSE](LICENSE).
