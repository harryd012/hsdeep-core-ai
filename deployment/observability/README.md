# Platform observability: Prometheus + Grafana

Prometheus and Grafana monitor **the HSDEEP platform itself**: the API, worker and
scheduler processes and their pipelines. Customer infrastructure stays on the
collectors → PostgreSQL → Command Center path. These are platform-level metrics with
bounded labels; no tenant, user, device, IP or credential is ever a label.

Design and status: `docs/design/PROMETHEUS_GRAFANA_IMPLEMENTATION_AUDIT.md`.

## Layout

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Prometheus and Grafana, pinned images, persistent volumes, health checks, bound to `127.0.0.1` |
| `.env.example` | Image versions, retention, ports, which Prometheus config to load |
| `prometheus/prometheus.yml` | Development: scrapes the backend on the host (`host.docker.internal:8000`) |
| `prometheus/prometheus.production.yml` | Kubernetes: discovers each backend pod and labels it `job=api` / `worker` / `scheduler` |
| `prometheus/rules/` | Alert rules (Phase 5) |
| `grafana/provisioning/` | Datasource and dashboard provider, loaded from files |
| `grafana/dashboards/` | Dashboard JSON (Phase 4) |
| `secrets/` | `metrics_token`, `grafana_admin_password`; git ignored, mounted read-only |

## Run it locally

Start the backend on the host as usual (`uvicorn app.main:app --port 8000`). Development
`RUNTIME_MODE=all` puts API, worker and scheduler metrics in that one process. Then:

```bash
cd deployment/observability
cp .env.example .env
printf '%s' "dev-metrics-token" > secrets/metrics_token
openssl rand -base64 24 | tr -d '\n' > secrets/grafana_admin_password
docker compose up -d
```

- Prometheus: http://127.0.0.1:9090. Under **Status → Targets**, `hsdeep-backend` should be **UP**.
- Grafana: http://127.0.0.1:3002, user `admin`, password from `secrets/grafana_admin_password`.
  The Prometheus datasource is provisioned already.
- **Connect the app's Platform Observability page:**
  - backend: `PROMETHEUS_URL=http://127.0.0.1:9090`. The page then shows Prometheus's own
    scrape-target health for every role, read server-side and superuser-only.
  - frontend: `NEXT_PUBLIC_GRAFANA_URL=http://127.0.0.1:3002` in `frontend/.env.local` adds an
    "Open Grafana" button. That link is the only observability value the browser ever gets.
- **A backend on another port:** add `prometheus/targets/local-<name>.yml` (git ignored), for
  example `- targets: ["host.docker.internal:8012"]`. Prometheus picks it up within 30 s.
- **One stack per machine:** the Compose project name is fixed (`hsdeep-observability`), so
  running it from another checkout reuses the same containers and volumes. Grafana reads the
  admin password only on first start; if the stored one no longer matches your
  `secrets/grafana_admin_password`, run
  `docker compose exec grafana sh -c 'grafana cli admin reset-admin-password "$(cat /run/secrets/grafana_admin_password)"'`.

A development backend without `METRICS_BEARER_TOKEN` serves `/metrics` openly and ignores
the header. When the backend has a token, put the same value in `secrets/metrics_token`.

## Production

- Set `METRICS_BEARER_TOKEN` on every backend role. Without it, `/metrics` answers 404 in
  production.
- Use `PROMETHEUS_CONFIG=prometheus.production.yml` (or the same scrape config in your
  Prometheus Operator / Helm setup). It scrapes **each pod** of each role separately, so API
  metrics never stand in for worker or scheduler metrics.
- Prometheus needs RBAC to `list`/`watch` pods in the `hsdeep` namespace, and network access
  to the backend ports. `deployment/k8s/10-networkpolicy.yaml` (`backend-metrics-scrape`)
  admits pods labelled `app.kubernetes.io/name=prometheus` in namespace `monitoring`; with
  Helm, set `networkPolicy.metricsScrape.enabled=true`.
- Keep Prometheus and Grafana private: no Ingress, or put them behind your SSO proxy.
  Grafana has anonymous access and sign-up disabled.
- Grafana reads `admin_password` only on first start. To change it later use the UI or
  `grafana cli admin reset-admin-password`.

## Metric catalogue

All metrics come from `backend/app/core/metrics.py` and are exposed per process on `/metrics`.

