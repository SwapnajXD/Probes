# Probes — Technical Case Study

This is the deep-dive version of this project's story. For a quick overview, setup instructions,
and architecture summary, see the main [README](../README.md).

## Why this exists

I had a handful of personal side projects deployed on free-tier hosts, and kept running into the
same annoyance: no traffic for 15 minutes, and the backend would spin down, adding a slow
cold-start delay the next time anyone tried to use it. I wanted something to periodically check on
these services — and if I was building that anyway, it might as well also tell me when one of
them was *actually* down, not just keep it warm.

## Two deployments, two different purposes

This project deliberately used two different deployment targets for two different reasons:

- **AWS EKS** — a temporary, session-based deployment used purely to learn real Kubernetes
  operations: cluster provisioning, node groups, CNI networking, Ingress controllers, Secrets, and
  how pods handle (or fail to handle) persistent state. Provisioned and torn down repeatedly, with
  strict cost discipline verified via the AWS CLI after every session.
- **Render** — the permanent, always-on home for the app's actual job: continuously monitoring
  real backends and being reachable at a stable URL. The manifests in `k8s/` reflect the EKS
  learning exercise; the live deployment linked in the README runs on Render.

## What was actually built and tested (not just planned)

1. App built and unit-tested locally — the background checker was verified against both a
   reachable and an intentionally unreachable URL, confirming graceful failure handling.
2. Containerized with Docker — a single-stage Python image, deliberately pinned to a single
   gunicorn worker (`--workers 1`), since the background checker thread is not safe to run in
   multiple processes without duplicating pings. Documented directly in the Dockerfile.
3. Provisioned a real EKS cluster via `eksctl` — managed control plane, managed node group, VPC
   CNI, kube-proxy, and CoreDNS addons.
4. Installed the ingress-nginx controller, which provisioned a real AWS Elastic Load Balancer.
5. Built and pushed the image to Docker Hub, then deployed via the manifests in `k8s/`, with
   runtime configuration and secrets injected via a Kubernetes `Secret` rather than committed to
   any file.
6. Verified the live EKS deployment by hitting `/api/status` through the real internet-facing
   load balancer.
7. Deliberately killed a running pod to observe the ephemeral-storage behavior of Kubernetes pods
   (see below) — then tore the cluster down and confirmed via the AWS CLI that nothing was left
   running or billing.
8. Deployed to Render as a Docker-based web service, connected directly to this GitHub repo.
9. Diagnosed and fixed a Render-specific port-binding risk before it caused a failed deploy — set
   the `PORT` environment variable explicitly to match the Dockerfile's hardcoded `8080`, rather
   than relying on Render's `EXPOSE`-based auto-detection.
10. Identified and solved a free-tier-specific functional problem: Render's free web services
    sleep after 15 minutes of inactivity, which would silently pause the background checker thread
    entirely. Solved with a free external cron service pinging the app every 10 minutes.
11. Wired the app's `/metrics` endpoint into a self-hosted Prometheus and Grafana stack, with a
    Grafana alert rule routed through an existing Telegram alerting pipeline — rather than relying
    on the dormant in-app Telegram code. **Confirmed working end-to-end**: with Cloud Sentinel and
    Aliasly genuinely down, the alert fired and a real Telegram notification was received.

## A real incident, not a smooth demo

The EKS lab encountered two provisioning failures: an instance-type eligibility problem,
then nodes stuck `NotReady` with no `aws-node` DaemonSet present. Reinstalling the VPC CNI
addon was followed by both nodes becoming `Ready` within approximately one minute.

The full investigation sequence, resolution, lessons, and evidence limitations are now in
the [EKS VPC CNI incident postmortem](eks-vpc-cni-incident.md).

## Proving the ephemeral-storage lesson, not just describing it

To understand *why* Kubernetes needs explicit persistent storage, a live pod was deleted mid-session
and the dashboard was queried before and after:

| | Before restart | After restart |
|---|---|---|
| Incident `started_at` (down services) | Original timestamp | Reset to restart time |
| Uptime history | Accumulated | Wiped |

