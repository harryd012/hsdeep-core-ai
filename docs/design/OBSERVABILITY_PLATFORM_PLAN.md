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
| P2 Inventory & discovery | Saved views and bulk actions on hosts; discovery schedules, exclusions and approval of recommended sensors (on the existing discovery engine) | **Slices 1–2 implemented and tested**: scheduled discovery + scopes UI (`tests/test_discovery_schedule.py`); host saved views, bulk classification / monitoring pause and a tag facet (`tests/test_host_views_bulk.py`, UI not verified in a signed-in browser); sensor recommendations wait for P3's library |
| P3 Sensor library | Sensor templates (catalogue from the collectors' real metric types), recommendation from discovery profiles, bulk add for collectors that support configuration (SNMP, connectivity) | **Slice 1 implemented and tested**: catalogue of 29 templates with tenant usage, bulk thresholds through the P1 lifecycle, per-host coverage rules, sensors linked to their host; **slice 2**: bulk reachability checks tied to their hosts, a reachability sensor that alerts when a host stops answering (`tests/test_sensor_library.py`) |
| P4 Network Monitoring Center | Interface traffic / errors / utilization pages from `interfaces` + metrics, top-N bandwidth, VPN tunnels (FortiGate / Palo Alto data already collected) | **Slice 1 implemented and tested**: per-interface history (traffic, utilisation, error rates), network overview / interface list / interface history API, Network Monitoring and interface pages (`tests/test_network_center.py`) |
| P5 Infrastructure monitoring | Windows / Linux / VMware host views from existing collectors | **Slices 1–3 implemented and tested**: server fleet page and PRTG resource sensors (`tests/test_server_fleet.py`); infrastructure health overview with clouds (`tests/test_infrastructure_health_overview.py`); per-host detail already existed |
| P6 Dashboards & topology | Widget registry over aggregated APIs, saved dashboards per tenant / role; topology already exists, add drill-down to sensors | **Slice 1 implemented** (frontend only): topology drill-down to sensors, alerts, host tabs and interface pages; **slice 2**: saved dashboards and the widget registry (`tests/test_saved_dashboards.py`) |
| P7 Metrics / logs | Syslog receiver on the edge agent → normalized events, Logs Explorer over events | **Slice 1 implemented and tested**: syslog receiver (separate process), log-to-device linking at ingest, Logs Explorer API and page (`tests/test_logs_explorer.py`); not verified in a browser |
| P8 APM / tracing | OTLP/HTTP ingest of traces → spans table, service map from span parent/child | **Slice 1 implemented and tested**: OTLP/HTTP JSON trace ingest, spans table, services / traces / service map / trace APIs, Traces pages (`tests/test_tracing.py`); **slice 2**: APM section with per-service time series, operations, callers / callees and grouped errors (`tests/test_apm_services.py`); neither verified in a browser |
| P9 Alerting | Dynamic baselines, notification policies, Teams channel | **Slice 1 implemented and tested**: notification policies (severity / priority / site routing) and a Microsoft Teams channel per policy (`tests/test_notification_policies.py`); **slice 2**: opt-in dynamic baselines per sensor (`tests/test_sensor_baselines.py`) |
| P10 AI RCA / copilot | Copilot tools over the new sensor / history APIs | **Slice 1 implemented and tested**: the Copilot answers from sensors, network, logs, traces, baselines, coverage and named hosts (`tests/test_copilot_observability.py`) |
| P11 Reporting | Sensor / interface / availability reports from stored metrics | **Slice 1 implemented and tested**: availability, sensor performance and interface traffic reports over a period, with CSV export (`tests/test_observability_reports.py`) |
| P12 Integration center | One page over `monitoring_sources` + integrations with health, sync history | **Slice 1 implemented and tested**: one derived state per source / integration, 24 h run statistics, sync history and health events per source, Integration Center pages (`tests/test_integration_center.py`); checked in a browser. **Slice 2** (plan: `docs/design/INTEGRATION_FRAMEWORK_PLAN.md`): step 2a done — collector errors name their cause, run-level health events split from real transitions (`tests/test_collector_error_text.py`); step 2b done — ServiceNow and ITSM connections with sync-job history in the Integration Center; step 2c done — integration catalogue from the collector, ITSM and action registries (`GET /api/integration-center/catalog`, "Available integrations" tab) |
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

## 5. P2 slice 2: host saved views and bulk actions

Before this slice the Hosts explorer (`GET /api/explorer/hosts`) could filter but not keep a
filter set, and no API could change a host's criticality, environment or tags: all 671 hosts in
the dev database had them unset, so the priority engine treated every host as `medium`.

- **Tags in the explorer**: a `tag` filter (`devices.tags @> '["tag"]'`), a `tags` facet (tag
  arrays unnested in SQL, one count per tag) and `tags` on each row. The query budget is now 9
  statements per read.
- **Saved views** (`host_saved_views`, migration `c3e9a1f7d2b5`): name, filters (only the
  explorer's own filter keys, `extra=forbid`), layout (`health` | `inventory`) and `shared`.
  Listed: the caller's own views, then views shared in the tenant. A reader (`devices.read`)
  may keep views: they are personal settings, listed as such in `tests/test_rbac_write_routes.py`. Only the owner changes or
  deletes a view; another user's or tenant's view is a 404. Sharing needs `devices.write`.
  At most 50 per user; names unique per user. `owner_id` has no foreign key (users are
  soft-deleted). Create / update / delete are audit events (`host_view.*`).
  `GET|POST /api/explorer/hosts/views`, `PATCH|DELETE /api/explorer/hosts/views/{id}`.
- **Bulk actions** (`POST /api/explorer/hosts/bulk` for classification, `POST
  /api/explorer/hosts/bulk/monitoring` for pause / resume; each route carries its own write
  permission and the subscription gate): up to 200 hosts of the
  caller's tenant; ids from another tenant come back in `not_found` and are never touched.
  - `set_criticality` (`critical | high | medium | low`, the priority engine's weight keys) and
    `set_environment` (`production | staging | development | test | dr | lab`, new vocabulary),
    or `null` to clear; `add_tags` / `remove_tags` (1–50 characters, at most 50 tags per host).
    Need `devices.write`. No collector writes these fields, so a sync does not overwrite them.
  - `pause_monitoring` / `resume_monitoring`: every active (resp. paused) sensor of the hosts
    goes through the P1 lifecycle (`SensorService.apply`, alerts re-evaluated, an open alert
    resolves on pause). Disabled sensors are never touched. Need `monitoring.update`; at most
    5,000 sensors per request. The vendor's own check keeps running (stated in the UI).
  - Each changed host is one audit event (`device.bulk.<action>`) with old and new values;
    unchanged hosts are not audited. `GET /api/explorer/hosts/vocabulary` serves the allowed
    values so the UI does not hard-code them.
- **UI** (`/infrastructure/hosts`): saved views at the top of the facet rail (apply, save the
  current filters, update an edited view, share, delete); the layout and the applied view are
  in the URL. Row checkboxes and select-page appear for users who may run at least one action;
  the bulk bar asks for confirmation before pausing or resuming. A Tags facet and a Tags column
  in the Inventory layout. Selection is per page (no "select every match" across pages yet).
- **Refactor**: `SensorService.update` now loads the sensor and calls `apply(sensor, change)`,
  which the bulk path reuses; `Column.header` in the page kit accepts a node (for the
  select-all checkbox).
- **Not verified**: the UI was type-checked, linted and served, but not exercised in a
  signed-in browser session.

## 6. P3 slice 1: sensor library, bulk thresholds and coverage

Before this slice nothing described what the collectors measure as a whole: every sensor carried
only its own `metric_type`, there was no way to set thresholds on many sensors at once, and no
view of hosts that miss a kind of monitoring.

- **Catalogue** (`app/services/monitoring/sensor_library.py`): 29 templates, one per kind of
  measurement a collector really emits (the `metric_type` strings in `app/collectors/*.py`,
  `firewall_saver.py` and `backup_saver.py`): collectors, unit, which way is worse (`above`,
  `below`, or a 1 / 0 state sensor). Default thresholds only where the platform already defines
  one: CPU 80 / 95 and memory 85 / 95 (`actions/health_thresholds.py`), firewall settings, the
  backup RPO and the backup collector's free-space limits. Every other template says "none
  defined". A metric type belongs to exactly one template (tested).
- **Usage**: `GET /api/monitoring/sensor-library` (`monitoring.read`): per template, the tenant's
  sensors by status, how many run on user thresholds, and which collectors reported them. Metric
  types no template covers are listed as uncatalogued (Datadog passes its query names through).
- **Bulk thresholds**: `POST /api/monitoring/sensor-library/templates/{key}/apply`
  (`monitoring.update`, active subscription): set thresholds, or `reset` to the provider's, on
  every sensor of the template or only those of up to 200 chosen hosts (`device_ids`; ids of
  another tenant come back in `not_found`). `dry_run` counts first; at most 1,000 sensors per
  call. Each sensor goes through `SensorService.apply` (P1), so status and alerts follow at
  once; each changed sensor is one audit event `sensor.template_applied` with old and new values.
  State sensors and PRTG sensors refuse with 422.
- **Coverage** (`GET /api/monitoring/sensor-library/recommendations`, read-only): per host,
  `unmonitored` (no sensor and no polled interface), `server_without_os` (a server with no
  sensor from SSH, WinRM / WMI, VMware, Zabbix, Datadog or PRTG), `network_without_polling` (a
  switch, router or firewall with no interfaces and no network-collector sensor) and
  `unclassified` (type unknown or other). Each rule names the step that resolves it; nothing is
  created, since adding a check needs a source and usually credentials. Filter by rule, paged.
- **Sensors linked to their host**: `BundleSaver` linked only firewall and backup sensors to
  their device (`SensorRepository.upsert` has no `device_id`), so sensors from SSH, WinRM,
  connectivity, VMware and every other collector had no host: host views, alert correlation
  and the coverage rules could not see them. Every reading that names a device of the same bundle
  is now linked; a reading whose device is not in the bundle keeps its existing link.
- **UI** (`/monitoring/library`, under Monitoring in the sidebar): Templates (catalogue with
  live counts and status mix; select one to see its details and, with `monitoring.update`, check
  then apply or reset its thresholds) and Coverage (gap counts per rule, filter, host list
  linking to the host page). Verified on the dev tenant: 34 PRTG sensors on the PRTG template,
  260 of 269 hosts with no sensor or interface, 267 of unknown or other type.
- **Not in this slice**: PRTG's own sensor kind (`type_raw`) is not fetched, so PRTG sensors stay
  one vendor-defined template; storing it needs a column on `sensors`, and the test database's
  `sensors` table is still owned by `postgres` (section 3). Recommendations from discovery
  profiles wait for profiles that describe devices (today's profiles are scan presets). Bulk
  creation of checks (reachability for many hosts) is slice 2: it creates monitoring sources and
  counts against plan limits (done in slice 2, section 7). Applying thresholds to hosts chosen in the UI is
  API-only for now.

## 7. P3 slice 2: bulk reachability checks

The Coverage tab could name unmonitored hosts but not cover them. Two gaps stood in the way of
simply creating connectivity sources for them:

- **Duplicate hosts**: a connectivity source reports its target as a device of its own, and the
  saver matches devices only by (source, external id) or hardware serial, so every check would
  have created a second device for the host it watches.
- **Silent outages**: the collector emitted latency and packet-loss sensors only while ICMP
  answered; a host that stopped answering changed its device status and raised no sensor alert.

What this slice adds:

- **Reachability sensor** (`app/collectors/connectivity.py`): every run reports one
  `reachability` sensor, up (1) or down (0) over all checks that ran, so an unreachable host
  raises an alert through the normal sensor path. The catalogue gains a `reachability` state
  template. Existing connectivity sources get the sensor from their next collection.
- **Checks tied to a host**: `config.connectivity.device_id` names the existing host a
  connectivity source watches. `BundleSaver` then maps the check's device to that host (tenant
  checked) and neither creates nor changes a device row: the host's own sources keep its name,
  type and status. The check's sensors land on the host (slice 1 linking), and the canonical
  telemetry uses the host's real type.
- **Bulk endpoint**: `POST /api/monitoring/sensor-library/reachability` (`monitoring.create`,
  active subscription): up to 200 hosts, ICMP or a TCP port, poll interval 1 minute to 1 day.
  Hosts already watched by a tied connectivity source, hosts without an address and ids outside
  the tenant are skipped and listed. `dry_run` sorts the hosts and reports the plan's remaining
  Sources. Each check is one monitoring source, so the request needs room for **all** of them
  in the plan's Sources limit, or it is refused whole with 402 (nothing half-created). Source
  names are unique per tenant (`Reachability: <host> [<id prefix>]`). Every created source is an
  audit event `monitoring_source.reachability_added`.
- **UI** (Coverage tab): users with `monitoring.create` select hosts that have an address (per
  page), choose ICMP or a TCP port and an interval, preview (what will be added, what is skipped,
  plan room) and confirm. Verified live on the dev tenant up to the preview: it is on the Starter
  plan with 5 of 5 sources used, so the page correctly offers no "Add" and says the plan allows
  0 more. Creation itself is covered by the API test, not exercised on dev data.
- **Where checks run**: connectivity sources are polled by the platform's collection service
  (edge collectors only push telemetry through `/api/v1/ingest`; they do not run sources), so a
  host must be reachable from that service. Stated in the UI.
- **Plan cost**: one source per host, so the Starter plan (5 sources) covers few hosts. A single
  source probing many targets would need a multi-target connectivity collector; not built.

## 8. P4 slice 1: Network Monitoring Center

Before this slice interfaces kept only their latest state: the saver derived rx / tx bits per
second and utilisation from two counter samples (`interface_rates.py`) and wrote them onto the
interface row, so every poll overwrote the last and nothing could be charted. No page showed
interfaces across devices, and VPN tunnels appeared only per firewall.

- **Interface history** (`BundleSaver`, step 2): each poll that derives a value records it in
  `metrics` with `interface_id`, `metric_name` and `dimensions.channel`: `rx_bps`, `tx_bps`
  (capability `bandwidth`), `interface_utilization_pct` (`interfaces`) and `errors_in` /
  `errors_out` in errors per second (`errors`). Error rates come from the cumulative error
  counters with the same rules as traffic (`compute_error_rates`): no previous sample, a counter
  that went backwards or a non-positive interval records nothing, never 0 or a negative rate. A
  first poll records nothing. The canonical pipeline only asserts interface capabilities, so no
  series is stored twice; the metric retention job (P1) applies.
- **API** (`/api/network`, `monitoring.read`, tenant-scoped, 404 across tenants):
  `GET /overview` (interface totals, enabled-but-down, disabled, fresh and measured counts,
  top 10 by utilisation and by throughput from samples of the last 30 minutes, enabled-but-down
  list, top 10 by error rate over the last hour from history, VPN tunnels down first);
  `GET /interfaces` (status filter, search over interface, description and device, sort by
  problems / utilisation / throughput / name, paged); `GET /interfaces/{id}` (with the
  recorded history channels); `GET /interfaces/{id}/history` (every channel bucketed, avg and
  max, 1 h to 30 d). The existing `/api/network/status` (dashboard site and device counts) is
  unchanged.
- **Freshness**: a rate whose last sample is older than 30 minutes is `fresh: false`; the
  overview and pages never present it as current load.
- **UI**: "Network" under Observe. `/network`: Overview (tiles, busiest links, top throughput,
  enabled-but-down, errors, VPN tunnels) and Interfaces (filters, search, sort, paging).
  `/network/interfaces/[id]`: state, current load, inbound / outbound traffic, utilisation and
  error history charts, details. Verified on the dev tenant: 5 interfaces on 2 devices, one
  enabled but down (`hsdeep-lab-sw-b Gi1/2`), no current rates (last counter sample 38 days
  ago, stated on the page), no VPN tunnels.
- **Not in this slice**: per-interface alert thresholds (utilisation, errors) and top-talkers
  by flow need NetFlow / sFlow (not collected); WAN / SD-WAN path quality; interface charts on
  the host page.

## 9. P5 slice 1: server fleet

Already in place: the Linux (SSH), Windows (WinRM / WMI) and VMware collectors report per-device
CPU, memory, disk, load, uptime and process sensors, every reading is kept as history (P1), and
`/infrastructure/hosts/[id]` shows one host's verdict, sensors and metric history. Missing: a view
across all servers.

- **API** `GET /api/explorer/hosts/resources[?platform=windows|linux|vmware|other]`
  (`devices.read`, tenant from the token; `app/services/host_resources_service.py`): one row per
  server with a host-resource sensor, worst first (any sensor down, then warnings, then the
  highest CPU / memory / disk %), and a fleet summary that always covers every platform.
- **Resources** come from each sensor's metric type through the existing canonical vocabulary
  (`canonical_metric_name`); generic checks (PRTG `prtg_sensor`, SSL, ping) and per-process
  sensors are not host resources. The busiest core and the fullest volume speak for CPU and disk
  (worst sensor status first, then highest %).
- **Platform** is the reporting collector, else the device's OS string, else "other"; never the
  name.
- **No fabricated values**: each value carries its own sensor's status; percentages are compared
  only when the unit is `%` (VMware MHz / MB are shown as reported); a server with no reading for
  an hour is "stale". Nothing is written.
- **UI** `/infrastructure/servers` (nav: Infrastructure → Servers): stat strip (measured, down,
  warning, disk ≥ 90 %, CPU ≥ 90 %, stale), platform tabs, table with gauges, rows open the host
  page.
- **PRTG** (slice 2): the collector now requests PRTG's own sensor type (`type_raw`, and the
  type name as a fallback; never the user-editable sensor name) and stores CPU load, memory and
  disk free sensors as `prtg_cpu_load` / `prtg_memory_free` / `prtg_disk_free`, with the unit
  PRTG prints after the value. Every other PRTG sensor stays `prtg_sensor`, unit unset, as before.
  PRTG's memory and disk sensors report what is free: a free percentage is shown as used
  (100 - free) with the reported free value in the tooltip; a free amount in GB is shown as free
  and never compared as a percentage. The sensor library's PRTG template covers the new types.
  `STATUS = Needs Verification`: the type ids (`snmpcpu`, `wmiprocessor`, `snmpmemory`,
  `wmimemory`, `wmidiskspace`, `snmpdiskfree` ...) are not yet checked against a live PRTG (the
  dev PRTG at 192.168.1.5 was unreachable), and existing PRTG sensors take the new types at
  their next sync. Not mapped: uptime (PRTG prints it as "3 d 4 h", which the value parser does
  not read) and the platform of PRTG-only servers (shown as "other").

## 9. P6 slice 1: topology drill-down

Order changed on purpose: saved dashboards need a new table, and an uncommitted security S3
migration (`f6b4d0e2a8c3`) sits on the same base revision in this working copy, so a second
migration now would give Alembic two heads. Slice 1 is the drill-down, which needs no storage;
saved dashboards and the widget registry follow as slice 2 once S3's migration is committed.

- **Topology drawer** (`components/topology/TopologyDrillDown.tsx`): selecting a device on the
  live topology now shows a MONITORING section in place of the old "no event feed" note: its
  sensors (count, down and warning, the failing ones linking to their sensor pages), open
  alerts, and links to the host page's Overview, Sensors, Alerts, Interfaces and Events tabs.
  Data comes on demand from the existing `/api/devices/{id}/sensors` and `/alerts`; nothing
  new on the backend. Sites, sources and L3 synthetic nodes say they have no sensors.
- **Link ends**: each end of a link offers "Open interface history" when its device has an
  interface of exactly that name (looked up through `/api/network/interfaces`). The default L2
  tree view draws links that cannot be selected, so this was type-checked and built but not
  exercised in the browser.
- **Host page**: interface rows open the P4 interface page.
- **Verified live** on the dev tenant: selecting `hsdeep-lab-sw-b` shows "No sensor reports for
  this device" (it is polled through interfaces), 0 open alerts and working host-tab links;
  the host's `Gi1/2` row opens `/network/interfaces/<id>`.

## 10. P6 slice 2: saved dashboards and the widget registry

- **Model** (`saved_dashboards`, migration `b7d2e4f9a1c6`, revises S3's `f6b4d0e2a8c3`): name,
  description, widgets (JSONB), `shared`; `owner_id` without a foreign key, unique name per
  owner, at most 30 per user and 24 widgets per dashboard. Same ownership rules as saved host
  views: a user lists their own and the tenant's shared dashboards, changes only their own
  (another user's private or another tenant's dashboard is a 404). A reader (`dashboard.read`)
  may keep dashboards (personal settings, listed in `tests/test_rbac_write_routes.py`); sharing
  needs `devices.write`. Create / update / delete are audit events (`dashboard.*`).
- **Widget registry** (`app/schemas/dashboards.py`): nine types, each reading one existing API
  with the viewer's own permissions: network summary, busiest links, interfaces down while
  enabled, VPN tunnels (`/api/network/overview`); sensor history and interface history
  (`/history` of P1 and P4); coverage gaps (sensor library recommendations); server fleet
  (P5 `/api/explorer/hosts/resources`); sensors by template (sensor library). Each type has a
  strict settings model (unknown keys, widget types, sizes, windows and channels are refused);
  a chart naming a sensor or interface outside the caller's tenant is refused at save, so a
  dashboard never stores another tenant's ids. A widget whose data the viewer may not read shows
  "no permission"; a deleted target shows "no longer exists".
- **API** (`/api/dashboards`): `GET /widgets` (registry with settings schemas), list, create,
  get, update, delete.
- **UI**: "My Dashboards" under Observe (`/dashboards`): pick, create (starts with three network
  widgets), edit (title, size, order, add, remove, share), delete. Charts are added from the
  sensor and interface pages ("Add to dashboard"), so nobody types an id. Verified on the dev
  tenant: created a dashboard, added server fleet and sensors-by-template widgets, added the
  "SNMP CPU Usage" chart from its sensor page, every widget rendered its real data (no servers
  reporting resources; 34 PRTG sensors, 5 down; no measurements in the chart window), then
  deleted the test dashboard.
- **Not in this slice**: drag-and-drop layout, role-default dashboards, a widget for alerts.

## 11. P7 slice 1: syslog receiver and Logs Explorer

Before this slice a collector could post log lines to `POST /api/v1/ingest/events` as events
with `telemetry_type = LOG`, but nothing produced them (no syslog listener; the repository has
no edge agent program) and nothing could search them (the ingest schema stated "no full-text
log search").

- **Syslog parsing** (`app/services/logs/syslog.py`, pure): RFC 5424 (with structured data) and
  RFC 3164 (BSD) into an ingest event: message, platform severity (syslog 0-2 critical, 3-4
  warning, 5-7 info), category by facility (auth / authpriv / audit security; kern, daemon,
  syslog, cron, ntp, clock system; otherwise application), `event_type = syslog.<facility>`,
  attributes (sender address, host, app, procid, msgid, facility, syslog severity, format,
  structured data). Nothing is invented: a missing field stays absent, a message without PRI is
  kept as user.notice and flagged `pri_missing`, an unreadable timestamp falls back to the receive
  time and says so (`timestamp_source`). RFC 3164 has no year: the receive year is used, the
  previous one around New Year.
- **Syslog receiver** (`python -m app.edge.syslog_receiver`): a separate process for the
  customer's network, never part of the central API process. UDP (default port 5514), batches of
  up to 500 to the existing ingest API with a collector credential from the environment (never
  logged; https required except for localhost). A failed batch (network error, 429, 5xx) is
  retried in order with backoff; a bounded buffer (default 20,000) drops the oldest beyond its
  limit and counts every drop; a status line every minute. Smoke-tested over a real UDP socket
  against a stand-in ingest server: 4 datagrams, 2 batched requests, credential header present,
  nothing dropped.
- **Log to device** (ingest): a LOG event without a device is linked to the device of the
  tenant whose IP address, hostname or name equals the line's syslog host (else its sender
  address), only when exactly one device matches; an ambiguous or unknown host stays unlinked.
- **Logs Explorer API** (`/api/logs`, `/api/logs/stats`, `monitoring.read`, tenant-scoped):
  window defaults to the last hour and is capped at 7 days; text search is a case-insensitive
  substring match over the message (wildcards in the search text are literal); filters for
  severity (several), host, app, event type and device; newest first, paged; stats give volume
  per bucket by severity and the top hosts, apps, types and severities (a facet ignores its own
  filter). No full-text index: `pg_trgm` needs a database superuser, so search stays bounded to
  the window.
- **UI** (`/logs`, "Logs" under Observe): time range, refresh, stacked volume chart, severity
  toggles and host / app / type facets, search, log lines (device link when linked; click a line
  for its attributes), paging, and a note on how to get logs in when the window is empty.
  Type-checked and linted; **not verified in a browser** (the dev servers were stopped).
- **Not in this slice**: syslog over TCP / TLS, a log retention job for LOG events, saved log
  searches, log-based alert rules, and a log widget for dashboards.

## 12. P8 slice 1: OpenTelemetry traces

- **Ingest** (`POST /api/v1/ingest/otlp/v1/traces`): OTLP/HTTP with the JSON encoding, so an
  OpenTelemetry SDK or collector works unchanged with `OTEL_EXPORTER_OTLP_ENDPOINT=<platform>/api/v1/ingest/otlp`,
  `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` and
  `OTEL_EXPORTER_OTLP_HEADERS=X-HSDEEP-Collector-Credential=<credential>`. Authenticated by a
  collector credential like the other ingest routes (tenant and source from the credential). The
  protobuf encoding is answered with 415 and says how to switch: neither `protobuf` nor
  `opentelemetry-proto` is a dependency, and the dependency files were being changed by another
  session. Compressed bodies are refused (415); bodies over 8 MB get 413; up to 10,000 spans
  per request. Response per OTLP: `{}`, or `partialSuccess` with the rejected count and first
  reason.
- **Decoding** (`app/services/tracing/otlp.py`, pure): hex trace / span ids (all-zero or malformed
  rejected), enum values as numbers or names, 64-bit times as strings, a span that ends before it
  starts rejected, `service.name` from the resource (else OpenTelemetry's `unknown_service`);
  attributes (128), values (1,024 characters) and events (32) bounded.
- **Storage** (`trace_spans`, migration `c4e8a2d6f1b3`): one row per span, unique per (tenant,
  trace, span) so a resent batch is a no-op; indexes for the window, service and trace lookups.
  Spans follow the metric retention (`metric_retention_days`, or the tenant's own metric
  retention from S5), purged by the existing retention job.
- **API** (`/api/traces`, `monitoring.read`, windows up to 7 days): `/services` (spans, error
  spans and rate, traces, p50 / p95 / max of entry spans: server / consumer spans or spans
  without a parent), `/` (traces: start, duration from first start to last end, spans, errors,
  services, root span or the earliest span when the root has not arrived, filters for service,
  errors, minimum duration and span-name text), `/{trace_id}` (all spans, orphan count),
  `/service-map` (caller -> callee edges from parent / child spans across services, with calls,
  errors, average and p95 callee duration).
- **UI** ("Traces" under Observe): `/traces` with Services (tiles, a layered service map, the
  services table) and Traces (filters, search, paging); `/traces/[traceId]` with the waterfall
  (spans on the trace timeline, indented under their parent, coloured by service, failed spans
  red) and the selected span's attributes, resource and events. Type-checked and linted; **not
  verified in a browser** (another session's dev server holds this folder).
- **Not in this slice**: protobuf encoding and gzip, metrics and logs over OTLP, linking spans to
  hosts (`host.name`) and logs (trace ids in log lines), latency / error-rate alert rules,
  sampling controls.

## 13. P5 slice 3: infrastructure health overview

The `/infrastructure` landing page was a count row, a raw table of every device and a text dump
of the topology. It is now a health overview (layout inspired by the reference product's
"current health overview", original design):

- **Backend** (additive, no new endpoint): the canonical `InfrastructureOverviewService`
  (`GET /api/infrastructure/overview`) adds `devices.by_category`, i.e. device health per
  resource family from the existing vocabulary (`resource_family`: server → compute,
  switch / router → network, firewall → security ...; an unmapped type stays `unknown`), and
  `devices.cloud.providers`, i.e. cloud resources per provider and service category
  (virtual machines, networking, security, load balancing) from the collector's own
  `resource_kind`; anything else is `other`, never guessed. Health uses the service's existing
  status buckets (unknown is never healthy). The provider falls back to the device vendor for
  rows written before collectors set `custom_metadata.provider`; the Azure collector now sets
  `provider` / `resource_kind` / `region` like AWS and GCP.
- **UI**: summary strip (devices, sites, sensors down, active alerts, open incidents, failing
  sources), one card per family plus data sources (critical / warning counts of the total and a
  bar split by health, linking to Servers, Network, Firewalls, Universal Resources or Hosts),
  and a Clouds panel: AWS, Azure and Google Cloud cards ("Not connected" when a provider has no
  resources, not 0) with a services row per connected provider. The existing collection,
  canonical topology and SD-WAN / SASE panels stay below. The raw device table and topology
  text are gone (Hosts and Topology cover them); the unused `InfrastructureKpiRow` was removed.
- **Not collected yet**: serverless, storage, database and container services of the clouds
  (the connectors list VMs, networks, security rules and load balancers only).

## 13. P9 slice 1: notification policies and Microsoft Teams

Before this slice a tenant had one notification setting: email, Slack and PagerDuty on or off,
each at a minimum severity, and every enabled channel received every incident at or above it.
There was no Teams channel and no way to send different incidents to different people.

- **Policies** (`notification_policies`, migration `e3a9c5f7b2d8`, a new table: the existing
  notification tables are owned by `postgres` in the test database, so none was altered): name,
  on / off, order, minimum severity, optional priorities (P1-P4) and sites, the tenant channels
  it sends to (email / Slack / PagerDuty), optional email recipients of its own, and an optional
  Microsoft Teams webhook of its own. At most 50 per tenant, unique names.
- **Routing** (`NotificationDispatcher.destinations`): with no enabled policy, delivery is exactly
  as before. With enabled policies an incident goes only where a matching policy sends it: a
  tenant channel once (still at its own minimum, e.g. PagerDuty's), a policy's own recipients or
  Teams webhook as its own destination (`email:<policy>`, `teams:<policy>` in the delivery log).
  The existing guarantees hold per destination: sent once, retried up to 3 times, "resolved"
  only where the opening was sent, no flood of history when a destination is set up (incidents
  older than the policy, or 24 hours, are skipped). A tenant whose only destination is a Teams
  webhook needs no notification-settings row.
- **Teams**: an Adaptive Card (title by severity, severity / status / priority / times, an
  "Open the incident" button), the format both the classic Teams connector and the Workflows
  webhooks accept. Only https URLs on `*.webhook.office.com`, `*.logic.azure.com` and
  `*.api.powerplatform.com` are accepted, so a tenant URL cannot make the platform call internal
  addresses. The webhook is stored encrypted (`SecretCrypto`), returned only masked (last 4
  characters), kept when the masked value is sent back, and never written to the audit log.
- **API** (`/api/settings/notifications/policies`, admin only like the other notification
  settings, writes need an active subscription): list, create, update, delete, and `/test`
  (a labelled test to the policy's destinations, recorded nowhere; it reports channels the
  policy names but the organization has not enabled). Sites must belong to the tenant. Every
  change is audited (`notification_policy.*`).
- **UI**: a "Notification policies" panel on Settings > Notifications (add, edit, enable / turn
  off, test, delete, a line saying whether policies are in force) and policy destinations named
  in the delivery log. Type-checked and linted; **not verified in a browser** (another session's
  dev server holds this folder).
- **Not in this slice**: dynamic baselines (slice 2), quiet hours and escalation steps, routing
  by device environment or business service.

## 14. P9 slice 2: dynamic baselines

Fixed thresholds miss what is normal for a sensor at a given time: 70 % CPU at noon can be
fine and alarming at 3 a.m.

- **Learning** (`app/services/monitoring/baseline_service.py`): opt-in per sensor. From the
  sensor's own primary-channel history (P1) over the last 14 days, in SQL: mean, standard
  deviation and count per hour of the day (UTC) and overall. Relearned hourly by the job, and at
  once when the settings change.
- **Judging** (`baseline_math.judge`, pure): the latest reading against its hour (or the overall
  figures when that hour has fewer than 5 samples), as z = distance from the expected value in
  standard deviations. Anomalous at or beyond the sensitivity (2-6, default 3), higher, lower or
  either. Honesty rules: under 3 days or 30 readings of history the sensor is "learning" and never
  alerts; a flat series gets a spread floor (5 % of its mean) instead of alarming on any change;
  an anomaly clears only below 75 % of the sensitivity, so a value at the limit does not flap; a
  reading older than 2 hours is not judged ("stale") and a paused or disabled sensor is not judged
  ("paused"), and in both cases an open anomaly alert resolves.
- **Alerts** (`AlertService.evaluate_baseline`): a warning alert, `source = baseline`, keyed as a
  signal (`baseline:<sensor id>` on the sensor's source) rather than by sensor id, so the sensor's
  own status alert is never touched and the one-active-alert-per-sensor rule holds. It says what
  the sensor reads, what it usually reads at this hour and how far off it is; it correlates,
  updates and auto-resolves like other alerts, so incidents and notification policies (slice 1)
  apply.
- **Storage** (`sensor_baselines`, migration `f4b1d7e9c3a5`): one row per sensor with its
  settings, learned figures and latest verdict.
- **Job** (`sensor_baselines`, every 5 minutes): enabled baselines (and any still anomalous),
  least recently checked first, at most 2,000 per pass.
- **API**: `GET` / `PUT /api/monitoring/sensors/{id}/baseline` (read `monitoring.read`; change
  `monitoring.update` and an active subscription, audited as `sensor.baseline_updated`) and
  `GET /api/monitoring/baselines` (anomalous first).
- **UI**: a "Dynamic baseline" panel on the sensor page: state, the usual range for the current
  hour and what it is based on, the latest reading and its distance, how much history is used;
  with permission, turn on / off, direction and sensitivity. Type-checked and linted; **not
  verified in a browser**.
- **Not in this slice**: day-of-week seasonality, baselines for interface channels, and turning
  baselines on in bulk from the sensor library.

## 15. P10 slice 1: Copilot over observability data

The Copilot's query path builds a bounded, real-data context from keyword matches (alerts,
incidents, tickets, SOPs) and lets the local model explain it; it never invents data. None of
the observability data from P1-P9 reached it.

- **Context providers** (`app/services/copilot_observability.py`), included only when the
  question is about them (whole-word matching, so "login" or "report" trigger nothing):
  sensors (counts by status, failing sensors), network (interface totals, busiest links,
  interfaces down while enabled, VPN tunnels down), logs (warning / critical lines in the last
  hour, latest 10), traces (services by error rate and p95, last hour), unusual readings
  (anomalous baselines, furthest first) and monitoring coverage (hosts per gap rule).
- **Named hosts**: a question naming a device by name, hostname or address (up to 3) gets that
  device's failing sensors, enabled-but-down interfaces, open alerts and recent warning /
  critical log lines, which is the evidence an operator would collect first.
- **Bounds and safety**: read-only, tenant-scoped, at most 10 items per list and 200 characters
  per log line; each provider runs in its own savepoint and degrades to a note instead of
  failing the turn. Observability context is included only for callers with `monitoring.read`
  (the chat itself still needs `incidents.read`); everyone else gets exactly the previous
  context.
- **Tested** with the language model stubbed: the assembled context on real rows, tenant
  isolation, and the chat route with and without `monitoring.read`. The answers a real model
  gives over this context were not evaluated.
- **Not in this slice**: feeding the same evidence into the RCA engine, and LLM tool calling.

## 16. P11 slice 1: period reports

The existing reports (infrastructure summary, alert analysis, collector performance, SOP log)
show current state from live APIs; none covers a period from recorded history or exports.

- **Availability** (`GET /api/reports/availability`): per device, the share of recorded
  reachability checks that were up (the canonical `reachability` telemetry written at each
  collection), with checks, failed checks and first / last check. Devices with no check in the
  period are counted as not measured, never as available. Summary: fleet availability (up checks
  over all checks), devices under 99.9 % and under 99 %. A percentage of checks, not of
  wall-clock time, and stated as such.
- **Sensor performance** (`GET /api/reports/sensors`, optional `metric_type`): per sensor,
  average / minimum / maximum / p95 of its primary-channel readings, and the number of its
  status alerts and the minutes they were open within the period (time outside the period is not
  counted).
- **Interface traffic** (`GET /api/reports/interfaces`): per interface, average and peak inbound
  / outbound traffic and utilization and peak error rates, from the P4 history.
- All computed in the database, read-only, `monitoring.read`, tenant-scoped, a window of up to 90
  days (default 7), at most 2,000 rows.
- **UI**: three report pages under Reports (cards on the Reports page and in the sidebar), each
  with presets from 24 hours to 90 days or custom dates (UTC), summary tiles, the table and CSV
  export through the existing formula-safe CSV helper. Type-checked and linted; the three pages
  were checked in a browser against the dev tenant on 2026-11-11 (90-day availability, sensor
  table and empty interface state render, no console errors). Run against the dev tenant for September to early October (read-only): 10 devices
  measured at 91.2 % fleet availability, one at 29.5 %, 259 not measured; 20 sensors with alerts
  in the period; no interface history (it starts with P4).
- **Not in this slice**: scheduled / emailed reports, PDF, and availability weighted by time.

## 17. P8 slice 2: APM section

Per-service health over time on the spans P8 slice 1 stores. No new table or migration: the
existing `(tenant, service, start)` and `(tenant, start)` indexes cover every query.

- **API** (on `/api/traces`, `monitoring.read`, tenant-scoped, windows up to 7 days; the service
  name is the rest of the path, so names with "/" work):
  - `/services/{service}/timeseries`: requests, errors, error rate and p50 / p95 / p99 of the
    service's entry spans per bucket. The bucket is the smallest of 1 / 5 / 15 / 30 min,
    1 / 3 / 6 h that keeps the window within 120 points (1 h → 1 min, 24 h → 15 min,
    7 d → 3 h). Empty buckets are measured zeros, with latency `null`, never 0 ms.
  - `/services/{service}/operations`: per span name, the calls, errors, p50 / p95, total time
    and share of the service's time (top 100 by time).
  - `/services/{service}/dependencies`: the service-map edges into (callers) and out of
    (callees) the service, from the same join as `/service-map` (now one shared query).
  - `/errors` (optional `service`): failed spans grouped by service, span name and exception:
    the type and message of OpenTelemetry's `exception` span event, else the span's status
    message (cut to 300 characters). Count, traces, first / last seen and the most recent
    trace as an example; top 100, most frequent first.
- **UI** ("APM" under Observe, children Services / Errors / Traces): one time range (1 h / 6 h /
  24 h / 7 d) in the URL (`?range=`), shared by every APM page and kept on links.
  - `/apm`: services with a request trend line (first 25 services), error rate and entry
    latency, plus the service map.
  - `/apm/services/{name}`: requests, error rate and latency (p95 line with the p50 to p99
    band) charts, operations, callers and callees, the service's errors, and recent failed and
    slow (at least its p95) traces, each opening the trace waterfall.
  - `/apm/errors`: error groups across services.

  `/traces` is unchanged; its service map is now the shared `components/apm/ServiceMap`.
- **Not in this slice**: linking services to hosts (`host.name`), latency / error-rate alert
  rules, traces on incidents, logs by trace id, protobuf and gzip.

## 18. P12 slice 1: Integration Center

- **Overview** (`GET /api/integration-center`): every monitoring source (synthetic test sources
  left out) and every generic integration of the tenant, each with one operator-facing `state`
  derived only from what was recorded: `disabled`, `failing` (last collection failed, or the
  integration reports a connection / authentication / discovery failure), `degraded` (the health
  state machine said so), `never_synced`, `stale` (no success for three poll intervals, at least
  15 minutes) or `healthy`. Per source: last success / failure / attempt, next run, consecutive
  failures, last error (cut at 500 characters), sensors and devices it maintains, runs in the last
  24 hours with failures, success rate (success or partial) and average run time. Worst state
  first. Credentials are reported only as configured or not.
- **Source detail** (`GET /api/integration-center/sources/{id}`): the same row plus the last 50
  collector runs (sync history) and the last 50 source-health events. Another tenant's source is
  a 404.
- Read-only, `monitoring.read`, tenant-scoped. Syncing stays on the existing
  `POST /api/monitoring/sources/{id}/sync` (`monitoring.update`), offered as "Sync now" on the
  detail page.
- **UI**: `/monitoring/integrations` (under Monitoring in the sidebar) with summary tiles, state
  filters and search; `/monitoring/integrations/[id]` with sync history and health events.
  Type-checked and linted; checked in a signed-in browser against the dev tenant on 2026-11-12:
  5 sources, the dev PRTG server failing all 114 runs of the last 24 hours ("Could not reach
  PRTG", 5,929 consecutive failures), four SNMP lab sources disabled, no console errors. "Sync
  now" was not pressed: it runs a real collection. Seen in that data, not changed here: the PRTG
  error text gives no cause after "Could not reach PRTG:", and every repeated failure is audited
  as a `Degraded -> Degraded` transition, which buries real transitions in the event list.
- **Alongside**: the collector pipeline now feeds the existing Prometheus metrics
  (`app.core.metrics`, served at `/metrics`): run outcome, duration and item counts, collector
  phase durations, skipped runs, alerts opened by origin and severity, and scheduler queue depth.
  Recording a metric can never fail a run (`tests/test_collection_metrics.py`).
- **Not in this slice**: per-integration sync history (generic integrations record no runs),
  editing sources from this page.
