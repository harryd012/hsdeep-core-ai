# PHASE 3.1 — Backend Contract Integrity

**Status:** audit complete; implementation pending
**Milestone:** PHASE 3.1
**Predecessors:** PHASE 1 `5a7be7729`, PHASE 3 `aab1a06b5`, PHASE 3 wiring `9f8bb8b7e`
**Branch:** `main` (54 commits ahead of `origin/main`, unpushed)

This document records the repository state **before** implementation, per the
milestone's "document the current state before implementation" requirement.
Nothing here describes behaviour that has not been verified by reading the cited
source lines.

---

## 1. Scope

Close four known backend contract gaps so that no dashboard number can be
displayed without an authoritative backend source:

| # | Gap | Contract |
|---|-----|----------|
| 1 | Blocked / Identity / Malware counters | SOC KPIs |
| 2 | `AlertIntelligenceService.get_metrics()` | `GET /api/incidents/intelligence` |
| 3 | `failed_runs` | `GET /api/incidents/collector-health` |
| 4 | `failure_rate_pct` | `GET /api/incidents/collector-health` |

Explicitly **out of scope** for 3.1: PHASE 2 dashboard hierarchy/UX, the
Infrastructure Graph (PHASE 4), any redesign of cards/navigation/sidebar.

## 2. Method

* Whole-tree term scan (962 files: `backend/**`, `frontend/src/**`, `docs/**`;
  excluding `.venv`, `node_modules`, `.next`, `__pycache__`, `.git`) for
  `AlertIntelligenceService`, `alert_intelligence`, `failed_runs`,
  `failure_rate`, `total_runs`, `malware`, `blocked`, `identity`, `threat`,
  `get_metrics`.
* Direct reading of every hit that could own or consume the metric.
* No file in the tree was modified to produce this audit.

Note: `search_codebase` indexes a stale copy of this project inside the chat
workspace (it matched `hsdeep-core/frontend/src/api.ts`, a path that does not
exist in this repository). All findings below come from direct reads of
`I:\AI-LAB\hsdeep-core-ai`.

## 3. Repository state at audit time

| Item | Value |
|------|-------|
| HEAD | `9f8bb8b7e` (wire incident list rows into the investigation workspace) |
| Branch | `main`, 54 ahead of `origin/main` |
| Protected WIP | 22 modified + 109 untracked = 132 entries (another session; untouched) |
| Backend layout | `backend/app/{actions,ai,api,collectors,core,models,repositories,scheduler,schemas,services}` — 43 route modules, 54 service modules, 61 model modules |
| Frontend layout | `frontend/src/{app,components,hooks,lib}` |
| Test runner | `backend/.venv/Scripts/python.exe -m pytest` |

### 3.1 Environment trap (see §9)

There are **two virtualenvs**, both reporting Python 3.11.6:

* `I:\AI-LAB\hsdeep-core-ai\.venv` — the one auto-activated in a shell; it
  produces **3 failures + 9 teardown errors** on `test_incident_lifecycle.py`
  (`RuntimeError: Event loop is closed`, `got Future attached to a different
  loop`, `asyncpg InternalClientError`).
* `I:\AI-LAB\hsdeep-core-ai\backend\.venv` — the correct one, registered with the
  `py` launcher; the same file gives **13 passed**.

`pyproject.toml` declares `requires-python = ">=3.12,<3.13"` while both venvs are
3.11.6. That mismatch is pre-existing and is **not** addressed by PHASE 3.1.

## 4. Baseline test evidence (before any change)

```
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_incident_lifecycle.py -q --no-header
-> 13 passed in 8.19s
```

This file is the direct contract test for both endpoints in scope:
`TestIntelligenceEndpoint` (2 tests) and `TestCollectorHealthEndpoint` (2 tests),
plus lifecycle and cross-tenant tests.

Existing assertions that constrain the solution:

* `test_incident_lifecycle.py:272` — `assert "duplicate_alerts_removed" in body`
  → the field must **remain present**.
* `test_incident_lifecycle.py:321-322` — `healthy + degraded + failing + unknown
  == len(collectors)` → the four state buckets must stay complete and disjoint.


## 5. Finding 3.1.1 — Blocked / Identity / Malware: NO authoritative source

**Frontend expectation.** `frontend/src/lib/soc/socModels.ts` already models the
correct truth state. `SocKpi.value` is `number | null` and `SocKpi.source` is
`"live" | "fallback" | "unavailable"`, and the header states: *"The backend has
NO SOC-specific endpoints yet (threat-trend series, attack vectors, MITRE ATT&CK
coverage, geo threat map, workflow stats, identity/malware/blocked counters)"*
(lines 6-12).

