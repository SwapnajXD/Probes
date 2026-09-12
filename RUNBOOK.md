# Probes Runbook

Live host: Render at `https://probes-794f.onrender.com`. EKS is a temporary learning
deployment that was deleted after testing. Use the Kubernetes steps only against an
existing lab cluster; an incident in Render does not require provisioning AWS resources.

## Check live service

```bash
curl --fail --show-error --connect-timeout 10 --max-time 60 \
  https://probes-794f.onrender.com/health/live
```

Expected: HTTP 200 and `{"status":"ok"}`. This checks whether Flask responds, independently
of monitored websites and SQLite. A 404 may mean the deployed revision predates the health
routes; compare the deployed commit with the repository before diagnosing a process failure.

## Check application status

```bash
curl --fail --show-error --connect-timeout 10 --max-time 60 \
  https://probes-794f.onrender.com/api/status
curl --include --show-error --connect-timeout 10 --max-time 60 \
  https://probes-794f.onrender.com/health/ready
curl --include --show-error --connect-timeout 10 --max-time 60 \
  https://probes-794f.onrender.com/health/startup
```

`/api/status` returns a JSON list. Inspect `name`, `is_up`, `checked_at`, `status_code`,
`latency_ms`, and `incidents`. An unchecked target has `is_up: null`. Compare `checked_at`
with the current time: a responding Flask process does not prove the checker is producing
fresh results. The checker waits `CHECK_INTERVAL_SECONDS` after each sequential check cycle,
so the time between samples also includes time spent checking all targets.

| Endpoint | HTTP 200 means | HTTP 503 means |
|---|---|---|
| `/health/live` | Flask responds | This route does not return 503; investigate the host or proxy if one is received |
| `/health/ready` | Initialization completed and both SQLite tables can be queried | Initialization incomplete, or database inaccessible, locked, or missing required tables |
| `/health/startup` | Initial database setup and checker startup completed | Initialization has not completed successfully |

Startup success remains latched after initialization. Readiness checks database access again
on every request. Neither endpoint becomes unhealthy merely because a monitored website fails.

## Check metrics

```bash
curl --include --show-error --connect-timeout 10 --max-time 60 \
  https://probes-794f.onrender.com/metrics
```

Expected: HTTP 200, `Content-Type: text/plain; version=0.0.4; charset=utf-8`, and
`service_up` / `service_latency_ms` gauge metadata and samples for configured targets.
`service_up` is 1 for up and 0 for down or not yet checked. Latency is omitted until a
measurement exists. Labels identify targets by name.

If the endpoint responds but Grafana has no data, inspect the Prometheus target's scrape
error and last scrape time, then check the Grafana data source and panel query. A target
being `UP` in Prometheus means the scrape succeeded; `service_up` describes the monitored
website. Check for stale `checked_at` values even when scraping succeeds.

## When a target is down

1. Open the Grafana alert and identify the target name, firing time, and configured threshold.
2. Confirm its status and incident start time in `/api/status`. Distinguish a real down result
   from a new target with no check history.
3. Inspect Render logs for that target's `event=health_check` entries. Look for `status=down`,
   latency, and errors such as `ConnectTimeout`, `ReadTimeout`, `SSLError`, or `ConnectionError`.
4. Check the configured target URL manually. For a public URL, replace the example below:

   ```bash
   curl --location --output /dev/null --silent --show-error \
     --connect-timeout 3 --max-time 15 \
     --write-out 'http=%{http_code} total_seconds=%{time_total}\n' \
     'https://YOUR-TARGET-DOMAIN/health'
   ```

5. Check DNS resolution, TLS certificate validity, connectivity, and the target's own logs.
   Keep TLS verification enabled. A successful check from your laptop does not establish
   connectivity from Render. Probes currently counts HTTP responses below 500, including
   4xx, as reachable.
6. Confirm a fresh successful check, closure of the active incident, Grafana's resolved state,
   and receipt of a resolved Telegram notification if configured. Allow for the check interval,
   scrape interval, and alert evaluation/notification settings.