| Metric | Type | Labels |
|---|---|---|
| `hsdeep_http_requests_total` | counter | method, route (template), status |
| `hsdeep_http_request_duration_seconds` | histogram | method, route |
| `hsdeep_http_requests_in_progress` | gauge | method |
| `hsdeep_http_errors_total` | counter | method, route, status (5xx and exceptions) |
| `hsdeep_build_info` | gauge | version, runtime_mode |
| `hsdeep_collection_run_total` | counter | source_type, status |
| `hsdeep_collection_run_duration_seconds` | histogram | source_type, status |
| `hsdeep_collection_failures_total` | counter | source_type, stage |
| `hsdeep_collection_skipped_total` | counter | source_type |
| `hsdeep_collection_items_upserted_total` | counter | entity_type, source_type |
| `hsdeep_collector_execution_total` | counter | source_type, phase |
| `hsdeep_collector_phase_duration_seconds` | histogram | source_type, phase |
| `hsdeep_scheduler_queue_depth` | gauge | - |
| `hsdeep_scheduler_last_tick_timestamp_seconds` | gauge | - |
| `hsdeep_scheduler_tick_duration_seconds` | histogram | - |
| `hsdeep_scheduler_tick_failures_total` | counter | - |
| `hsdeep_alert_created_total` | counter | source, severity |
| `hsdeep_dependency_up` | gauge | dependency (database, redis) |
| `hsdeep_dependency_last_check_timestamp_seconds` | gauge | dependency |
| `hsdeep_db_pool_size`, `_max_overflow`, `_checked_out`, `_overflow`, `_saturation_ratio` | gauge | - |

`hsdeep_dependency_*` is set by the existing `/health` check, which readiness probes call.
A process that nobody probes has no reading, so alert on staleness too.

## Status

- Implemented and unit-tested: the metrics above, and these configuration files (structure
  checked by `backend/tests/test_observability_deployment.py`).
- Live-verified on Docker Desktop (2026-11-11): both containers healthy, both configs pass
  `promtool check config`, backend targets **up** through `host.docker.internal` with the
  token file, `hsdeep_*` series queryable, Grafana's provisioned datasource healthy and
  answering PromQL, and anonymous Grafana access refused (401).

## In the HSDEEP app

The superuser-only **Platform Observability** page (`/observability`, under Manage in the
sidebar) is the main place to look. It draws everything natively from the backend, which
queries Prometheus server-side, so nobody has to leave HSDEEP for a separate dashboard:

| Section | Source |
|---|---|
| Platform Health | `GET /api/platform/observability/health`: per-service HEALTHY / DEGRADED / WARNING / CRITICAL / UNKNOWN, each with its rule |
| Application Performance | `GET /api/platform/observability/timeseries`: native charts of request rate, 5xx rate, p50/p95/p99, collection runs, failures by stage, scheduler and alerts over 1 h to 7 d; and the live in-process summary |
| Scrape Targets | `GET /api/platform/observability/prometheus` |
| Alert Rules | `GET /api/platform/observability/alerts` (Prometheus `/api/v1/rules`) |
| Grafana Dashboards | links to the five provisioned dashboards with the chosen time range |

Backend settings (server-side only, never sent to the browser): `PROMETHEUS_URL`,
`GRAFANA_URL` (health check) and `ALERTMANAGER_URL` (health check). Frontend:
`NEXT_PUBLIC_GRAFANA_URL` (links only). When Prometheus is down, every telemetry-based status
reads UNKNOWN and the charts say so: nothing is shown as healthy without evidence.

Grafana is **linked, not embedded**. Grafana runs with anonymous access off and its sign-in
cookie is `SameSite=Strict`, so an iframe on the HSDEEP origin would only show Grafana's
login. Embedding would need `allow_embedding` plus either anonymous access or shared SSO,
which is a security decision to make explicitly. The native charts cover the same data inside
HSDEEP.

## Grafana dashboards

Five original dashboards, generated by `grafana/build_dashboards.py` (regenerate after
editing: `python deployment/observability/grafana/build_dashboards.py`). They are provisioned
read-only into the *HSDEEP Platform* folder and link to each other:

