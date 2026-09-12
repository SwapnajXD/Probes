# Prometheus, Grafana, and Telegram

Probes exposes metrics for Prometheus to scrape. Grafana reads Prometheus, evaluates
alert rules, and routes notifications to Telegram. Grafana owns the primary notification
path; leave Probes' optional `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` unset to avoid
duplicate in-app notifications.

The [case study](CASE-STUDY.md) records a successful firing notification when Cloud Sentinel
and Aliasly were down. The repository has no exported dashboard, alert rule, notification
policy, or resolved-notification evidence. The settings below are a reproducible example,
not a claim about the current external stack. Record actual settings when verifying it.

## Prometheus scrape setup

Render scrape URL: `https://probes-794f.onrender.com/metrics`.

Merge this job into your existing Prometheus configuration's `scrape_configs` list:

```yaml
scrape_configs:
  - job_name: probes
    scheme: https
    metrics_path: /metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    static_configs:
      - targets:
          - probes-794f.onrender.com
```

Keep any existing jobs. Validate the complete configuration before reloading Prometheus:

```bash
promtool check config /path/to/prometheus.yml
```

Reload using your Prometheus installation's existing procedure. See the
[Prometheus configuration reference](https://prometheus.io/docs/prometheus/latest/configuration/configuration/).
The scrape timeout must not exceed the scrape interval.

In Prometheus' Targets view, verify the `probes` job has state `UP`, a recent successful
scrape, and the expected URL. Run `up{job="probes"}` in its query view; the value should be 1.
Then run `service_up{job="probes"}` to inspect monitored targets. The two metrics answer
different questions: scrape success and monitored-site reachability.

## Metrics and semantics

| Metric | Labels | Meaning |
|---|---|---|
| `service_up` | `service` (target name) | Gauge: 1 for reachable; 0 for down or not yet checked |
| `service_latency_ms` | `service` (target name) | Gauge: latest check latency in milliseconds, including failed requests; omitted before a measurement exists |
| `up` | Added by Prometheus, including `job` and `instance` | 1 when the scrape succeeds; 0 when it fails; absent if no matching target is configured |

Probes currently treats HTTP responses below 500, including 4xx, as reachable. Names are
escaped in metric labels; URLs are not metric labels. Use unique names in `services.yaml`
because they also identify database history and incidents.

The exporter reads stored results: a successful scrape does not prove the checker is making
progress. Inspect `/api/status` and its `checked_at` timestamps when results look stale.
A dedicated last-check-age metric and alert remain future improvements.

Check the endpoint directly:

```bash
curl --fail --show-error --connect-timeout 10 --max-time 60 \
  https://probes-794f.onrender.com/metrics
```

The current code returns Prometheus text format with gauge metadata. The endpoint tests
verify values, label escaping, content type, and recovery updates; live output still needs
to be captured after the updated code is deployed.

## Grafana data source and dashboard

Add a Prometheus data source in Grafana using the Prometheus server URL reachable from
Grafana itself. For containers on the same network this might be `http://prometheus:9090`;
`localhost` refers to the Grafana container in that setup. Use **Save & test** to verify
connectivity. See [Grafana's Prometheus data source setup](https://grafana.com/docs/grafana/latest/datasources/prometheus/configure/).

Create a dashboard named `Probes` with these example panels, a last-24-hours time range,
and a 30-second refresh:

| Panel | PromQL | Display |
|---|---|---|
| Monitored target status | `service_up{job="probes"}` | Stat per `service`; 1 = Up, 0 = Down or unchecked |
| Target latency | `service_latency_ms{job="probes"}` | Time series in milliseconds; legend `{{service}}` |
| Observed availability over 24h | `100 * avg_over_time(service_up{job="probes"}[24h])` | Percent, 0–100, per service |
| Probes scrape status | `up{job="probes"}` | Stat; 1 = scrape succeeds, 0 = scrape fails |

The availability panel averages scraped gauge samples. Repeated scrapes can repeat the same
stored check; missing scrapes are not automatically counted as downtime. It is an observation
ratio, not a precise duration-based SLO or the same calculation as the SQLite API's check ratio.
Do not convert missing data to an Up value. Export the verified dashboard JSON with its data
source mapping when capturing evidence.

## Grafana alert rules and thresholds

Create Grafana-managed rules in a `Probes` folder and a 30-second evaluation group. These
are example thresholds to review against the deployed check interval and normal latency:

| Rule | Prometheus query A (Instant) | Threshold condition B | Pending period |
|---|---|---|---|
| `ProbesTargetDown` | `service_up{job="probes"}` | A is below 1 | 2 minutes |
| `ProbesScrapeFailed` | `up{job="probes"}` | A is below 1 | 2 minutes |
| `ProbesTargetSlow` (optional) | `service_latency_ms{job="probes"} and on(job, instance, service) (service_up{job="probes"} == 1)` | A is above 2000 | 5 minutes |

For each rule, make B the alert condition. If your Grafana version requires a Reduce
expression, reduce A with Last and apply the threshold to that result. Preserve the service
labels so target alerts have a separate instance per target. Add the static label `app=probes`
and `severity=warning`, and a runbook annotation pointing to this repository's `RUNBOOK.md`.
Set Keep firing for to None (0) for the initial lab test.

Use No Data and Error states for query failures rather than treating them as Normal.
Grafana can emit separate `DatasourceNoData` and `DatasourceError` alerts; route these as
monitoring failures too. Missing-series resolution is not proof that a target recovered:
confirm a fresh `service_up=1` and the closed incident. See
[Grafana-managed rule configuration](https://grafana.com/docs/grafana/latest/alerting/alerting-rules/create-grafana-managed-rule/).

The two-minute pending period starts after Grafana first observes a failing value. Detection
also depends on the check cycle, scrape timing, and evaluation timing. A repeated stale value
does not prove two minutes of fresh failures. Initially unchecked targets also export zero;
confirm a baseline check before judging an alert test.

## Telegram contact point and notification policy

In Grafana, add a contact point named `probes-telegram` with the Telegram integration.
Use an existing bot or create one with BotFather, and make sure the bot can access the intended
chat. Enter the bot token and chat ID privately in Grafana. Never put them in repository files,
exported configuration, screenshots, or command transcripts. Follow the
[Grafana Telegram guide](https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/configure-telegram/).

Keep resolved messages enabled (leave **Disable resolved message** off if shown). Use the
contact point's Test function and confirm receipt in an intended test chat. This validates
delivery only; it does not prove the real rule condition or policy routes correctly.

Choose the rules' Advanced notification routing through notification policies. Add a child
policy matching `app=probes`, selecting `probes-telegram`. Suggested lab timing:

| Policy setting | Example value |
|---|---|
| Group by | `alertname`, `service`, `instance` |
| Group wait | 30 seconds |
| Group interval | 1 minute |
| Repeat interval | 4 hours |

Preview routing for each rule. Separately verify that `DatasourceNoData` and `DatasourceError`
reach the intended contact point: do not assume they carry `app=probes`. On a shared Grafana
instance, use matching policies scoped to the actual data source/rule labels and preserve the
existing default receiver. Check for silences or mute timings when testing. See
[notification policy configuration](https://grafana.com/docs/grafana/latest/alerting/configure-notifications/create-notification-policy/).

## Controlled down and recovery test

Use a disposable local Probes checkout and a dedicated test chat. Keep the production
monitoring configuration intact. This procedure assumes Probes, Prometheus, and the test
target run on the same host; container installations need addresses reachable across their
networks. It has not been executed as part of writing this guide.

Save this target server as `lab_target.py` outside the production checkout and run it with
`python3 lab_target.py`:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

down_file = Path("/tmp/probes-alert-lab-down")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503 if down_file.exists() else 200)
        self.end_headers()
        self.wfile.write(b"Probes alert lab\n")


HTTPServer(("127.0.0.1", 8091), Handler).serve_forever()
```

In the disposable checkout, set `services.yaml` to:

```yaml
check_interval_seconds: 10
services:
  - name: alert_lab
    url: http://127.0.0.1:8091/health
```

Leave in-app Telegram credentials unset and start the development app with
`CHECK_INTERVAL_SECONDS=10 DATABASE_PATH=/tmp/probes-alert-lab.db python3 app.py`.
Add a separate Prometheus job called `probes-lab`, using `scheme: http`, target
`127.0.0.1:8080`, path `/metrics`, and the same scrape settings as above. Copy the target-down
rule with `job="probes-lab"`, label `app=probes-lab`, and a policy routing only to the test chat.

1. Ensure `/tmp/probes-alert-lab-down` is absent. Confirm the target returns 200, a fresh
   API result shows `alert_lab` up, Prometheus sees `service_up=1`, and the lab rule is Normal.
2. Run `touch /tmp/probes-alert-lab-down`. The target now returns 503 while Probes stays alive.
3. Confirm a fresh down check, one active incident, and `service_up=0`. Keep the target failing
   through the two-minute pending period and notification delay. Confirm the rule reaches
   Firing and the intended Telegram chat receives a firing message for `alert_lab`.
4. Wait for several more failed checks. Confirm the active incident keeps its original
   `started_at` rather than creating duplicate incidents. Capture the firing evidence.
5. Remove only the marker with `python3 -c 'from pathlib import Path; Path("/tmp/probes-alert-lab-down").unlink(missing_ok=True)'`.
   Confirm the target returns 200, a fresh check shows up, the incident has `ended_at`, and
   the metric becomes 1.
6. Confirm the rule returns to Normal and Telegram receives a resolved notification for the
   same target. Capture timestamps for the state change, incident closure, and both messages.
7. Stop the lab processes and remove the dedicated lab scrape job, alert rule, and policy.
   Keep the evidence with the tested configuration. A contact point test alone is insufficient
   for either the firing or recovery result.

## Evidence checklist

Record the tested commit, environment, component versions, timestamps, and actual rule/policy
settings with each capture. Screenshots must be genuine and redact bot tokens and chat IDs.

| Component | Evidence | Current repository status |
|---|---|---|
| `/metrics` | Live curl output with HTTP headers and samples | Endpoint tests pass locally; updated live curl capture pending |
| Prometheus | Target `UP`, scrape URL, and last scrape time | Integration recorded in case study; current capture pending |
| Grafana | Dashboard screenshot and sanitized JSON export | Export and screenshot pending |
| Alerting | Rule query, threshold, timing, Firing state, and policy match | Historical firing recorded; actual configuration export/capture pending |
| Telegram | Firing message showing target and timestamp | Receipt recorded in case study; screenshot pending |
| Recovery | Resolved message plus fresh up check and incident closure | End-to-end recovery verification and screenshot pending |

No Grafana rules, external configuration, or Telegram messages were changed or sent while
preparing this guide. Use the [runbook](../RUNBOOK.md) for diagnosis if a verification step fails.
