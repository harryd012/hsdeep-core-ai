# Unified Network & Infrastructure Observability: audit and phased plan

Status: **in progress** (2026-11-03). Source: the user's master brief "HSDEEP CORE AI — Unified
Network & Infrastructure Observability Platform" (P0–P14). Branch `feat/observability`, stacked
on `feat/backup-phase2` (the dev database is migrated to that branch's head).

Rules carried from the brief: extend the existing architecture, no duplicate collectors / routes /
models, no fabricated measurements or success states, original design (no third-party code,
assets or branding), backend-enforced RBAC and tenant isolation, and every feature labelled by its
real state (tested / mocked integration / partial / UI only / not implemented / blocked).

## 1. Audit: what exists (verified in the repo)

Earlier audits: `docs/REPOSITORY_AUDIT.md`, `docs/PLATFORM_LEDGER.md` (batches 1–11),
`docs/DEVICE_SUPPORT_MATRIX.md`. Summary of the monitoring core:

| Area | Exists | Where | State |
|---|---|---|---|
| Collector framework | `BaseCollector` + `CollectorRegistry`, `NormalizedBundle` → `BundleSaver`, collection orchestrator, retry/backoff, rate limiter, source health | `app/collectors/`, `app/services/monitoring/` | Implemented |
| Collectors | PRTG, SNMP (v1/v2c/v3, L3, firewall), connectivity (ICMP/TCP/DNS), Linux SSH, Windows WinRM/WMI, Meraki, FortiGate, FortiManager, Palo Alto, Azure, AWS, GCP, VMware, Kubernetes, Zabbix, Datadog, Azure Backup, Veeam | `app/collectors/*.py` | Implemented with mocked-transport tests; live connectivity `Needs Verification` per collector |
| Distributed collection | Edge agents, enrollment tokens, collector credentials, `/api/v1/ingest/*`, collector runs | `models/agent.py`, `collector_enrollment_token.py`, `collector_credential.py`, `routes/ingest.py` | Implemented |
| Sensors | One row per vendor check: metric type, unit, current value, warning/critical thresholds, status up/down/warning/unknown | `models/sensor.py` | Implemented; **no lifecycle (pause/disable), no user thresholds, no detail/history API** |
| Channels + history | PRTG channels and collector metrics stored append-only in `metrics` with `sensor_id`, `metric_type`, canonical `metric_name`, `dimensions` | `models/metric.py`, `bundle_saver.py` | Stored; **not queryable per sensor/channel** |
| Interfaces | Interface table, rates / utilization computation | `models/interface.py`, `interface_rates.py` | Implemented |
| Discovery | Scopes (CIDR/IP), probe jobs and hosts, identity resolution, materialization, profiles, evidence | `services/discovery/`, `routes/discovery*.py` | Implemented |
| Topology | L2/L3 links, resource relationships with evidence, topology standard, live topology, dependency graph / impact | `topology_*`, `dependency_*`, `relationship_evidence_service.py` | Implemented |
| Alerts / incidents | Sensor-status alerts, vendor alerts, dedupe per signal, correlation, incident lifecycle, priority engine, RCA, maintenance windows | `alert_service.py`, `alerting/`, `incident_*`, `priority/`, `rca_*`, `maintenance_window.py` | Implemented |
| Notifications | Email, Slack, PagerDuty (incident notifications), webhooks | `notifications/dispatcher.py`, ledger batch 10 | Implemented; Teams `Not implemented` |
| Metering / quotas | Usage counters, API quota, plan sensor limits | `services/metering/`, `models/usage.py` | Implemented |
| AI | Copilot router, AI diagnosis, AI insights, SOP / runbook intelligence | `copilot_router.py`, `ai_*`, `sop/` | Implemented |
| Logs | Normalized events, NOC events, vendor event messages | `normalized_event.py`, `noc_event.py`, `event.py` | Events only; **no syslog listener, no log explorer** |
| Traces / APM / OpenTelemetry | — | — | **Not implemented** |
| NetFlow / sFlow / IPFIX, SNMP traps | — | — | **Not implemented** |
| Sensor templates / library | — | — | **Not implemented** |
| Dashboard builder (widgets, saved dashboards) | Fixed dashboards (executive, command center, infrastructure) | `dashboard_service.py`, `components/command-center/` | Fixed only; **no builder** |

## 2. Phased plan (brief order)

| Phase | Scope in this codebase | State |
|---|---|---|
| P0 Audit, stabilization | This document; full backend suite green on the branch | **Done** |
| P1 Unified sensor model | Sensor lifecycle (active / paused / disabled), user thresholds with direction, sensor detail with channels, bucketed channel history API, sensor detail page with time-series chart, PRTG channel names kept, every sensor reading kept as history, metric retention job (off by default) | **Slice 1 implemented and tested** (`tests/test_sensor_lifecycle.py`) |
| P2 Inventory & discovery | Saved views and bulk actions on hosts; discovery schedules, exclusions and approval of recommended sensors (on the existing discovery engine) | **Slice 1 implemented and tested** (scheduled discovery + scopes UI, `tests/test_discovery_schedule.py`); saved views / bulk actions next; sensor recommendations wait for P3's library |
| P3 Sensor library | Sensor templates (catalogue from the collectors' real metric types), recommendation from discovery profiles, bulk add for collectors that support configuration (SNMP, connectivity) | planned |
| P4 Network Monitoring Center | Interface traffic / errors / utilization pages from `interfaces` + metrics, top-N bandwidth, VPN tunnels (FortiGate / Palo Alto data already collected) | planned |
| P5 Infrastructure monitoring | Windows / Linux / VMware host views from existing collectors | planned |
| P6 Dashboards & topology | Widget registry over aggregated APIs, saved dashboards per tenant / role; topology already exists, add drill-down to sensors | planned |
| P7 Metrics / logs | Syslog receiver on the edge agent → normalized events, Logs Explorer over events | planned (needs agent work) |
| P8 APM / tracing | OTLP/HTTP ingest of traces → spans table, service map from span parent/child | planned (new ingestion path) |
| P9 Alerting | Dynamic baselines, notification policies, Teams channel | planned (engine exists) |
| P10 AI RCA / copilot | Copilot tools over the new sensor / history APIs | planned (engine exists) |
| P11 Reporting | Sensor / interface / availability reports from stored metrics | planned (reports module exists) |
| P12 Integration center | One page over `monitoring_sources` + integrations with health, sync history | planned |
| P13 RBAC, metering, scale | New roles as permission bundles; metering of metrics / logs / traces | planned |
| P14 Hardening | Performance benchmarks, retention jobs | planned |

## 3. P1 slice 1: sensor lifecycle, thresholds, channels, history

- **Lifecycle** (`sensors.admin_state`): `active` | `paused` | `disabled`. *Paused*: readings are
  still stored, the sensor's status reads `paused` and it raises no alert (an open alert is
  resolved). *Disabled*: readings and channel values from the collector are ignored entirely;
  status `disabled`. Resuming sets status `unknown` until the next collection. The platform pulls
  most sensors from a vendor (PRTG, Meraki ...), so pausing here does not pause the check in the
  vendor's system; that is stated in the UI.
- **Thresholds** (`threshold_source` `provider` | `user`, `threshold_direction` `above` | `below`):
  user thresholds survive every sync and decide the status from the value (`down` past critical,
  `warning` past warning, else `up`); when the collector sends no value, the provider status stands.
  Reverting returns to the provider's thresholds at the next sync.
- **Channels**: the distinct metric types recorded for the sensor, with latest value and sample
  count (24 h), from `metrics`.
- **History**: `GET /api/monitoring/sensors/{id}/history` returns time buckets (avg / min / max /
  count) for one channel over 1 h – 30 d, at most 300 points, computed in the database.
- **Audit**: every lifecycle or threshold change is an audit event with old and new values.
- **Primary channel**: a sensor's own reading is stored as its `primary` channel. When the
  collector already sends that series as channel-less metrics tied to the sensor, those are tagged
  `primary` and the saver adds no second copy.

### Test database ownership (2026-11-03)

`hsdeep_test` was created by the `postgres` superuser, which owns 96 of its 109 tables
(including `sensors`); the `test` role owns only the 13 tables it created itself, so `test`
cannot run a migration that alters those tables. `f2b7d9e4a6c1` was applied to `hsdeep_test` as
`postgres`. **Open:** every later migration that alters a `postgres`-owned table hits the same
wall until the tables are reassigned to `test` (`REASSIGN OWNED BY postgres ...` is not usable
for a superuser; reassign per table, or recreate the test database as `test`).

## 4. P2 slice 1: scheduled discovery and the scopes console

Already in place before this slice: discovery scopes (target, protocols, limits, **exclusions**),
"probe now" storing a durable probe job, and operator-approved materialization of probed hosts
into devices. Missing: `schedule` was a stored, never-executed string, and scopes had no UI.

- **Schedule format** (`app/services/discovery/schedule.py`): a repeat interval, a number and
  `m` / `h` / `d`, from 15 minutes to 30 days, stored normalized (`"6H"` → `"6h"`); anything else
  is a 422. No scope in the dev database had a schedule, so tightening the validation breaks no
  data.
- **Next run** (`discovery_scopes.next_run_at`, migration `a8c4e2f6b1d3`): one interval after the
  schedule is set, after the scope is re-enabled, and after every run (success or failure), so
  saving a schedule never starts an unplanned sweep and a failing scope is not retried every
  tick. An unchanged schedule keeps its slot; clearing it clears the next run.
- **One probe path** (`scope_probe_service.run_scope_probe`): "probe now" and the scheduler record
  the same durable job, now with `trigger` (`manual` | `scheduler`) on `discovery_probe_jobs`.
- **Scheduler job** (`app/scheduler/discovery_jobs.py`, every 60 s): advisory lock (one worker),
  at most 5 due scopes per tick probed one after another, each in its own session; only enabled,
  probeable scopes of tenants with an active subscription. A lapsed tenant's scope moves on one
  interval without probing.
- **Approval stays manual**: a scheduled probe creates no device. Hosts are reviewed and
  materialized under "Probe jobs", exactly as before.
- **Audit**: scope create / update (changed fields, old and new) / delete are audit events
  (`discovery_scope.*`). Fixed on the way: `AuditService.record` re-inserted a failed event on the
  caller's commit (event added outside its savepoint), so an audit failure broke the request it
  was meant never to break (`tests/test_audit_service_savepoint.py`).
- **UI**: "Discovery scopes" panel on `/infrastructure/discovery`: list with schedule, last and
  next run; create / edit (type, target, exclusions, host limit, protocols, schedule); run now,
  enable / disable, delete, for users with `devices.write`. Scheduled probe jobs are marked in
  the probe review list.