7. Record the cause and recovery evidence using the incident record below.

## When Probes itself is unavailable

1. Check the existing Render service's deployment status and deployed commit.
2. Read its build and runtime logs. Look for import errors, invalid configuration,
   `event=initialization_failed`, worker exits, and repeated restarts.
3. Check `/health/live`, `/health/ready`, and `/health/startup` separately. Confirm Render's
   health-check path is `/health/live`, the container binds to `PORT`, and a Docker command
   override is not bypassing the repository's command.
4. Check the latest [GitHub Actions run](https://github.com/SwapnajXD/Probes/actions/workflows/ci.yml)
   for the deployed commit. Render deployment is not yet gated by CI, so a deployment does
   not establish that tests or scans passed.
5. If a recent deploy caused the outage, identify a compatible previous successful deploy
   and roll back from the service's Deploys page. Check its port, health path, and database
   configuration, especially if reverting to a revision without the new health routes.
   A dashboard rollback disables automatic deploys; reenable them after fixing and verifying
   the underlying issue. See [Render rollback behavior](https://render.com/docs/rollbacks).
6. Repeat health, API, and metrics checks. Verify that target timestamps advance and incident
   tracking resumes. Record the deployed revision and recovery time.

## When storage or the checker fails

- For `event=initialization_failed`, verify `DATABASE_PATH`, its parent directory permissions,
  and available disk space. The configured Render path is `/app/data/health.db`.
- For readiness 503 with liveness 200, inspect database access and locking. Keep one Gunicorn
  worker and one application instance for the current SQLite/in-process checker design.
- For stale checks, inspect `event=checker_error` and recent `event=health_check` entries.
  A failed write rolls back the check and incident change, logs the error, and continues to
  the next target. Fix the storage error before assuming a network failure.
- `event=checker_stop_timeout` means the thread did not finish within its shutdown wait.
  Check the final target log and deployment timing; request timeouts are not total deadlines.
- Preserve available history before replacing a service or container. Without persistent
  storage, recreation or redeployment can erase SQLite data. A code rollback does not recover
  lost incident history. Do not delete the database as a routine recovery step.

## Kubernetes lab debugging

First confirm the cluster and namespace:

```bash
kubectl config current-context
kubectl get pods -A -l app=probes
```

The current manifests omit a namespace, so use the namespace shown above (often `default`).
The planned lab namespace is `probes`; use the following commands once the workload is there,
or replace `-n probes` with its actual namespace. Replace `POD_NAME` with a listed pod name.

```bash
kubectl get pods -n probes
kubectl describe pod POD_NAME -n probes
kubectl logs POD_NAME -n probes --tail=200
kubectl logs POD_NAME -n probes --previous --tail=200
kubectl get events -n probes --sort-by=.lastTimestamp
kubectl get endpointslice -n probes -l kubernetes.io/service-name=probes
kubectl get service probes -n probes -o yaml
kubectl get nodes
kubectl get daemonset aws-node -n kube-system
```

Previous logs are available only when a previous container instance exists. For a Service
with no ready endpoints, inspect its selector, pod labels, readiness, and target port. The
existing lab Service targets port 8080; an updated image needs `PORT=8080` until the manifests
are updated together. See [Kubernetes Service debugging](https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/).

For EKS nodes stuck `NotReady`, inspect node conditions and the `aws-node` DaemonSet before
changing addons. Read the [EKS VPC CNI postmortem](docs/eks-vpc-cni-incident.md) for the historical investigation.
The current manifests do not yet include health probes or a PVC; do not claim readiness
traffic removal, probe-driven restarts, or persistent history until those lab changes are tested.

## Incident record

Record timestamps in UTC and redact tokens, chat IDs, and private URL parameters from evidence.

```text
Incident title:
Affected target or Probes component:
Detected at / incident started at (UTC):
Impact and duration:
Deployed commit or image ID:
Observed logs, status, and metrics:
Cause:
Recovery action and recovered at (UTC):
Fresh checks and resolved notification verified:
Follow-up action:
Evidence links:
```
