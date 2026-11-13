# Prometheus + Grafana platform observability: Phase 0 audit and plan

Status: **Phase 0 approved; Phase 1 implemented and tested** (2026-11-09). Audited against
`origin/main` at `40465dc87`. Approved decisions: an `__unmatched__` route bucket, the Next.js
page for platform superusers only, and Docker Compose plus the scrape NetworkPolicy now (K8s
Prometheus/Grafana manifests later).

Scope rule: Prometheus and Grafana monitor **the HSDEEP platform itself**: its API, worker and
scheduler processes and its pipelines. Customer infrastructure stays on the existing
collectors → PostgreSQL → Command Center path. This is a separate track from
`OBSERVABILITY_PLATFORM_PLAN.md`, which covers customer-facing observability (sensors,
network center, logs explorer).

## 1. What exists (verified in the code)

| Area | Where | State |
|---|---|---|
| `prometheus-client` dependency | `backend/requirements.txt` (`0.25.0`) | Present, reuse it |
| `/metrics` endpoint | `backend/app/main.py` `metrics()` | Implemented. Checks the bearer token with `hmac.compare_digest` when `METRICS_BEARER_TOKEN` is set, returns **404 in production when it is unset**, and is open in dev. Tested in `tests/test_metrics_endpoint.py` |
| Metric definitions | `backend/app/core/metrics.py` | 9 metrics defined: `hsdeep_collection_run_total`, `hsdeep_collection_run_duration_seconds`, `hsdeep_collection_items_upserted_total`, `hsdeep_collector_execution_total`, `hsdeep_collector_phase_duration_seconds`, `hsdeep_collection_skipped_total`, `hsdeep_scheduler_queue_depth`, `hsdeep_scheduler_last_tick_timestamp_seconds`, `hsdeep_alert_created_total`. Labels are bounded (`source_type`, `status`, `phase`, `entity_type`, `source`, `severity`) |
| ServiceNow metrics | `backend/app/services/servicenow/metrics.py` | 8 metrics defined: push, pull and webhook latency, retries, conflicts, failures, queue depth, active pulls |
| Runtime roles | `config.py` `VALID_RUNTIME_MODES = (all, api, worker, scheduler)`; `main.py` lifespan | Every role runs the **same FastAPI app under uvicorn**, so each process already serves its own `/metrics`. The API uses port 8000, the worker 8001 (`deployment/k8s/06-backend-worker.yaml`), and the scheduler is in `07-backend-scheduler.yaml` |
| Process model | `backend/Dockerfile` `CMD uvicorn ... --port 8000` | One uvicorn process per pod with no `--workers`, so **no prometheus multiprocess mode is needed** |
| Request logging | `backend/app/core/request_logging.py` `RequestObservabilityMiddleware` | One structured line per request, with correlation id. `/health`, `/metrics` and the docs are excluded. It logs `request.url.path`, the raw path |
| Collection orchestrator | `services/monitoring/collection_orchestrator.py` `CollectionOrchestrator.run()` | Clear hook points: `authenticate()`, `collect()`, `normalize()`, the saver, and the final `status` (`success`, `error` or `skipped`) |
| Scheduler tick | `scheduler/jobs.py` `tick()` / `_run_due_sources()` | The due-source count is already computed (`len(due_ids)`). Skips for subscription and rate limit are audited |
| Alert creation | `services/alert_service.py` `evaluate_sensor()` | Hook point for alerts created by severity |
| DB engine | `core/db.py` `create_async_engine(..., pool_pre_ping=True)` | Default QueuePool. Pool stats can be read without running queries |
| K8s / Helm | `deployment/k8s/`, `deployment/helm/hsdeep/` | API, worker and scheduler deployments, with a default-deny NetworkPolicy. `k8s/README.md` lists **K2 platform observability: documented, not implemented** |
| Frontend | `frontend/src/lib/navigation.ts` (`visibleNav`; superusers see everything), `lib/api.ts` | No `/observability` route yet. `lib/observability/observabilityState.ts` is the customer-observability state machine and can be reused for "not configured" states |

## 2. Critical findings

