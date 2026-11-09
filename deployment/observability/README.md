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
- For the app's Platform Observability page to link to Grafana, set
  `NEXT_PUBLIC_GRAFANA_URL=http://127.0.0.1:3002` in `frontend/.env.local`. That URL is the
  only observability value the frontend ever gets.

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
- **Not live-verified yet:** `docker compose up`, a Prometheus target scraping a real
  backend, and Grafana loading the datasource. Docker was not running on the machine where
  this was built.
- Not implemented yet: dashboards (Phase 4) and alert rules (Phase 5).

## References

- Prometheus [configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
  (scrape `authorization.credentials_file`, `kubernetes_sd_configs`, relabelling)
- Prometheus [metric and label naming](https://prometheus.io/docs/practices/naming/) and
  [instrumentation](https://prometheus.io/docs/practices/instrumentation/)
- Grafana [provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
  and [Docker configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/)
  (`GF_SECURITY_ADMIN_PASSWORD__FILE`)