`frontend/src/lib/soc/socAdapter.ts:40-46`:

```ts
const FALLBACK_KPI = {
  // No corresponding security counters or historical series are exposed.
  // Missing telemetry is not zero and must never be replaced with demo values.
  blocked: null, blockedChange: null,
  identity: null, identityChange: null,
  malware: null, malwareChange: null,
};
```

**Rendering.** `components/soc/SocDashboard.tsx` — the component that would
render these three KPI cards — is imported by **nothing** (verified by scanning
every `.tsx` for `SocDashboard`). The cards are unreachable from any route, so no
fabricated value is currently displayed.

**Backend reality.** There is no persisted, aggregatable source:

* `backend/app/models/` (61 modules) contains **no** SASE, verdict, session or
  URL-filter model — scanning every model module for `sase|verdict` returns zero
  hits.
* `backend/app/services/network_intelligence/sase.py` is an **in-memory,
  per-session policy evaluator**. It defines `MALICIOUS_URL_CATEGORIES = {…
  "malware", "phishing", "command-and-control", "c2", "botnet" …}` (line 42) and
  `CASB_BLOCKED_CATEGORIES` (line 54), and returns `StageVerdict` /
  `DECISION_BLOCK` for a single payload. Nothing is counted or stored.
* `app/services/network_intelligence/correlation.py:44` consumes
  `sase.get("decision") == "BLOCK"`, and its header states the status is
  *"Advisory-only"* (line 11).
* The word **`identity`** in this repository means **device identity resolution**
  (`services/discovery/identity.py`, `recognition/identity.py`,
  `tests/test_identity_resolution.py`, `tests/test_resource_identity.py`). It is
  not a user-identity or identity-threat counter.

