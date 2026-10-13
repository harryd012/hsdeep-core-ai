# HSDEEP CORE AI — Kubernetes Deployment Foundation (K1)

This directory is the **K1** layer of the three-phase Kubernetes plan:

| Phase | Scope | Status |
|-------|-------|--------|
| **K1** | Deployment foundation: Docker images, Kubernetes manifests, Helm chart, health/readiness probes, ConfigMap/Secret split, Pod Security, NetworkPolicy, PDB, Ingress, docs. | **Implemented here** |
| **K2** | Platform observability: Prometheus, Grafana, OpenTelemetry traces/metrics, structured-log shipping, operational-health integration. | Documented, **not implemented** |
| **K3** | Kubernetes monitoring integration: discover customer clusters, collect telemetry, normalize → correlate → SOP/ticket/SLA. | Documented, **not implemented** |

Nothing here changes the application, its ports, its health contract, or its
configuration names — it packages the existing product for a cluster.

## What runs

One backend image, three roles selected by `RUNTIME_MODE`
(`all | api | worker | scheduler`, enforced by `backend/app/core/config.py`):

| Role | `RUNTIME_MODE` | Port | Service | Notes |
|------|----------------|------|---------|-------|
| api | `api` | 8000 | `hsdeep-backend:8000` | HTTP only; the sole user-facing role; Ingress target |
| worker | `worker` | 8001 | — | scheduler ticks + RCA/EventBus consumers; scales horizontally (Postgres advisory lock = single tick owner) |
| scheduler | `scheduler` | 8002 | — | scheduler ticks only; single replica |

Plus: **frontend** (Next.js standalone, `:3000`), **postgres:16** (StatefulSet),
**redis:7** (Deployment). Postgres/Redis are a self-contained baseline — swap for
a managed database/cache in production.

## Health contract (no prefix — mounted at the root)

- `GET /health/live` — liveness, **dependency-free**. A Postgres/Redis blip must
  never restart the pod.
- `GET /health/ready` — readiness, **dependency-aware**: `503` when Postgres (or
  Redis, because `READINESS_REQUIRE_REDIS=true`) is unavailable, `200` otherwise.

Both backend roles and the frontend wire `startupProbe`→`readinessProbe`→
`livenessProbe` accordingly.

## Configuration vs secrets

Non-secret settings live in a **ConfigMap** (`hsdeep-config`); credentials live
in a **Secret** (`hsdeep-secrets`, plus `hsdeep-postgres-auth`). The split mirrors
`backend/app/core/config.py`. `RUNTIME_MODE`/`PORT` are per-role, set on each
Deployment. Images bake in **no** `.env` and no credentials (see
`deployment/README.md` and `backend/Dockerfile`).

## Deploy — plain manifests (kustomize)

```bash
# 1. Build images (from repo root) — tag matches Chart.appVersion
docker build -t hsdeep-core-ai-backend:9a backend
docker build -t hsdeep-core-ai-frontend:9a \
  --build-arg NEXT_PUBLIC_API_URL=https://portal.example.com frontend

# 2. Provision the two Secrets BEFORE applying (placeholders are refused at
#    production startup, and the Postgres StatefulSet will not start without
#    hsdeep-postgres-auth). Prefer your secret manager; these examples show the
#    keys. Never put a real password in source control or shell history.

#    2a. PostgreSQL auth Secret (keys: POSTGRES_DB, POSTGRES_USER,
#        POSTGRES_PASSWORD). Create it out-of-band and keep the password out of
#        git history:
kubectl create secret generic hsdeep-postgres-auth -n hsdeep \
  --from-literal=POSTGRES_DB=hsdeep \
  --from-literal=POSTGRES_USER=hsdeep \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)"

#    2b. Application Secret. The DATABASE_URL password MUST match 2a.
#        Edit 02-secret.example.yaml (do NOT commit it) or use a secret manager.
kubectl apply -f deployment/k8s/02-secret.example.yaml   # after editing!

# 3. Apply the rest
kubectl apply -k deployment/k8s
```

> Both `02-secret.example.yaml` and `03-postgres-secret.example.yaml` are
> intentionally excluded from `kustomization.yaml`, so `kubectl apply -k` never
> creates a Secret with a placeholder password. You must provision
> `hsdeep-secrets` and `hsdeep-postgres-auth` yourself (step 2) before applying.

## Deploy — Helm

```bash
helm install hsdeep deployment/helm/hsdeep -n hsdeep --create-namespace \
  --set secrets.existingSecret=hsdeep-secrets \
  --set postgres.auth.existingSecret=hsdeep-postgres-auth
```

Toggle components with `--set` (`postgres.enabled`, `redis.enabled`,
`ingress.enabled`, `networkPolicy.enabled`, `pdb.enabled`, `hpa.enabled`, and the
`backend.roles.*` map). See `values.yaml`.

## Frontend API URL

`NEXT_PUBLIC_API_URL` is inlined into the client bundle **at build time** (it is a
build `ARG`). The browser calls the backend directly (there is no Next rewrite
proxy), so the image must be built with the externally-reachable API origin and
the Ingress must serve `/api` and `/health` from the backend Service. Default
placeholder host: `portal.example.com`.

## Security posture

- Namespace enforces the **restricted** Pod Security profile.
- All pods: `runAsNonRoot`, uid/gid **1001** (matches the images' non-root users;
  Postgres/Redis use 999), `seccompProfile: RuntimeDefault`, dropped `ALL`
  capabilities. Backend runs with a read-only root filesystem + `/tmp` emptyDir.
- **NetworkPolicy** default-deny, then least-privilege: Postgres/Redis accept
  only backend traffic; frontend accepts only the ingress controller; backend
  egress allows DNS, the datastores, and the internet for collectors while
  blocking the cloud metadata endpoint (`169.254.0.0/16`).
- A dedicated ServiceAccount does **not** auto-mount its token (the app does not
  use the Kubernetes API in K1).

## K2 roadmap (not implemented)

- Export Prometheus metrics from the app (`/metrics`) and scrape with
  Prometheus; Grafana dashboards for the operational-health signals already
  surfaced by `/health`.
- OpenTelemetry traces across API → collectors → correlation; propagate
  trace context.
- Ship the existing structured JSON logs (`LOG_FORMAT=json`) to a log backend.

## K3 roadmap (not implemented)

- A cluster-connector that discovers a customer Kubernetes cluster's nodes,
  pods, and workloads as first-class monitored resources, feeding the existing
  normalization → correlation → incident → SOP/ticket/SLA pipeline.
