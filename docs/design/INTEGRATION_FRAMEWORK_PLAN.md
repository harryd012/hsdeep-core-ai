# Integration framework: P12 slice 2 plan

Status: **approved 2026-11-12**. Clean-up and step 2a **implemented and tested** (see §5); 2b and 2c
planned.

Replaces the uncommitted "Universal Integration Registry – Phase 2" draft found on
`feat/observability` (`backend/app/services/integration/`,
`backend/alembic/versions/add_integration_registry_entities.py`,
`backend/alembic/versions/add_integration_idempotency.py`). Parent plan:
`docs/design/OBSERVABILITY_PLATFORM_PLAN.md`, row P12.

## 1. What already exists

The codebase already has four integration abstractions. Each one has its own job:

| Abstraction | Job | Code | Storage |
|---|---|---|---|
| Collector framework | Read: discover devices, pull metrics, events, alerts and backup data on a schedule | `app/collectors/base.py` (`BaseCollector`, `CollectorRegistry`), 28 collectors in `app/collectors/` (PRTG, FortiGate, FortiManager, Meraki, AWS, Azure, GCP, Zabbix, Datadog, VMware, Veeam, Kubernetes, SNMP, WMI, …), `app/services/monitoring/collection_orchestrator.py`, `rate_limiter.py`, `retry_backoff.py`, `source_health_manager.py` | `monitoring_sources` (encrypted credentials through `SecretCrypto` / `MonitoringSourceConnectionInfo`), `collector_runs`, `source_health_audit` |
| ITSM connectors | Two-way tickets: push / pull / webhooks | `app/services/itsm/` (`BaseITSMProvider`, `ProviderRegistry`, `ProviderFactory`, `CredentialManager`, `ConnectorManager`), `providers/servicenow.py`, `app/api/routes/itsm.py` | `itsm_providers`, `itsm_connections`, `itsm_sync_jobs` (retry + dead letter) |
| Device Action Plane | Write: classified, policy-checked actions on devices | `app/actions/` (`DeviceActionProvider`, `ActionProviderRegistry`, `policy.py`, `verification.py`), providers for ICMP, SNMP, PRTG, Cisco, Aruba | action records |
| Generic integrations | A status record per external system | `app/models/integration.py`, `app/services/integration_service.py`, `app/api/routes/integrations.py` | `integrations` |

Everything the draft set out to provide is already covered:

| Draft feature | Already provided by |
|---|---|
| Provider interface + registry + factory | `BaseCollector` / `CollectorRegistry`; `BaseITSMProvider` / `ProviderRegistry` / `ProviderFactory`; `ActionProviderRegistry` |
| Encrypted credentials | `SecretCrypto`, `app/services/itsm/credential_manager.py`, monitoring-source credentials |
| Health monitoring | `source_health_manager.py` (state machine + audit), Integration Center (P12 slice 1) |
| Quota / retry / timeout | `rate_limiter.py`, `retry_backoff.py`, ITSM sync-job retries |
| Execution logs | `collector_runs`, `itsm_sync_jobs`, action records |
| PRTG, ServiceNow, FortiGate, FortiManager, Meraki, AWS, Azure providers | existing collectors and the ServiceNow ITSM provider |
| "Skills" with idempotency | Device Action Plane (classification, policy, verification) |

The draft also can't be completed as it stands. It imports models that don't exist
(`IntegrationConnection`, `IntegrationSkill`, `IntegrationExecutionLog`) and an `AuditService`
that `audit_chain.py` doesn't define. Its migration branches off `f7a4d5e6a8b1`, which gives Alembic
a second head. Its `downgrade()` drops about eight tables that `upgrade()` never creates. The dev
database is still at `f4b1d7e9c3a5`, so it was never applied.

**Decision:** don't build a fifth framework. Delete the draft. Close the real gaps that P12 slice 1
left, using the abstractions above.

## 2. Gaps worth closing

