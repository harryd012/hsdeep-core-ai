# PHASE 5C — Monitoring Data-Path Integrity

**Status:** implemented (two contract defects fixed, one defence-in-depth hardening; two findings documented as deferred)
**Milestone:** PHASE 5C (follows PHASE 5B, which removed the legacy `/prtg/root-sensors` passthrough)
**Branch:** `main`

PHASE 5C audits the monitoring data path end to end — PRTG collector → `Sensor`
persistence → tenant-scoped APIs → frontend dashboard — and fixes correctness,
tenant-isolation and contract defects **without redesigning or duplicating** the
existing pipeline.

No new model, repository, service, route, collector or status map was introduced.
After this phase there is still exactly one PRTG transport, one PRTG status map
and one sensor read path.

---

## 1. Scope and method

The audit traced a single sensor's whole life: where PRTG's numeric status code
is read, how it becomes `Sensor.status`, which API surfaces expose it, and what
the dashboard renders from it. Every read/write path was inspected rather than
sampled:

| Layer | Artefacts inspected |
|-------|---------------------|
| Collector | `app/collectors/prtg.py`, `app/collectors/base.py`, `app/collectors/__init__.py` |
| Persistence | `app/services/monitoring/bundle_saver.py`, `app/repositories/{sensor,monitoring_source,device,metric,event}_repository.py` |
| Orchestration | `app/services/monitoring/collection_orchestrator.py`, `source_health_manager.py` |
| API | `app/api/routes/monitoring.py`, `app/api/routes/devices.py`, `app/services/adapters/prtg_adapter.py` |
| Enrollment | collector enrollment routes, `MonitoringSourceRepository` |
| Models | `app/models/monitoring_source.py`, `app/models/sensor.py` |

The PRTG status vocabulary was cross-checked against Paessler's published
documentation rather than inferred from the existing code, because the existing
code was itself the suspect.

---

## 2. Defect 1 — PRTG severity inversion (fixed)

`_PRTG_STATUS_MAP` in `app/collectors/prtg.py` mapped PRTG's numeric sensor
status codes onto the internal vocabulary and was **wrong in two independent
ways**:

| Code | PRTG meaning | Previous mapping | Correct mapping |
|------|--------------|------------------|-----------------|
| 4 | Warning | `down` | `warning` |
| 5 | Down | `warning` | `down` |
| 7 | Paused by user | `warning` | `unknown` |
| 10 | Unusual | `warning` | `warning` |

The map inverted the two states that matter most — a **Warning was persisted as
Down and a Down was persisted as Warning** — and promoted a deliberately
*paused* sensor to an alarm. Codes 1, 2, 6, 8, 9, 11 and 12 were absent
entirely and silently fell through.

The map is now the full, correct 1–14 table:

```python
_PRTG_STATUS_MAP = {
    1: "unknown",   # Unknown
    2: "unknown",   # Collecting
    3: "up",        # Up
    4: "warning",   # Warning
    5: "down",      # Down
    6: "unknown",   # NoProbe
    7: "unknown",   # Paused by user
    8: "unknown",   # Paused by dependency
    9: "unknown",   # Paused by schedule
    10: "warning",  # Unusual
    11: "unknown",  # Paused by license
    12: "unknown",  # Paused until
    13: "down",     # Down (Acknowledged)
    14: "down",     # Down (Partial)
}
```

An unreachable code can no longer be mistaken for an alarm: only 4, 10 (warning)
and 5, 13, 14 (down) alert; every other code is `unknown`.

### 2.1 The status column itself was ambiguous

Fixing the table alone was not sufficient. PRTG exposes a sensor's status twice
(`status` and `status_raw`) and **which column carries the numeric code versus
the text spelling differs between API surfaces and PRTG versions**. The previous
reader bet on one column and one type, so a payload that carried the code in the
other column degraded every sensor to a non-alarm state.

`_normalize_sensor_status(row)` now resolves in two ordered passes and is
deliberately permissive about representation:

1. **numeric code**, preferring `status` then `status_raw`
2. **text spelling**, preferring `status_raw` then `status`
3. `"unknown"` — an unrecognised value is **never** upgraded into an alarm

It is supported by two small helpers:

- `_coerce_status_code(raw)` — accepts `int`, whole `float` and digit-only
  `str` (the checked-in fixtures in `tests/fixtures/prtg_payloads.py`
  deliberately carry both numeric and quoted-string forms); rejects `bool`.