1. **No `hsdeep_*` metric is ever emitted.** Nothing imports `app/core/metrics.py`, and its
   `observe_*` helpers have **no callers**. The module never loads, so `/metrics` returns
   only the default `python_*` and `process_*` collectors today. The same is true of
   `services/servicenow/metrics.py`. A dashboard built on the current names would be empty.
   Most of Phases 1–2 is wiring these up, not defining new metrics.
   **The ServiceNow metrics must not be loaded as they are:** every one except the two queue
   gauges is labelled by `connection_id`, a per-tenant UUID, which breaks the no-tenant-label
   rule. Phase 1 leaves that module unloaded. Relabelling it (drop `connection_id`) is a
   Phase 2 item.
2. **No HTTP request metrics exist.** `hsdeep_http_*` must be added. The label must be the
   **route template** (`request.scope["route"].path`, e.g. `/api/incidents/{incident_id}`), not
   `request.url.path`, which contains IDs and would be unbounded.
3. **Every `/metrics` scrape costs a database lookup.** The metrics token arrives as a
   `Bearer` header but is not a JWT, so `_RateLimitMiddleware` treats it as a possible API key
   and calls `api_key_tenant()`. `/metrics` should skip the rate-limit and quota lookup. This
   does not change the metering rule, because scrapes are not tenant traffic.
4. `METRICS_BEARER_TOKEN` is not checked in `validate_for_production()`. The current behaviour
   is already safe (404 when unset), so this stays a documented choice rather than a startup
   failure.

## 3. Duplicates to avoid

- No second metrics module or exporter process: extend `app/core/metrics.py` and reuse each
  process's own `/metrics`.
- No `prometheus-fastapi-instrumentator` or similar library: one small middleware covers it.
- No new collection pipeline, and no platform metrics stored in PostgreSQL.
- Keep `RequestObservabilityMiddleware`; add instrumentation alongside it.
- No new state machine for the UI page: reuse the `observabilityState.ts` patterns.

## 4. Security and tenant isolation

- Labels never carry tenant IDs, user IDs, device IDs, IPs, raw URLs or credentials. A test
  will scrape `/metrics` after tenant traffic and assert that no UUIDs or IPs appear.
- Prometheus authenticates with `authorization.credentials_file`, a mounted secret. The token
  never appears in `prometheus.yml`.
- Grafana has anonymous access off, reads its admin password from a secret file, and is not
  published by default: Compose binds it to `127.0.0.1`, and K8s has no Ingress for it.
- K8s gets a NetworkPolicy that lets only the Prometheus pod reach the backend `/metrics`
  ports. Without it, the current default-deny policy would block scraping.
- The frontend gets only `NEXT_PUBLIC_GRAFANA_URL`, used as a link. No token, no iframe.

## 5. Implementation plan (each phase is a small commit; tests run and reported)