**Conclusion.** No authoritative source exists for `blocked`, `identity` or
`malware`. Per the milestone rule (*"If it does not exist: DO NOT fabricate a
metric. Use an explicit unavailable/null state"*), the correct action is to keep
the existing typed `null` + `source: "fallback"` representation and add **no**
backend metric. Any counter here would have to be invented.

## 6. Finding 3.1.2 — `AlertIntelligenceService.get_metrics()`: superseded dead code, but its endpoint fabricates one KPI

**The service exists** at `backend/app/services/alert_intelligence_service.py:45`
(`get_metrics(self, tenant_id: uuid.UUID) -> AlertIntelligenceMetrics`, line 49).
It is tenant-scoped and returns 7 fields, including `total_raw_alerts` and
`total_displayed_alerts`, which the endpoint never exposed.

**It is dead code.** A whole-tree scan for `AlertIntelligenceService` returns
exactly three hits: its own definition, a frontend comment, and a docs table row.
**Nothing imports it.**

**What ships instead.** `GET /api/incidents/intelligence`
(`backend/app/api/routes/incidents.py:93-123`) computes the metrics from
`IncidentRepository.intelligence_counts()` — a single conditional-aggregate scan
with a documented performance history (*"Measured on the real 2.4M-incident
tenant: 9.9s -> ~2.4s"*, `incident_repository.py:322`) — plus
`AlertRepository.count()`.

**The endpoint's definitions diverge from the service's:**

| Metric | `AlertIntelligenceService` | Shipped `/intelligence` |
|--------|---------------------------|--------------------------|
| `active_incidents` | open + acknowledged + investigating + **monitoring** | open + acknowledged + investigating |
| `correlated_incidents` | `alert_count > 1` (any status) | `status == open AND alert_count > 1` |
| `duplicate_alerts_removed` | `max(0, SUM(occurrence_count) − displayed − suppressed)` | **`= suppressed`** (line 121) |
| `noise_reduction_pct` | `(raw − displayed) / raw` | `suppressed / (open_alerts + suppressed)` |
| `total_raw_alerts` / `total_displayed_alerts` | returned | not returned (the frontend correctly omits them — `api.ts:2203-2207`) |

**Impact of the `duplicate_alerts_removed = suppressed` defect.** The value is
rendered as its own KPI card, immediately adjacent to the suppressed card:

* `frontend/src/app/(dashboard)/incidents/page.tsx:163-164` — `"Suppressed"` and
  `"Duplicates Removed"` cards.
* `frontend/src/app/(portal)/portal/page.tsx:272-274` — both values printed as
  separate sentences.

Two distinct operator-facing KPIs therefore always show the same number, and one
of them is not backed by its own authoritative computation.

**Consumers of the shipped contract** (must not break):
`incidents/page.tsx`, `(portal)/portal/page.tsx`, `hooks/useDashboardData.ts:79`,
`lib/dashboard/dashboardDataAdapter.ts:542`.

**Resolution.** The service as a whole is **obsolete** — resurrecting it would
revert a measured 4× performance fix and create duplicate metric logic, both of
which the milestone forbids. It is documented here as superseded and stays
unimported. The *fabricated* field is nevertheless fixed, by computing the
service's own documented definition through the authoritative repository path
(§8).

## 7. Findings 3.1.3 / 3.1.4 — `failed_runs` and `failure_rate_pct`: the authoritative source already exists and is not being used

**Authoritative source.** `backend/app/services/collector_health_service.py`
(`CollectorHealthService`, "Sprint 15"). It is read-only, derives everything from
the immutable `CollectorRun` audit table, and is tenant-scoped
(`get_health_for_tenant(tenant_id)` filters `MonitoringSource.tenant_id`).

Its 13-field `CollectorHealth` dataclass already contains every value the API is
missing:

| Field | Derivation (verified lines) |
|-------|----------------------------|
| `total_runs` | `COUNT(*)` over `CollectorRun` where `started_at >= cutoff` (79-82) |
| `successful_runs` | `status IN ('success','partial')` (84-89) |
| `failed_runs` | `status == 'error'` (91-96) — terminal failure per project semantics |
| `failure_rate_pct` | `failed_runs / total_runs * 100` (119); `total_runs == 0 -> 0.0` (120-122) |
| `success_rate_pct` | `successful_runs / total_runs * 100` (118) |
| `last_success_at` / `last_failure_at` / `last_error` | `MAX(finished_at)` / latest `status='error'` row (98-111) |
| `retry_count` | `failed_runs` (113-115) |
| `current_state` | `total_runs == 0 -> "unknown"`; `failure_rate >= 50 -> "failing"`; `> 0 -> "degraded"`; else `"healthy"` (124-131) |

The window is `window_hours` (default 24) — **the same window and default as the
route**.

**The route does not use it.** `GET /api/incidents/collector-health`
(`app/api/routes/incidents.py:126-179`) re-implements the aggregation inline via
`CollectorRunRepository.list(tenant_id, monitoring_source_id=..., since=..., limit=100)`,
and diverges in four ways:

| Aspect | Shipped route | `CollectorHealthService` |
|--------|---------------|--------------------------|
| `total_runs` | `len(runs)` — **truncated at `limit=100`** | exact `COUNT(*)` |
| success definition | `status == 'success'` only | `status IN ('success','partial')` |
| failure rate | **not computed at all** | `failed_runs / total_runs` |
| `failed_runs`, `failure_rate_pct`, `successful_runs`, `retry_count`, `last_failure_at` | **absent from `CollectorHealthItem`** | present |

The `limit=100` truncation is a genuine correctness defect for any busy source:
once a source has more than 100 runs in the window, `total_runs` under-reports and
the success rate is computed over a truncated sample.

**The frontend already documents the absence**
(`frontend/src/lib/api.ts:2218-2221`): *"failure_rate_pct / last_failure_at /
successful_runs / failed_runs / retry_count are NOT returned by
GET /api/incidents/collector-health … so they are deliberately not declared - a
declared-but-absent field renders `undefined`."*

**Resolution.** Reuse the existing service (deleting the duplicate inline
aggregation — the milestone rule is *"Do not create a second execution
counter"*), and extend `CollectorHealthItem` to carry the fields the service
already computes. `CollectorHealthList`'s four state buckets stay intact, so
`test_incident_lifecycle.py:321-322` remains valid.

### 7.1 Documented behavioural alignment

Adopting the authoritative service changes two previously-displayed values. Both
are corrections, and both are recorded here because they are visible to
operators:

1. **`success_rate_pct` now counts `partial` runs as successes.** Under the old
   route a source whose runs were all `partial` reported `0%` success and was
   classed `failing`; under the authoritative service it reports `100%` success
   and `0%` failure. The authoritative definition wins because it is the
   documented Sprint 15 semantics (`collector_health_service.py:84-89`).
2. **`last_success_at` / `last_error` are no longer window-limited.** The service
   reports the last success/failure ever recorded for the source; the old route
   reported the last one *inside the window*, so a source failing for 3 days
   showed "—" while the service shows the real (3-day-old) timestamp.

`current_state` counts (`healthy_count` / `degraded_count` / `failing_count` /
`unknown_count`) feed the dashboard pipeline widget via
`dashboardDataAdapter.ts:659-668`; they will reflect the authoritative state
machine after this change.

## 8. Authoritative source map (result)

| Metric | Authoritative owner | Action |
|--------|--------------------|--------|
| `blocked`, `identity`, `malware` | **none exists** | document; keep the frontend `null` / `unavailable` state; add nothing |
| `AlertIntelligenceService.get_metrics()` | superseded by `IncidentRepository.intelligence_counts` + `AlertRepository` | document as obsolete; do not import |
| `duplicate_alerts_removed` | `Alert` occurrence volume (the service's documented definition) | compute via a new single-scan aggregate in `AlertRepository` |
| `failed_runs` | `CollectorHealthService` | expose from the existing service |
| `failure_rate_pct` | `CollectorHealthService` | expose from the existing service |

## 9. Tenant isolation

Both endpoints resolve the tenant server-side and never trust the browser:

* `intelligence` — `tenant_id: uuid.UUID = Depends(get_tenant_id)` (`incidents.py:95`)
* `collector-health` — `tenant_id: uuid.UUID = Depends(get_tenant_id)` (`incidents.py:128`)

Every aggregate in scope is filtered on that value:
`IncidentRepository.intelligence_counts` filters `Incident.tenant_id` (line 341),
`AlertRepository.count` filters `Alert.tenant_id`, `AlertRepository.summary`
filters `Alert.tenant_id` (359-375), and `CollectorHealthService.get_health_for_tenant`
filters `MonitoringSource.tenant_id` (64-67) before any run is counted.
Existing coverage lives in `tests/test_tenant_isolation.py`.

## 10. Environment traps and out-of-scope findings

Recorded, **not** fixed by PHASE 3.1:

1. **Two virtualenvs.** Use `backend/.venv/Scripts/python.exe`; the repo-root
   `.venv` produces 3 failures + 9 teardown errors on the same test file (§3.1).
2. **`requires-python` mismatch.** `pyproject.toml` requires 3.12–3.13; both
   venvs are 3.11.6.
3. **Invented demo data still present in the SOC adapter.**
   `socAdapter.ts:48-81` defines `FALLBACK_TREND`, `FALLBACK_VECTORS` (invented
   counts 42/35/28/22/14/9/4/2), `FALLBACK_MITRE` (`78%`, `142/298/381`) and
   `FALLBACK_POINTS` (invented cities). They are labelled `source: "fallback"`
   and render as "demo data", and `SocDashboard` is currently unmounted, so
   nothing displays them today. Removing them is a separate cleanup.
4. **`CoreHUD`** is an orphan component with hardcoded metrics (previously
   reported; unchanged here).
5. **Collector-health query cost.** The service issues ~6 aggregate queries per
   source (an N+1 the old route also had, at 1 query per source). A future
   single-scan aggregate in the style of `intelligence_counts` would remove it;
   correctness is prioritised over speed for this milestone.
6. **`AlertIntelligenceMetrics` Pydantic defaults.** Every field is declared
   `= 0` (`incidents.py:68-72`), so a field that was ever omitted from the
   response would silently serialise as a fabricated `0` instead of failing.
   Addressed in this milestone by making all five fields required.

---

## 11. Implementation (what changed)

| File | Change |
|------|--------|
| `backend/app/repositories/alert_repository.py` | **new** `occurrence_volume(tenant_id)` — one conditional-aggregate scan returning `(raw, displayed, suppressed)`. Base filter mirrors `count()` exactly (`tenant_id` + not soft-deleted), so its `suppressed` is provably identical to `count(tenant_id, is_suppressed=True)`. |
| `backend/app/api/routes/incidents.py` | `/intelligence`: `duplicate_alerts_removed` is now `max(0, raw − displayed − suppressed)` (the service's documented definition) instead of `= suppressed`; the former separate suppressed `count()` round trip is folded into the same scan, so the endpoint issues **no additional query**. `AlertIntelligenceMetrics` fields are now required (no `= 0` defaults). `/collector-health`: delegates to `CollectorHealthService.get_health_for_tenant(tenant_id, window_hours=…)`; `CollectorHealthItem` gained `failure_rate_pct`, `successful_runs`, `failed_runs`, `retry_count`, `last_failure_at`. Removed the now-unused `datetime`/`timezone`/`timedelta` and `MonitoringSourceRepository`/`CollectorRunRepository` imports. |
| `frontend/src/lib/api.ts` | `CollectorHealth` declares the five newly-shipped fields; the stale "these are NOT returned" note is replaced with the current contract. No UI redesign, no new rendering. |
| `backend/tests/test_incident_lifecycle.py` | **new** fixtures `contract_tenant` + `bare_tenant`, and two test classes (`TestIntelligenceContractIntegrity`, `TestCollectorHealthContractIntegrity`). Existing tests and assertions untouched. |
| `docs/architecture/phase-3-1-backend-contract-integrity.md` | this document (the `docs/architecture/` directory is new). |

**No change** to `blocked` / `identity` / `malware`: §5 established that no authoritative source exists, so adding a counter would mean inventing one.

**Known semantic change** (documented in §7.1): `success_rate_pct`, `last_success_at`, `last_error` and `current_state` now follow the authoritative service's definitions (partial = success; last success/failure not window-limited; state derived from the failure rate).

## 12. Verification evidence

**Baselines, taken before any change** (using `backend/.venv`):

```
tests/test_incident_lifecycle.py                                      13 passed          8.19s
tests/test_tenant_isolation.py tests/test_repositories.py
tests/test_ai_insight_bounded.py                                      88 passed   223.80s (3:43)
```

**After the change:**

| Check | Result |
|-------|--------|
| Module import + model contract inspection | OK — `occurrence_volume` present; all five `AlertIntelligenceMetrics` fields `required=True`; `CollectorHealthItem` carries the five new fields |
| `frontend` `tsc --noEmit` | **0 errors** |
| `eslint src/lib/api.ts` | **0 findings** |
| Direct DB verification (36 assertions) | **36 passed, 0 failed** — see §12.1 |
| `pytest tests/test_incident_lifecycle.py` | **22 of 26 passed, 0 failed**, then the run stalled in a fixture teardown — see §12.2 |

### 12.1 Direct DB verification

Because the shared dev DB was simultaneously under heavy load from another
session (see §13.1), the contract was additionally verified by driving the exact
code the endpoints call — no HTTP layer, and only the small tables
(`monitoring_sources`, `collector_runs`, `alerts`). Every expectation asserted by
the new pytest classes was reproduced:

* **Collector health (27 checks).** `Contract Mix` (2 success + 1 partial + 1
  error inside 24h, 1 error 36h outside): `total_runs=4`, `successful_runs=3`,
  `failed_runs=1`, `success_rate_pct=75.0`, `failure_rate_pct=25.0`,
  `retry_count=1`, `last_failure_at` present, `last_error` carried,
  `current_state=degraded`. `Contract Failing` (1 success + 3 errors):
  `failure_rate_pct=75.0`, `current_state=failing`. `Contract Healthy`: 0.0 /
  `healthy`. `Contract Empty` (no runs): `total_runs=0`, `failure_rate_pct=0.0`,
  finite, `last_success_at=None`, `last_failure_at=None`,
  `current_state=unknown`. `window_hours=72` picks up the 36h-old failure:
  `total_runs=5`, `failed_runs=2`, `failure_rate_pct=40.0`. All rates finite and
  within 0–100; every state one of the four known buckets; another tenant
  returns no collectors.
* **Alert volume (9 checks).** `occurrence_volume` → `raw=7`, `displayed=1`,
  `suppressed=1`; `duplicate_alerts_removed = 5` while
  `suppressed_alerts = 1` (proving the two KPIs are now independent
  measurements); `suppressed` equals `count(tenant_id, is_suppressed=True)`
  exactly (no drift from folding the query); a second tenant reports `0`/`0`.

### 12.2 `pytest tests/test_incident_lifecycle.py` (partial run)

| | |
|---|---|
| Baseline, before the change | **13 passed, 0 failed**, 8.19s |
| After the change | **22 passed, 0 failed**, then the run stalled inside a fixture teardown |

The two new classes add 13 tests. The 22 that completed include **all four
`TestIntelligenceContractIntegrity` tests** and **five of the nine
`TestCollectorHealthContractIntegrity` tests** — i.e. every assertion that
exercises the changed behaviour through the real HTTP endpoints:

* `duplicate_alerts_removed` is now independent of `suppressed_alerts` (5 vs 1);
* `failure_rate_pct` / `successful_runs` / `failed_runs` / `retry_count` /
  `last_failure_at` are present with the authoritative values
  (`degraded`, `success_rate_pct=75.0`, `failure_rate_pct=25.0`);
* `partial` is counted as a success (`successful + failed == total`);
* `failing` at 75% failure; `healthy` at 0% failure;
* `total_runs == 0` → `current_state="unknown"`, `failure_rate_pct == 0.0`,
  finite (no divide-by-zero), both timestamps `None`.

The four tests that did not execute are
`test_state_buckets_cover_every_collector`, `test_window_hours_is_applied`,
`test_collector_health_is_tenant_isolated` and
`test_item_schema_includes_failure_fields`. Every one of their assertions is
already proven by §12.1 (the state buckets, the 72h window and the isolation
checks were all reproduced there), and the last is a pure model-schema check
performed in §12's first row.

**The stall is environmental, not a failure.** `pg_stat_activity` showed the
fixture's `DELETE FROM tenants` **executing** (`wait_event_type = None` — running,
not lock-blocked) for 10+ minutes at a time, because a single tenant delete fans
out to 77 cascading FKs including the 5.2 GB `incidents`, 1.78 GB `alert_history`
and 1.2 GB `metrics` tables (§13.2), while a second session concurrently saturated
the disk and left connections `idle in transaction` (§13.1). **No test failed or
errored at any point.**

## 13. Runtime environment findings (recorded, not fixed here)

### 13.1 The dev DB is a contended resource

While this milestone ran, a **second session's backend** (`uvicorn app.main:app
--reload`) was active and issuing large queries against `incidents`. Measured
directly via `pg_stat_activity`:

* 13 concurrent non-pytest sessions, several running
  `SELECT incidents.tenant_id, … ` and `SELECT count(*) FILTER (…)` for 60s+,
  all in `wait_event_type = IO` (`AioIoCompletion`).
* `SELECT count(*) FROM incidents` = **21.4s** on its own.

Consequently any test that touches `incidents` — including the pre-existing
`/intelligence` endpoint tests — queues behind that load. Baselines taken before
the load appeared were fast (13 tests / 8.19s; 88 tests / 3:43), so this is
contention, not a regression. A `DELETE FROM tenants` for a **childless** tenant
was measured at **1.14s**, so the tenant-cleanup pattern itself is cheap.

Practical mitigations applied to the new tests only:

* A new `bare_tenant` fixture (tenant with no children) is used wherever a test
  merely needs "some other tenant", instead of `incident_tenant`, whose teardown
  cascades into the multi-GB `incidents` table.
* The new tests assert only on small tables (`monitoring_sources`,
  `collector_runs`, `alerts`).

### 13.2 Every tenant delete fans out to 77 tables

`pg_constraint` shows **77 foreign keys reference `tenants` with
`ON DELETE CASCADE`** — from `alerts` and `incidents` to `metrics`,
`alert_history`, `audit_events` and every ticket/SOP/workflow table. All 77 FK
columns are indexed, but each tenant delete still performs one probe per table,
several of which are multi-GB:

| Table | Total size | Est. rows |
|-------|-----------|-----------|
| `incidents` | 5 204 MB | 2 570 025 |
| `alert_history` | 1 783 MB | 4 611 641 |
| `metrics` | 1 227 MB | 3 075 004 |
| `audit_events` | 151 MB | 315 277 |
| `alerts` | 99 MB | 1 312 |
| `collector_runs` | 8 968 kB | 16 614 |
| `tenants` | 736 kB | 2 306 |

The `tenants` table holds **2 306 rows against 2 229 leftovers** reported by the
previous session — consistent with teardown deletes being abandoned. This is a
test-infrastructure/DB-hygiene problem, out of PHASE 3.1's scope.

### 13.3 Torn Next.js generated artifacts break `tsc`

`frontend/.next/dev/types/routes.d.ts` and `validator.ts` were half-written
(the former jumps mid-token from an interface body to `es": {}`), producing
**29 spurious `tsc` errors** with no relation to the source. Both are inside
`.next/`, which `frontend/.gitignore:17` ignores, and Next regenerates them.
Deleting `.next/dev/types/` restored a clean `tsc --noEmit` (**0 errors**).
This reproduces the trap recorded by the previous session.

### 13.4 Two virtualenvs (restated from §3.1)

Use `backend/.venv/Scripts/python.exe`. The repository-root `.venv` yields 3
failures + 9 teardown errors on the same test file.



