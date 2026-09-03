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
    on the dormant in-app Telegram code.

## A real incident, not a smooth demo

During EKS cluster creation, node group provisioning timed out with no obvious cause. Rather than
retry blindly, the failure was traced methodically:

1. `eksctl`'s own error gave no root cause, so I pulled raw CloudFormation stack events directly.
2. The first attempt's events showed the real error: the chosen instance type wasn't Free Tier
   eligible on this AWS account, explaining a silent rollback. Switched instance types.
3. The second attempt stalled differently — no CloudFormation failure at all, meaning the problem
   had moved elsewhere in the stack.
4. Checked the Auto Scaling Group's activity log directly: the EC2 instances had launched fine.
5. Checked the EC2 instances by ID: both `running`, ruling out an instance-launch failure.
6. `kubectl get nodes` showed the nodes had joined the cluster, but were stuck `NotReady`.
7. Inspecting `kube-system` directly found it: no `aws-node` (VPC CNI) DaemonSet existed at all,
   despite the original cluster-creation log claiming it had installed successfully.
8. Reinstalled the `vpc-cni` addon directly via the AWS CLI. Within a minute, both nodes were
   `Ready`.

**Lesson:** a tool reporting success doesn't guarantee a resource still exists or is healthy
later — verify actual runtime state rather than trusting historical logs.

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