| Phase | Work | Files |
|---|---|---|
| 1 Backend instrumentation | Add `hsdeep_http_requests_total`, `_request_duration_seconds`, `_requests_in_progress` and `_errors_total`, labelled by method, route template and status. Exclude `/health*`, `/metrics` and the docs; label unmatched routes `__unmatched__`. Import `core.metrics` at startup. `/metrics` skips the limiter and quota lookup. Add `hsdeep_build_info{version, runtime_mode}` | `core/metrics.py`, new `core/http_metrics.py` middleware, `main.py` |
| 2 Pipeline wiring | Call the existing helpers from `CollectionOrchestrator.run()` (timing per phase, final status). Add `hsdeep_collection_failures_total{source_type, stage}`, where stage is auth, collect, normalize or persist. Call `observe_scheduler_tick` in `tick()`, and add `hsdeep_scheduler_tick_failures_total` and tick duration. Call `observe_alert_created` in `evaluate_sensor`. Add a counter for incident creation failures. Add DB pool gauges (size, checked out, overflow), read from the pool with no queries. Add Redis and DB up gauges, set by the existing readiness check. Import the ServiceNow metrics | `collection_orchestrator.py`, `scheduler/jobs.py`, `alert_service.py`, `core/metrics.py`, `readiness.py` |
| 3 Deployment | `deployment/observability/` with: a Compose file (pinned Prometheus and Grafana, volumes, retention set by env, healthchecks using the `wget` the images ship); `prometheus.yml` (local target `host.docker.internal:8000`); `prometheus.production.yml` (separate `api`, `worker` and `scheduler` jobs); `.env.example`; and a README. Plus a K8s NetworkPolicy for scraping | `deployment/observability/**`, `deployment/k8s/10-networkpolicy.yaml` |
| 4 Grafana | A provisioned datasource and 5 original dashboards: Executive Platform Health, Collector Operations, API Performance, Scheduler & Worker Health, Alert Pipeline. Panels query only metrics from Phases 1–2; anything without a source is listed as pending, not drawn | `deployment/observability/grafana/**` |
| 5 Alert rules | API, worker and scheduler `up == 0`; collector failure ratio; collector p95; scheduler tick stale; API 5xx ratio; API p95. Thresholds live in one place. Notifications are marked **not configured** until a receiver is set up and tested | `deployment/observability/prometheus/rules/*.yml` |
| 6 Next.js page | `(dashboard)/observability/page.tsx`. A "Platform Observability" nav item, visible only to superusers and platform admins. A Grafana link from `NEXT_PUBLIC_GRAFANA_URL` and a "not configured" state. No health values are rendered | `frontend/src/app/(dashboard)/observability/`, `lib/navigation.ts` |
| 7 Validation | pytest: auth and production behaviour (existing tests), request counters and histogram, route-template labels, a test that no UUIDs or IPs appear in labels, collector, scheduler and alert metrics, no duplicate registration on re-import, scraping each role. `promtool check config`, `promtool check rules` and dashboard JSON lint run through the pinned images; `docker compose config`. Frontend unit tests and a bundle grep for tokens. Then the full backend and frontend suites | `backend/tests/`, `frontend/tests/`, `deployment/observability/validate.*` |
| 8 Docs | Metric catalogue, a runbook for each alert, production deployment steps, and a final report using the five status labels | `deployment/observability/README.md`, `deployment/OBSERVABILITY.md` |

Live verification needs Docker Desktop with the dev backend running. Anything not seen working
in a running Prometheus and Grafana will be reported as **implemented but not live-verified**.

## 6. Decisions needed before Phase 1

1. **Route label for 404s and unmatched paths:** one `__unmatched__` bucket (recommended),
   which keeps cardinality bounded against scanners.
2. **Who sees the Next.js page:** platform superusers only (recommended: these are platform
   internals, not tenant data), or every tenant admin.
3. **K8s scope:** add the scrape NetworkPolicy plus Prometheus and Grafana manifests under
   `deployment/k8s/` now, or deliver Docker Compose only and leave K8s documented
   (recommended: Compose plus the NetworkPolicy now, K8s manifests later).

No phase needs a database migration.

## 7. Phase 1 result: IMPLEMENTED AND TESTED (not live-verified in Prometheus)

Files created: `backend/app/core/http_metrics.py`, `backend/tests/test_platform_metrics.py`.
Files modified: `backend/app/core/metrics.py`, `backend/app/main.py`.

- New metrics: `hsdeep_http_requests_total{method,route,status}`,
  `hsdeep_http_request_duration_seconds{method,route}`,
  `hsdeep_http_requests_in_progress{method}`, `hsdeep_http_errors_total{method,route,status}`
  (5xx and unhandled exceptions), `hsdeep_build_info{version,runtime_mode}`.
- `HttpMetricsMiddleware` is a plain ASGI middleware installed outermost. It reads the route
  template after routing. For responses made before routing (rate-limit 429s, CORS
  preflights) it matches the path against the app's routes. Unmatched paths go to
  `__unmatched__`, and non-standard methods to `OTHER`. `/health*`, `/metrics` and the docs
  are excluded, the same list as the request log.
- `app.main` now imports `app.core.metrics`, so every existing `hsdeep_*` family appears on
  `/metrics`, but stays at zero until Phase 2 calls the helpers.
- `/metrics` skips `_RateLimitMiddleware` (no per-IP wait, no API-key DB lookup). Its token
  check is unchanged.
- Tests: `tests/test_platform_metrics.py` covers route-template labels, the unmatched bucket,
  exclusions, 5xx/exception errors, a pre-routing 429, the in-progress gauge, method bounding,
  the real-app scrape (families and runtime role), no UUID/IP label values, the scrape
  skipping the limiter, and single registration.

## 8. Phase 2 result: IMPLEMENTED BUT NOT LIVE-VERIFIED (wiring tested; not scraped by a running Prometheus)