| Dashboard | UID | Shows |
|---|---|---|
| Executive Platform Health | `hsdeep-executive` | API availability, request rate, 5xx, p95, collection success, scheduler freshness, PostgreSQL / Redis, DB pool |
| API Performance | `hsdeep-api` | RED by component, status classes, p50/p95/p99, slowest and busiest routes, 4xx / 5xx by route, in-progress |
| Collector Operations | `hsdeep-collectors` | Runs by source type and outcome, failures by stage, run p50/p95/p99, phase timing, items saved; filter `source_type=prtg` for PRTG |
| Scheduler & Worker Health | `hsdeep-scheduler` | Tick age, due sources, tick duration and failures, process scrape health, DB pool, memory and CPU (Linux) |
| Alert Pipeline | `hsdeep-alerts` | Alerts opened by severity and source, persist-phase timing and failures |

Panels without a metric yet are explicit *Pending* notes: collector retry counts, alert
processing latency, correlation activity, active monitoring sources, host CPU / memory / disk
(needs node_exporter) and container restarts (needs cAdvisor or kube-state-metrics).

## Alert rules and runbooks

Rules live in `prometheus/rules/` (recording rules plus three alert files) and are unit-tested
with `promtool test rules prometheus/rules-tests/hsdeep-alerts.test.yml`. They are evaluated
by Prometheus and shown in the app's Alert Rules section. **No Alertmanager is deployed**, so
firing alerts are not sent anywhere until a receiver is configured and tested.

### HsdeepApiTargetDown
Prometheus cannot scrape an API process. Check the process is running, then
`curl -H "Authorization: Bearer $TOKEN" http://<host>:8000/metrics`. A 401 means
`secrets/metrics_token` and the backend's `METRICS_BEARER_TOKEN` differ; 404 in production
means the backend has no token set.

### HsdeepApiErrorRateHigh
More than 5 % of requests end in 5xx. API Performance → *5xx by route* shows which route.
Then read the backend's `api_request_failed` log lines for that route.

### HsdeepApiLatencyHigh
p95 above 2.5 s. Check *Slowest routes*, then the DB pool (*Scheduler & Worker Health*) and
the dependency checks: slow PostgreSQL or Redis timeouts are the usual causes.

### HsdeepDatabaseUnreachable
`/health` reports PostgreSQL down. Check the database is running and reachable from the
backend, credentials in `DATABASE_URL`, and connection limits.

### HsdeepRedisUnreachable
`/health` reports Redis down for 10 minutes. The platform keeps serving, but caching, rate
limiting and the Redis event transport are degraded. Restore Redis or check `REDIS_URL`.

### HsdeepDependencyCheckStale
Nothing has called `/health` on that process for 10 minutes, so its dependency state is
unknown. In Kubernetes, check the readiness probe; elsewhere, point a probe at `/health/ready`.

### HsdeepDbPoolSaturated
More than 90 % of the SQLAlchemy pool is in use. Look for slow queries or a stuck
collection, and size the pool for the worker concurrency (`max_concurrent_collections`).

### HsdeepWorkerTargetDown
A worker process is not scrapeable. Collections and event consumers stop with it. Check the
pod or process and its logs.

### HsdeepCollectorFailureRatioHigh
One source type is failing more than 20 % of runs. Collector Operations → *Failures by stage*:
`authenticate` = credentials, `collect` = vendor API or network, `normalize` = an unexpected
payload, `persist` = saving or alert evaluation.

### HsdeepCollectorLatencyHigh
p95 run time for a source type above 60 s. *Mean phase duration* shows whether the vendor
call (`collect`) or saving (`persist`) is slow.

### HsdeepSchedulerTargetDown
The scheduler process is not scrapeable. Check it is running; in a split deployment the worker
role also runs the scheduler, so ticks may continue.

### HsdeepSchedulerTickStale
No process has completed a tick for over 5 minutes, so due collections are not starting. Check
the scheduler logs for `Scheduler tick` and the database (ticks hold an advisory lock).

### HsdeepSchedulerNeverTicked
Scheduler-capable processes are up but none has ticked in 15 minutes since starting. Check
`RUNTIME_MODE` (only `all`, `worker` and `scheduler` run the scheduler) and the startup logs.

### HsdeepSchedulerTicksFailing
Ticks are raising before finishing. The scheduler logs show the exception; a database
problem is the usual cause.

## References

- Prometheus [configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
  (scrape `authorization.credentials_file`, `kubernetes_sd_configs`, relabelling)
- Prometheus [metric and label naming](https://prometheus.io/docs/practices/naming/) and
  [instrumentation](https://prometheus.io/docs/practices/instrumentation/)
- Grafana [provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
  and [Docker configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/)
  (`GF_SECURITY_ADMIN_PASSWORD__FILE`)
