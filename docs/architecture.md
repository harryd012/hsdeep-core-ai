# HSDEEP CORE AI — Architecture & Module Status

## Backend layout (`backend/app/`)

This is the one real, production-wired backend (built by `Dockerfile`,
served via `uvicorn app.main:app`). Clean Architecture layering:

```
app/
  models/        SQLAlchemy 2 ORM models (persistence)
  schemas/        Pydantic v2 request/response contracts (API layer)
  repositories/   All direct DB queries, one class per aggregate (data access)
  services/       Business logic, orchestrates repositories + external adapters
  api/routes/     FastAPI routers - thin, delegate to services only
  core/           config, db session, logging, redis
alembic/           migrations, one file per schema change
```

> ⚠️ There is a second, older prototype at `backend/main.py`, `backend/config.py`,
> `backend/services/*`, `backend/utils/system_monitor.py`, and
> `backend/app/api/{alerts,orchestration,intelligence}.py`. It is in-memory only
> (not DB-backed) and not wired into `app.main`. Per project decision, its
> logic will be ported into the `app/` architecture module-by-module as each
> corresponding real module (Alert Engine, RCA Engine, SOP Engine, Ticket
> Engine) is built, and the old files removed at that point - not before.

## Module 1: Monitoring Engine — ✅ Complete

**Tables added** (migration `d12b0f4785e7_add_monitoring_sources_and_sensors`):
- `monitoring_sources` — a configured connection to an external monitoring
  platform (tenant-scoped, optionally site-scoped)
- `sensors` — normalized metric/check readings pulled from a source,
  optionally linked to a `Device` once identity resolution matches them

**Extensibility mechanism:** `app/collectors/base.py` defines `BaseCollector`
(ABC) + `CollectorRegistry` (the Collector Framework's evolution of the
former `MonitoringAdapter`/`AdapterRegistry` pattern, retired in task 10.3).
Every vendor integration is a class that implements `authenticate()`,
`collect()`, and `normalize()`, and self-registers via
`@CollectorRegistry.register("<source_type>")`. The service layer
(`monitoring_service.py`) only ever calls `CollectorRegistry.get(...)` —
**adding a new monitoring source means adding one new collector file, not
touching the model, repository, service, or API.**

**Collectors implemented now:**
| source_type | Status | Notes |
|---|---|---|
| `prtg` | ✅ Real | Uses PRTG's documented `/api/table.json` HTTP API |
| `meraki`, `fortinet`, `cisco`, `azure`, `vmware`, `windows`, `linux`, `docker`, `kubernetes` | 🟡 Not yet implemented | Will be registered under `app/collectors/` as each vendor collector is built (see tasks 11.x) |

**API** (`/api/monitoring/*`):
- `GET /source-types` — list all registered collectors + implemented flag
- `POST /sources`, `GET /sources`, `GET /sources/{id}`, `PATCH /sources/{id}`, `DELETE /sources/{id}`
- `POST /sources/{id}/test-connection`
- `POST /sources/{id}/sync` — pulls readings via the collector, upserts `sensors`, records `status`/`last_error`/`last_sync_at` on the source (never leaves it in an unknown state, even on failure)
- `GET /sources/{id}/sensors`, `GET /sensors` (tenant-wide, filterable by site/device/status/metric_type)

**Verified:** migration applied against a real local Postgres instance via
Alembic autogenerate (zero manual diff needed against the models — schema and
code agree exactly); full create → list → test-connection → sync flow
exercised end-to-end against the running app, including the unreachable-host
error path and the invalid-`source_type` rejection path.

**Known gap carried forward:** there is no authenticated principal yet, so
`tenant_id` is a required parameter on every endpoint instead of being
derived from a session. Every endpoint has a `NOTE(auth)` comment marking
this. Should be resolved when an Auth module is built.

## Remaining modules (unchanged from initial scan)
2. Alert Engine — next recommended
3. Correlation Engine
4. Priority Engine
5. RCA Engine
6. SOP Engine
7. Ticket Engine
8. AI Copilot
9. Dashboard APIs
10. ServiceNow Integration

Frontend (`frontend/src/components/*.tsx`) is still fully static/mocked and
not wired to any backend endpoint — first real wiring point will be the
Alert Engine + this Monitoring Engine once both exist.
