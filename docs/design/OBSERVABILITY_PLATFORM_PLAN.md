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
| P5 Infrastructure monitoring | Windows / Linux / VMware host views from existing collectors | **Slice 1 implemented and tested**: server fleet page (`tests/test_server_fleet.py`); per-host detail already existed |
| P6 Dashboards & topology | Widget registry over aggregated APIs, saved dashboards per tenant / role; topology already exists, add drill-down to sensors | **Slice 1 implemented** (frontend only): topology drill-down to sensors, alerts, host tabs and interface pages; **slice 2**: saved dashboards and the widget registry (`tests/test_saved_dashboards.py`) |
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