- `CollectionOrchestrator.run()` now records each phase (`authenticate`, `collect`, `normalize`,
  `persist`) and the run's outcome. A failure is counted in the new
  `hsdeep_collection_failures_total{source_type, stage}`, where `stage` is `configure`,
  `authenticate`, `collect`, `normalize` or `persist`. A concurrent-run skip is counted in
  `hsdeep_collection_skipped_total`, not as a run.
- `scheduler.jobs.tick()` records queue depth, last-tick time and the new
  `hsdeep_scheduler_tick_duration_seconds`. A tick that raises counts in
  `hsdeep_scheduler_tick_failures_total`.
- The alert engine records `hsdeep_alert_created_total{source, severity}` when it opens an alert.
- `hsdeep_dependency_up{dependency}` and `hsdeep_dependency_last_check_timestamp_seconds` are set
  by the existing `/health` check, which readiness probes call, so no polling is added.
  `hsdeep_db_pool_*` gauges are read from the SQLAlchemy pool at scrape time (no query).
- The ServiceNow metrics no longer carry `connection_id`. Nothing records them yet, so the
  module stays unloaded.

## 9. Live summary for the Next.js page

`GET /api/platform/observability/summary` (superuser only, `require_admin`) reads this
process's own Prometheus registry. It needs no Prometheus server and no database. Values are
cumulative since the process started and cover that process only; the payload says so.
Percentiles are estimated from histogram buckets with the same interpolation as PromQL
`histogram_quantile()`. Anything unobserved is `null`, shown as "Not measured" and never as 0.
The page lays the API figures out by the RED method (rate, errors, duration), followed by
collection outcome and failure stage, scheduler freshness and alerts opened. Tile thresholds
match the planned Phase 5 alert rules.

## 10. Phase 3 result: IMPLEMENTED BUT NOT LIVE-VERIFIED

`deployment/observability/`: a Compose file with pinned Prometheus `v3.5.0` and Grafana
`12.1.1`, persistent volumes, retention by time and size, health checks, and both UIs bound
to `127.0.0.1`. Secrets are read-only files under `secrets/` (git ignored). Prometheus
authenticates with `authorization.credentials_file`. Grafana reads its admin password via
`GF_SECURITY_ADMIN_PASSWORD__FILE` and has anonymous access and sign-up disabled.
`prometheus.yml` scrapes a host backend in development. `prometheus.production.yml` uses
Kubernetes pod discovery, so each api / worker / scheduler pod is a separate target with
`job` set from its component label. The datasource and dashboard provider are provisioned
from files. `deployment/k8s/10-networkpolicy.yaml` adds `backend-metrics-scrape`, and the Helm
chart has the same behind `networkPolicy.metricsScrape.enabled`.

Checked by `backend/tests/test_observability_deployment.py`, `helm lint` / `helm template`
and `kubectl kustomize`. **Not run yet:** Docker was not running on the build machine, so
`docker compose up`, a live target and Grafana's datasource connection remain to be verified.

## 11. Live verification and frontend integration (2026-11-11): IMPLEMENTED AND TESTED

- Docker Desktop: Prometheus `v3.5.0` and Grafana `12.1.1` healthy on `127.0.0.1:9090` and
  `127.0.0.1:3002`. Both Prometheus configs pass `promtool check config`. The backend targets
  were **up** through `host.docker.internal` with the token file, and `hsdeep_*` series were
  queryable. Grafana's provisioned datasource reported healthy and answered PromQL, and
  anonymous Grafana requests were refused (401).
- The development scrape targets moved to `prometheus/targets/*.yml` (file_sd), so another
  local backend port is one git-ignored file, picked up without a restart.
- `GET /api/platform/observability/prometheus` (superuser only, new `PROMETHEUS_URL` setting)
  returns Prometheus's own per-target health for every role. The Platform Observability page
  shows it as "Prometheus scrape targets", next to the "Open Grafana" link. This was checked
  signed in: all targets up, then a deliberately unreachable worker target shown as **Down**
  with Prometheus's error.
- Caveat found live: the Compose project name is fixed, so every checkout on a machine shares
  one stack and its Grafana volume. Grafana reads the admin password only on first start
  (see README for the reset command).