- `_status_from_text(value)` — substring match over `_PRTG_STATUS_TEXT`, ordered
  `down → warning → unusual → up` so `"Down (Acknowledged)"` cannot fall through
  to the `up` needle.

### 2.2 Device status now shares the sensor vocabulary

`_derive_device_status(status_text)` was rewritten to delegate to
`_status_from_text`, so the device path and the sensor path **cannot disagree**.
Previously the device path had its own text handling, which is how "Unusual"
could mean one thing on a device and another on its sensors.

---

## 3. Defect 2 — wrong `filter_status` scale for the messages table (fixed)

`collect()` pulls active alarms from PRTG's **messages** table with an explicit
`filter_status` parameter:

```python
_ACTIVE_MESSAGE_STATUS_CODES = "4,5,7,10"   # before
_ACTIVE_MESSAGE_STATUS_CODES = "608,609,610,619,620"  # after
```

This was a **scale confusion, not a typo**. PRTG has two distinct numeric status
scales:

- the **sensor** scale (`3` Up, `4` Warning, `5` Down, `7` Paused…), used for
  sensor objects — this is the scale `_PRTG_STATUS_MAP` above decodes;
- the **log / table** scale, which Paessler documents as the values valid for
  table-based API calls (`/api/table.xml?...content=messages`).

`content=messages` uses the **log** scale. On that scale the alarm states are:

| Log code | Meaning |
|----------|---------|
| 608 | Down |
| 609 | Warning |
| 610 | Unusual |
| 619 | Down (Acknowledged) |
| 620 | Down (Partial) |

(For reference `607` is "Up" and `611` is "OK" on that scale.)

The previous value `"4,5,7,10"` used **sensor** codes. Interpreted on the log
scale those ids mean `Probe Denied (IP)`, `Probe Denied (Key)`, `Web Server` and
`Data` — i.e. the filter was selecting **system/informational noise instead of
active alarms**, while genuine Down/Warning notifications were filtered out.
Vendor-native alerts were therefore both noisy and unreliable.

---

## 4. Hardening — tenant-scoped `SensorRepository.list_for_source` (fixed)

The per-source sensor read was tenant-safe only by *convention*:

```python
GET /sources/{source_id}/sensors  ->  service.get_source(id, tenant_id)  # 404 if foreign
                                  ->  service.sensor_repo.list_for_source(id)  # no tenant filter
```

The route proved ownership and then read rows filtered on
`monitoring_source_id` alone. Because `Sensor` carries its own `tenant_id`,
relying on the *link* being correct made tenant isolation depend on data
integrity rather than on the query.

`list_for_source` gained an optional `tenant_id`:

```python
async def list_for_source(
    self, monitoring_source_id: uuid.UUID, tenant_id: uuid.UUID | None = None
) -> list[Sensor]:
```

and the route now passes it:

```python
return await service.sensor_repo.list_for_source(source_id, tenant_id=tenant_id)
```

Both layers are kept deliberately. The ownership check decides **whether** the
tenant may see the source at all (404 otherwise); the repository filter means a
row whose `monitoring_source_id` were wrong or corrupt still could not leak into
another tenant's per-source read. Callers that already hold a vetted source
(collectors, tests) may omit the argument, mirroring `list_for_tenant()` — the
path the tenant-wide dashboard listing uses.

---

## 5. Audited and found correct (no change)

