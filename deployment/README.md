# HSDEEP CORE AI - Production Deployment (Phase 9A)

This document is the deployment contract for the **already-built** application.
Phase 9A establishes a cloud-deployable foundation; it does **not** deploy to a
cloud provider and does **not** include Phase 9B work (see the end of this file).

## Target architecture

```
Browser
  -> HTTPS / WAF / Load Balancer        (Phase 9B: cloud edge - see OBSERVABILITY.md)
  -> Next.js frontend (container)       frontend/Dockerfile

API deployment              Worker deployment              Scheduler deployment
  FastAPI (RUNTIME_MODE=api)   FastAPI (RUNTIME_MODE=worker)   FastAPI (RUNTIME_MODE=scheduler)
  PostgreSQL                   PostgreSQL + Redis              PostgreSQL + Redis
  Redis                        EventBus / RCA consumers       monitoring_collection_tick
                                                              task_dispatch_tick
```

All three roles run the **same image**; only `RUNTIME_MODE` differs. Every role
still serves HTTP (so `/health/live` and `/health/ready` always answer) - the
role decides which *background* work starts.

## Runtime roles

| `RUNTIME_MODE` | HTTP | Scheduler jobs | RCA / EventBus consumers |
| --- | :-: | :-: | :-: |
| `all` (default) | yes | yes | yes |
| `api` | yes | **no** | **no** |
| `worker` | yes | yes | yes |
| `scheduler` | yes | yes | no |

`all` preserves pre-9A local development exactly, so nothing changes on a dev box.

### Why the API role never starts the scheduler

Two independent guards apply:

1. **Role gate** - `api` never calls `start_scheduler()`, so N API replicas
   cannot each run a tick.
2. **Single-owner lock** (pre-existing, unchanged) - `app/scheduler/jobs.py`
   wraps every tick in `pg_try_advisory_lock(TICK_LOCK_KEY)`; a replica that
   cannot take the lock logs at debug and returns without doing any work.
   Proven by `tests/test_scheduler_single_owner.py`.

## Commands

Build (repository root):

```bash
docker build -t hsdeep-core-ai-backend:9a backend
docker build -t hsdeep-core-ai-frontend:9a frontend \
  --build-arg NEXT_PUBLIC_API_URL=https://api.example.com
```

Migrate once per release (as a separate job, not in every replica):

```bash
docker run --rm --env-file deployment/.env.production hsdeep-core-ai-backend:9a \
  alembic upgrade head
```

Run the three roles:

```bash
# API (behind the load balancer / WAF)
docker run -d --name hsdeep-api --env-file deployment/.env.production \
  -e RUNTIME_MODE=api -p 8000:8000 hsdeep-core-ai-backend:9a

# Worker (EventBus + RCA consumers + scheduler ticks)
docker run -d --name hsdeep-worker --env-file deployment/.env.production \
  -e RUNTIME_MODE=worker hsdeep-core-ai-backend:9a

# Scheduler-only (if you prefer ticks separated from consumers)
docker run -d --name hsdeep-scheduler --env-file deployment/.env.production \
  -e RUNTIME_MODE=scheduler hsdeep-core-ai-backend:9a

# Frontend
docker run -d --name hsdeep-web -p 3000:3000 hsdeep-core-ai-frontend:9a
```

Without containers (identical semantics - the role is just an env var):

```bash
RUNTIME_MODE=api       uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
RUNTIME_MODE=worker    uvicorn app.main:app --host 0.0.0.0 --port 8001 --no-access-log
RUNTIME_MODE=scheduler uvicorn app.main:app --host 0.0.0.0 --port 8002 --no-access-log
```

## Required production configuration

Copy `deployment/.env.production.example` and inject real values from a secret
manager. With `APP_ENV=production`, startup **fails closed** with an explicit
error for every unsafe value (`Settings.validate_for_production` plus the
startup CORS check in `app/main.py`):

| Variable | Required production value |
| --- | --- |
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `FORCE_HTTPS` | `true` |
| `CORS_ORIGINS` | explicit comma-separated allow-list (**never** `*`) |
| `JWT_SECRET_KEY` | non-placeholder, **distinct** from `SECRET_ENCRYPTION_KEY` |
| `SECRET_ENCRYPTION_KEY` | non-placeholder Fernet key |
| `DATABASE_URL` | managed PostgreSQL URL (no localhost / default creds) |
| `LOG_LEVEL` | explicit (e.g. `INFO`) |
| `LOG_FORMAT` | `json` (recommended for shipping) or `text` |
| `RUNTIME_MODE` | `api` / `worker` / `scheduler` |
| `PRTG_DEV_VERIFY_SSL` | `true` (when PRTG collectors are enabled) |

Never commit a filled-in env file, and never bake credentials into an image
layer - the image contains no `.env` and no secrets. `ci/secret_scan.py` fails
CI on credential material in shipping paths.

## Health and readiness

| Endpoint | Purpose | Behaviour |
| --- | --- | --- |
| `GET /health/live` | liveness (restart decision) | always 200 while the process serves; consults **no** dependency |
| `GET /health/ready` | readiness (traffic routing) | 200 only when PostgreSQL - and Redis when `READINESS_REQUIRE_REDIS=true` - is reachable; **503** otherwise |
| `GET /ready` | legacy alias | identical to `/health/ready` |
| `GET /health` | informational (frontend status) | 200 with dependency detail; contract unchanged since Phase 8 |
| `GET /ai-cto` | control-plane detail | 200, informational |

Point orchestrator probes at `/health/live` (liveness) and `/health/ready`
(readiness) - never at `/health`.

These probe paths are exempt from `FORCE_HTTPS` (`HTTPS_EXEMPT_PATHS` in
`app/core/request_logging.py`): container health checks and platform probes
call the process over plain HTTP, and refusing them would mark every replica
unhealthy. Every other plaintext request still gets a 400.

## Hosting on Render

`render.yaml` at the repository root is a ready Blueprint (API, worker, web,
PostgreSQL, Redis). Go-live checklist: [`RENDER.md`](RENDER.md). Managed
Postgres URLs (`postgres://` / `postgresql://`) are rewritten to the asyncpg
driver automatically (`normalize_database_url` in `app/core/config.py`).

## Known Phase 9B prerequisites

1. **Cloud WAF / API gateway** for edge protection (TLS termination, DDoS and
   bot mitigation). The in-app limiter is per-process burst throttling plus the
   per-tenant period quota - it is not edge protection.
2. **Cross-process event transport.** `EventBus` is in-process by design
   (`app/services/events/event_bus.py`). The worker role owns the RCA consumers,
   and the **scheduler/worker** is what creates incidents from monitoring data,
   so the automated chain still runs end-to-end there. Incidents created *by an
   API replica* publish to that replica's in-process bus, so moving RCA fully
   behind the API/worker split needs a shared transport (Redis Streams/pubsub).
   That is a deliberate Phase 9B item - not silently assumed here.
3. Centralized log shipping and alerting (see `OBSERVABILITY.md`).
4. Backup/PITR/RPO/RTO sign-off and one restore-verification drill
   (see `BACKUP_RESTORE.md`).
5. CI runtime services for the gates in `.github/workflows/ci.yml`
   (PostgreSQL + Redis; the browser suite needs a local dev stack).