The new pod started with a completely empty SQLite database, since `health.db` lived on the pod's
local disk with no `PersistentVolumeClaim` backing it. This is left as a known, intentional gap —
see [What's intentionally not done](#whats-intentionally-not-done-yet).

## An uptime monitor that needed its own uptime monitor

Render's free tier sleeps a service after 15 minutes of inactivity. For most apps that just means
a slow first request; for Probes, it's worse — the background checker thread sleeps too, silently
defeating the app's entire purpose. Rather than upgrade to a paid tier, a free external cron
service pings Probes every 10 minutes, keeping it continuously awake. The irony of an uptime
monitor needing its own external uptime check was not lost on me.

## CI and deployment

The current workflow (`.github/workflows/ci.yml`) configures Ruff lint, pytest, a Docker build,
and Trivy repository and image scans for pull requests and pushes to `main`. It uses Python
3.12 and read-only repository permissions. HIGH and CRITICAL findings with available fixes
fail the scans. Check the corresponding GitHub Actions run before claiming these checks passed.

An earlier workflow regenerated and committed `coverage.svg` and published Docker Hub images
after tests passed. Those jobs have been removed; Docker Hub credentials are no longer required
by CI. The image used by the historical EKS lab was published under that earlier workflow.

Render deployment remains separate. This workflow does not deploy to Render or gate its
automatic deployments. Automated deployment after successful CI is a future improvement.

**Historical fix in the retired badge job:** the first CI run failed with
`ModuleNotFoundError: No module named 'pkg_resources'` inside `coverage-badge`. Rather than assume
it was a missing dependency, I reproduced it in an isolated local venv and confirmed the actual
cause: `coverage-badge` (last updated years ago) depends on the deprecated `pkg_resources` API,
which recent `setuptools` releases have removed entirely — installing `setuptools` alone doesn't
fix it, since the module isn't in newer versions at all. The real fix is pinning
`pip install "setuptools<81"` specifically, which I verified end-to-end locally (ran the full test
suite, regenerated a real badge showing the correct percentage, confirmed exit code 0) before
pushing the fix.

### Cached base image scan findings

On 2026-09-12, a local build using the cached `python:3.12-slim` base succeeded, but
Trivy 0.70.0 reported 30 HIGH findings across Debian packages, covering four CVEs:
`CVE-2026-53612`, `CVE-2026-53613`, `CVE-2026-53614`, and `CVE-2026-14456`.
The affected packages came from util-linux and OpenSSL. The scan reported available fixes.

The image was rebuilt with:

```bash
sudo docker build --pull --no-cache -t probes:ci-local .
```

Docker pulled base image digest
`sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`.
The same Trivy version then scanned the rebuilt image using `--severity HIGH,CRITICAL`,
`--ignore-unfixed`, and `--exit-code 1`.

| Local scan | Image ID prefix | Debian findings | Python findings | Secret findings |
|---|---|---|---|---|
| Before refreshing the base | `75c89c9f6b0c` | 30 HIGH | 0 | 0 |
| After rebuilding | `23c4daadb4e5` | 0 | 0 | 0 |

Both saved JSON reports were inspected and their image IDs matched the corresponding builds.
The refreshed scan completed at 14:28:19 IST on 2026-09-12. These counts reflect the configured
severity and fix-availability filters, not an assertion that the image has no vulnerabilities
of any kind. The local repository scan also passed under this policy.

CI now uses `docker build --pull` to refresh its base image before building. The security
scan remains a required gate because pulling a newer image alone does not prove it is clear
of findings. No CVEs were suppressed and no severity thresholds were relaxed. The updated
GitHub Actions workflow still needs a successful remote run; these results are local evidence.

## Automated tests

The core logic in `checker.py` — incident open/close transitions, uptime tracking, environment-
variable URL resolution, and the guarantee that a failed Telegram alert never crashes the checker
loop — had only ever been verified manually, by watching `curl` output during development. A
22-test `pytest` suite now covers this directly, each test running against an isolated temporary
database.

To confirm the suite actually catches real regressions rather than passing vacuously, a bug was
deliberately introduced (an alert firing on the very first check, which should never happen since
there's no prior state to compare against) and the relevant test failed exactly as expected before
the bug was reverted.

## What's intentionally not done yet

- **No `PersistentVolumeClaim`** — the storage lesson above was deliberately observed rather than
  immediately fixed, to understand the problem before applying the solution.
- **No Helm chart or GitOps (Argo CD)** — manifests in `k8s/` are applied directly via `kubectl`
  to keep the focus on core Kubernetes concepts.
- **In-app Telegram alerting exists in `checker.py` but is intentionally dormant** — the
  architectural decision was to let the monitoring stack (Prometheus + Grafana Alerting) own alert
  routing, rather than hardcoding notification logic inside the application itself.

## Why this project, specifically

Most "deploy a web app" portfolio projects skip the parts that resemble real operations work —
inactive cloud costs, silent tooling failures, ephemeral storage surprises, platform-specific
quirks like free-tier sleep behavior. This project kept those parts in, on purpose, because
debugging real infrastructure failures is more representative of actual DevOps work than a
deployment that goes smoothly on the first try.