| Area | Finding |
|------|---------|
| Collection write path | Entirely **upsert-only**. No `DELETE` and no soft-delete anywhere in the sensor/device/metric/event save path; the only deletion is the explicit, tenant-scoped `delete_source`. A partial or failed run cannot silently remove previously collected rows. |
| Route ownership | Every source-scoped route proves ownership via `get_source(id, tenant_id)` → `404`. No source-scoped endpoint trusts a path id. |
| Enrollment | Tenant is taken from the principal (mint) or the token (register); site ownership is validated; token redemption is atomic and single-use. |
| Second PRTG path | None. `PrtgLookupAdapter` is DB-only and tenant-scoped through `list_for_tenant`. (PHASE 5B removed the one true second path, the credential-global `/prtg/root-sensors` passthrough.) |
| Message → device linkage | The `parent`-field fix (matching PRTG's name/host string rather than a numeric objid) is present and correct. |
| Scheduler | Concurrency is bounded; only one scheduler owner runs. |

---

## 6. Findings documented but deferred (not changed)

These are real observations, deliberately **not** fixed in PHASE 5C because each
would be a behaviour/product change rather than a defect correction. They are
recorded so they are not lost.

### 6.1 `Sensor.last_value_at` is written and exposed but never consumed

`BundleSaver` stamps `last_value_at` on every upsert and the API exposes it, but
**nothing reads it to decide staleness** — no API accepts a freshness predicate
and the dashboard does not surface per-sensor freshness. A sensor whose source
has silently stopped collecting therefore still renders as its last known status
with no age indication. Closing this means adding a freshness surface (a
threshold/age field and a UI affordance), which is new product behaviour.

### 6.2 No run-level wall-clock timeout

Timeouts exist only **per HTTP request** (`_STATUS_TIMEOUT = 10s`,
`_TABLE_TIMEOUT = 20s`, `_MAX_ATTEMPTS = 3`), plus bounded per-sensor
concurrency in `_collect_channels`/`_collect_historic`. The scheduler bounds how
many sources run at once but imposes **no per-run cap**, so a PRTG instance that
answers slowly — rather than failing — could hold a run open far longer than the
sum of its nominal timeouts. Closing this means introducing an overall run
budget and deciding the partial-failure semantics, which is a design decision.

---

## 7. Verification

### 7.1 Focused suites

| Suite | Result |
|-------|--------|
| `tests/test_prtg_collector.py` | **46 passed** — includes the new `TestPRTGStatusContract` class (37 collected cases) covering the 1–14 code table, both-column/both-type resolution, text spellings, the "unrecognised is never an alarm" rule, and the message-filter scale |
| `tests/test_monitoring_crud.py` + `tests/test_repositories.py` | 61 passed |
| `tests/test_collection_pipeline.py` | 5 passed |
| `tests/test_tenant_isolation.py` | 56 passed |

### 7.2 Affected-backend regression sweep (13 suites)

`test_prtg_collector`, `test_prtg_provider`, `test_monitoring_crud`,
`test_repositories`, `test_collection_pipeline`, `test_tenant_isolation`,
`test_collector_enrollment`, `test_collector_registration`,
`test_seed_dev_prtg_source`, `test_scheduler_single_owner`,
`test_universal_telemetry_pipeline`, `test_source_rate_limiter`,
`test_alert_normalization`:

```
274 passed, 100 warnings in 168.77s
```

> Note on flakiness: an earlier overlapping run of this sweep reported 2 failures
> in `test_collection_pipeline.py`. Those two tests pass when run alone and pass
> in the clean sequential sweep above; they were failing under **test-database
> contention from concurrent pytest processes** (that run took 402s for two
> tests), not because of any change in this phase.

### 7.3 PHASE 5B baseline preserved

All six protected symbols remain **absent** from the tree:
`_prtg_setting`, `PRTG_BASE_URL`, `PRTG_USERNAME`, `PRTG_PASSWORD`,
`_prtg_login_session`, `PrtgSensorOut`; `listPrtgRootSensors` and the
`/prtg/root-sensors` route remain removed, and
`tests/test_monitoring_crud.py` still asserts the route is not registered.

No frontend file was modified in this phase.

---

## 8. Files changed

| File | Change |
|------|--------|
| `backend/app/collectors/prtg.py` | Corrected `_PRTG_STATUS_MAP` (full 1–14); corrected `_ACTIVE_MESSAGE_STATUS_CODES` to the log scale; rewrote `_normalize_sensor_status` to resolve both columns and both representations; added `_coerce_status_code` and `_status_from_text`; unified `_derive_device_status` onto the shared text vocabulary |
| `backend/app/repositories/sensor_repository.py` | `list_for_source` gained an optional `tenant_id` filter |
| `backend/app/api/routes/monitoring.py` | Per-source sensor route passes `tenant_id`; documents the two-layer guarantee |
| `backend/tests/test_prtg_collector.py` | Added `TestPRTGStatusContract` (37 cases) |

PHASE 5B files carried in the same working tree: `backend/app/schemas/monitoring.py`
(removed the legacy passthrough schema) and `backend/tests/test_monitoring_crud.py`
(protection tests).