1. **Noisy and unclear health data** (seen in the 2026-11-12 browser check):
   - `PrtgCollector.authenticate` formats `httpx` connection errors with `str(exc)`, which is often
     empty. The operator then sees "Could not reach PRTG:" with no cause.
   - `SourceHealthManager.evaluate_and_update` records a failure that changes no state as
     `event_type="health_transition"`, so a long outage shows thousands of `Degraded -> Degraded`
     "transitions" and buries the real ones.
2. **ITSM connections are not in the Integration Center.** A failing ServiceNow sync, or a pile of
   dead-letter jobs, appears nowhere on the page that is meant to show "everything that feeds the
   platform". MSPs feel this the most: a broken ticket bridge means missed SLAs.
3. **No catalogue of what can be connected.** The provider registries are only known to code.
   Operators and the UI have no single list of the integration types available, what each one does
   (collect / tickets / actions) and what configuration it needs. This was the real need behind the
   draft's "registry".

Out of scope here (each needs its own plan and approval, ranked by commercial value): new
providers such as FortiAnalyzer, Prometheus as a source, Grafana alerts, GitHub. Each would be a
`BaseCollector` (read) or `BaseITSMProvider` (tickets), not a new framework.

## 3. Proposed scope: three steps, no migration

### 2a. Health data quality (small)
- `app/collectors/prtg.py`: say what went wrong as `"<ExceptionType>: <message>"` (for example
  `ConnectTimeout`), with a fallback when the message is empty. Check the other collectors'
  `authenticate` for the same `{exc}` pattern and fix them the same way.
- `app/models/source_health_audit.py`: add the event type `failure_recorded` (the column is a
  string, so no migration is needed). `source_health_manager.py` records failures that change no
  state with this type and keeps `health_transition` for real changes. Existing rows stay as they
  are.
- `integration_center.py` source detail: return state changes first, with an
  `include_failures=true` query parameter to show every failure. The UI gets a toggle to match.
- Tests: extend `tests/test_integration_center.py` and the source-health tests. Add a PRTG
  `authenticate` test with an exception whose message is empty.

### 2b. ITSM connections in the Integration Center (medium)
- `integration_center.py`: add rows of `kind="itsm"` built from `itsm_connections`. The state is
  derived only from what was recorded: `disabled`, `failing` (connection status is error /
  authentication failure, or the most recent sync job is `dead_letter`), `degraded` (sync jobs
  retrying), `never_synced`, `healthy`. The row includes 24-hour job statistics (total / failed /
  dead letter).
- `GET /api/integration-center/itsm/{id}`: the same row plus the last 50 `itsm_sync_jobs` as sync
  history. Another tenant's connection returns 404.
- Permissions: `monitoring.read` for the overview (same as slice 1). The ITSM detail route also
  needs `itsm.read`, if that permission exists (to be confirmed during the build). Credentials are
  reported only as configured or not. Read-only: retrying stays on the existing ITSM routes.
- UI: ITSM rows on `/monitoring/integrations`; detail at `/monitoring/integrations/itsm/[id]`.
- Tests: state derivation, tenant isolation, a dead-letter job making the row `failing`, the
  permission check.

### 2c. Integration catalogue (small)
- `GET /api/integration-center/catalog`: one list built at request time from `CollectorRegistry`,
  the ITSM `ProviderRegistry` and `ActionProviderRegistry`. Each entry has the type, display name,
  category, capabilities (`collect` / `tickets` / `actions`, plus what each collector produces),
  required configuration fields (no secrets) and how many the tenant has configured. Read-only,
  `monitoring.read`.
- UI: an "Available integrations" tab on `/monitoring/integrations`.
- Tests: every registered type appears, no secret fields appear, counts are per tenant.

### Clean-up (done first, on approval)
Delete `backend/app/services/integration/`, both `add_integration_*` migrations and
`backend/alembic/versions/temp.txt`, then check that `alembic heads` shows one head again.

