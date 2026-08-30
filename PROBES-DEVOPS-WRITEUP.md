# Probes — Service Health Dashboard

A self-built uptime/health monitoring dashboard, containerized and deployed to a real AWS EKS cluster — built as a hands-on DevOps learning project covering containerization, Kubernetes, cloud infrastructure, and incident debugging.

## What it does

Probes pings a configurable list of backend services on a timer, tracks uptime history and downtime incidents in SQLite, exposes a live dashboard, and serves Prometheus-compatible metrics for external monitoring integration.

**Core features:**
- Background health checker (Python thread) pinging services at a configurable interval
- SQLite-backed incident tracking — records exactly when a service goes down and when it recovers, with computed downtime duration
- `/metrics` endpoint in Prometheus text format, ready for external scraping
- Environment-variable URL resolution (`${VAR_NAME}` syntax in config) — keeps secrets and per-environment URLs out of committed config
- Optional Telegram alerting on status-change transitions (up→down / down→up), with safe no-op behavior when unconfigured
- A practical secondary use case: pinging low-traffic personal projects (e.g. free-tier hosted backends) frequently enough to prevent them from spinning down due to inactivity

## Tech stack

| Layer | Choice |
|---|---|
| App | Python, Flask, gunicorn |
| Storage | SQLite |
| Containerization | Docker |
| Orchestration | Kubernetes (Amazon EKS) |
| Ingress | ingress-nginx, backed by an AWS Elastic Load Balancer |
| Cluster provisioning | `eksctl` |
| Secrets | Kubernetes Secrets (`kubectl create secret`) |
| Monitoring integration | Prometheus-format `/metrics` endpoint (designed for future scrape by a Grafana/Prometheus stack) |

## What was actually built and tested (not just planned)

This wasn't a copy-paste deployment — every stage below was run, verified, and in one case, debugged from a real failure:

1. **App built and unit-tested locally** — background checker verified against both a reachable and an intentionally unreachable URL, confirming graceful failure handling (no crash on bad URLs, correct incident-opening logic).
2. **Containerized with Docker** — single-stage Python image, deliberately pinned to a single gunicorn worker (`--workers 1`) since the background checker thread is not safe to run in multiple processes without duplicating pings. Documented in the Dockerfile as a known constraint, not an oversight.
3. **`.dockerignore` / `.gitignore` added** after catching that the build context was including runtime artifacts (`health.db`, `__pycache__`) that shouldn't ship inside the image.
4. **Provisioned a real EKS cluster** via `eksctl` — managed control plane, managed node group, VPC CNI, kube-proxy, and CoreDNS addons.
5. **Installed the ingress-nginx controller**, which provisioned a real AWS Elastic Load Balancer automatically.
6. **Built and pushed the image to Docker Hub**, then deployed via Kubernetes `Deployment`, `Service`, and `Ingress` manifests, with runtime configuration and secrets injected via a Kubernetes `Secret` rather than committed to any file.
7. **Verified the live deployment** by hitting `/api/status` through the real internet-facing load balancer and confirming correct behavior — a genuinely monitored external service reporting "up" with real latency, and unconfigured services correctly reporting "down."
8. **Deliberately tested a pod restart** to observe and confirm the ephemeral-storage behavior of Kubernetes pods (see below).
9. **Tore the cluster down cleanly** after each session and verified via the AWS CLI that no billable resources were left running.

## A real incident, not a smooth demo

During cluster creation, both node group creation attempts eventually **timed out** with no obvious cause. Rather than assume it was transient and retry blindly, the failure was traced methodically:

1. `eksctl`'s own timeout error gave no root cause → pulled raw CloudFormation stack events directly.
2. First attempt's stack events showed the real error: `t3.medium` isn't Free Tier eligible on this AWS account, which explained the silent rollback. Switched to `t3.micro`.
3. Second attempt with `t3.micro` also stalled — but this time CloudFormation showed no failure at all, meaning the problem was somewhere *else* in the stack.
4. Checked AWS Auto Scaling Group activity directly — instances had launched successfully. So the EC2 layer wasn't the problem.
5. Checked the EC2 instances by ID directly — both were `running`, ruling out an instance-launch failure entirely.
6. Checked `kubectl get nodes` — the nodes *had* joined the cluster, but were stuck `NotReady`.
7. Inspected `kube-system` pods directly and found the smoking gun: **no `aws-node` (VPC CNI) DaemonSet existed at all**, despite the original cluster-creation log claiming it had been installed successfully. The addon had silently failed or vanished sometime after creation.
8. Reinstalled the `vpc-cni` addon directly via the AWS CLI. Within a minute, `aws-node` was running and both nodes flipped to `Ready`.

**Lesson demonstrated:** a tool reporting success (`"successfully created addon: vpc-cni"`) doesn't guarantee that resource still exists or is healthy later — verify actual runtime state (`kubectl get daemonset`, `kubectl describe node`) rather than trusting historical logs. This is the kind of debugging that reflects real day-2 operations work, not just following a tutorial.

## The Kubernetes storage lesson (proven, not just described)

To understand *why* Kubernetes needs explicit persistent storage, a live pod was deleted mid-session and the dashboard was queried before and after:

| | Before restart | After restart |
|---|---|---|
| Incident `started_at` (down services) | `08:18:08` | `08:22:57` (reset) |
| Uptime history | Accumulated | Wiped |

The new pod started with a completely empty SQLite database — proving that container-local disk (`health.db` in this case) does not survive a pod being deleted and recreated, which happens routinely during deployments, crashes, or node maintenance. The documented fix for a production version of this app would be a Kubernetes `PersistentVolumeClaim`, backed by a provisioner like `local-path` (used in the author's homelab k3s cluster) or AWS EBS (on EKS).

## Cost discipline

Every EKS session in this project followed a strict pattern: provision → test → **verify via AWS CLI that the cluster is deleted** before ending the session, specifically to avoid leaving a paid control plane (~$0.10/hr) or EC2 nodes running idle. This included recovering from an interrupted session where a cluster had been left running longer than intended, and confirming full teardown (`CloudFormation` stacks and `EC2` instances both checked directly, not assumed) before considering it closed.

## What's intentionally not done yet

- **No `PersistentVolumeClaim`** — the storage lesson above was deliberately observed rather than immediately fixed, to understand the problem before applying the solution.
- **No Helm chart or GitOps (Argo CD)** for this deployment — manifests were applied directly via `kubectl` to focus on core Kubernetes concepts first.
- **No Prometheus/Grafana scrape configured yet** — the `/metrics` endpoint exists and is ready, but wiring it into a real monitoring stack (home lab or otherwise) is a planned next step.
- **Telegram alerting exists in code but is intentionally dormant** — the architectural decision was to let a monitoring stack (Prometheus + Grafana Alerting) own alert routing rather than hardcoding notification logic inside the application itself.

## Why this project, specifically

Most portfolio "deploy a web app" projects skip the parts that actually resemble real operations work — inactive cloud costs, silent tooling failures, ephemeral storage surprises. This project kept those parts in, on purpose, because debugging a real infrastructure failure and understanding *why* Kubernetes behaves the way it does is more representative of actual DevOps work than a deployment that goes smoothly on the first try.
