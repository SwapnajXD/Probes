# Resume claims

## Safe to claim now

- The repository defines GitHub Actions CI for pull requests and pushes to `main`, including Ruff,
  pytest, a Docker build, and Trivy filesystem and image scans. The Trivy image scan uses the
  action's documented default image scan type.
- Local verification in this environment: `ruff check .` passed; `python -m pytest -q` passed
  all 74 tests; `docker build -t probes-audit .` succeeded.
- The Docker image runs as the non-root `app` user, retains one Gunicorn worker, and defines an
  HTTP liveness health check.
- Kubernetes manifests declare port `10000`, readiness and liveness probes, and CPU/memory
  requests and limits.
- Dependabot is configured for weekly pip and GitHub Actions dependency updates.
- The repository contains a Render Blueprint (`render.yaml`) describing a Docker web service.

## Needs work first

- **CI verified green:** the current GitHub Actions workflow run has not been inspected or verified.
- **CI/CD or automated deployment:** no deploy hook invocation or image registry push is present
  in the repository workflow. The external Render service's auto-deploy setting is unverified.
- **Live service healthy:** live endpoint checks have not been run as part of this audit.
- **Immutable Docker base pin:** `python:3.12-slim` is a mutable tag, not a digest-pinned image.
- **Kubernetes deployment verified:** manifest syntax and runtime behavior have not been checked
  against a live cluster.
- **Monitoring rules as code:** this repository has `docs/monitoring.md`, not a `monitoring/` or
  alert-rules-as-code directory.