## 4. Impact

| Area | Impact |
|---|---|
| Database | No new tables or migrations. One new value in a string column (`failure_recorded`). |
| API | Added: `GET /api/integration-center/itsm/{id}`, `GET /api/integration-center/catalog`, `include_failures` on source detail. Overview rows gain `kind="itsm"`. Existing fields are unchanged, so it stays backward compatible. The source-detail default changes from "every event" to "state changes first". This is a behaviour change on an endpoint only the Integration Center page uses. |
| Security | Read-only, tenant-scoped, existing permissions, credentials reported only as configured or not. Collector error text is already cut at 500 characters. Check that the exception type and message carry no secrets (PRTG puts its passhash in the URL, so it is stripped from the message). |
| Performance | The overview adds one grouped query over `itsm_connections` and `itsm_sync_jobs` for the last 24 hours. The catalogue is in memory plus one count query. With fewer audit rows written during long outages, the audit table also grows more slowly. |
| Commercial / MSP | One page shows monitoring *and* ticketing health, so a broken ticket bridge is caught before SLAs are missed. Fewer false "transitions" in the audit trail means less alert fatigue and faster diagnosis (lower MTTR). The catalogue supports onboarding and plan upsell (what else can be connected). |
| Technical debt | Retired: the half-built fifth framework. Still open: `integrations` rows have no connector writing them (`record_collection` has no callers). They stay visible as slice 1 shows them. Whether to fold them into `monitoring_sources` or retire them is a separate decision, because `/integrations` is a public API. |

Estimated complexity: 2a small (about half a day), 2b medium (about 1–1.5 days with UI),
2c small (about half a day). Each step lands as its own commit on `feat/observability` with tests
and an update to the P12 row of the observability plan.

## 5. Progress

### Clean-up — done (2026-11-12)
The draft package and both `add_integration_*` migrations were deleted. `alembic heads` shows
one head again (`f4b1d7e9c3a5`).

### 2a — implemented and tested (2026-11-12)
Two names changed from the plan above: the event type is `run_recorded` (it covers successes
below the recovery threshold as well as repeated failures, both of which were also written as
`health_transition`), and the query parameter is `include_run_events`.

- `app/collectors/base.py`: `describe_error(exc, source)` gives `"<ExceptionType>: <message>"`
  (just the type when the message is empty), with configured secrets scrubbed through
  `readiness.redact`. Used by every "could not reach" message in the PRTG, FortiGate,
  FortiManager, Meraki, Palo Alto, Datadog, Zabbix and PNETLab collectors. AWS, Azure, GCP,
  vCenter, SSH and SNMP already named the type. `passhash` was added to the secret-key hints in
  `readiness.py`.
- `SOURCE_HEALTH_EVENT_RUN_RECORDED = "run_recorded"`: `SourceHealthManager` writes it for
  every run that changes no health state. `health_transition` is now written only for
  Healthy → Degraded. Old rows are unchanged.
- `GET /api/integration-center/sources/{id}` leaves out run-level events by default. These are
  `run_recorded` rows, plus legacy `health_transition` rows whose health status did not change.
  It returns `hidden_run_events` with the count; `?include_run_events=true` returns every event.
  The detail page has an "Include routine runs (N hidden)" toggle.
- Tests: `tests/test_collector_error_text.py`; added to `tests/test_source_health_manager.py`
  (`test_only_state_changes_are_audited_as_transitions`) and `tests/test_integration_center.py`
  (`test_source_detail_hides_run_level_health_events`).
- Checked in a signed-in browser against the dev tenant: the dev PRTG source now reports
  "Could not reach PRTG: ConnectError: All connection attempts failed", where runs from before
  the change show only "Could not reach PRTG:". Its health events show the one real transition
  (Healthy → Degraded, 34 days ago), with 5,951 routine runs hidden. No console errors.
