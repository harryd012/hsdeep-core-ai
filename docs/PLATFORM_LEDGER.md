# Platform build-out ledger

Running record of the enterprise build-out programme (audit, blockers,
batches). Each entry cites files and test evidence; anything not verified is
marked `STATUS = Needs Verification`.

## Phase 0 audit (2026-10-30)

Branch: `feat/platform-buildout-b1`, cut from `chore/deploy-readiness`
(`11d8b0692`). Worked in the worktree; the main checkout
(`I:\AI-LAB\hsdeep-core-ai`, branch `feat/ui-page-kit`) holds 244 uncommitted
or untracked paths owned by another workstream and was not touched.

What exists (verified from code):

| Area | Evidence | State |
|---|---|---|
| Auth, principal-derived tenancy | `app/core/auth.py` (`get_current_user`, `get_tenant_id`, `require_permission`). A route walk over the app (389 API routes) finds 18 without a user principal: health/readiness probes, `/metrics`, `/ai-cto`, login/refresh/logout, public plan list and signup, the Stripe webhook (signature), the ServiceNow inbound webhook (HMAC), collector registration (enrollment token) and `/api/v1/ingest/*` (collector credential). No route takes `tenant_id` from the query string. | Implemented |
| Collectors | `app/collectors/` (PRTG, Meraki, FortiGate, Palo Alto, SNMP, Linux SSH, ...), `NormalizedBundle` -> `BundleSaver` | Implemented; live PRTG ingest `STATUS = Needs Verification` (needs a reachable PRTG and credentials) |
| Incident lifecycle | `app/services/incident_lifecycle_service.py`: validated transitions open -> ... -> validating -> recovered -> closed, plus exception states | Implemented |
| Action verification | `app/actions/verification.py`, `device_action_service.py`: a provider result alone never marks an action verified | Implemented for read-only diagnostics |
| Closure loop | `app/services/closure_service.py`: TICKET_RESOLVED -> delayed TICKET_CLOSED | Implemented |
| Topology L2/L3 | `topology_*_service.py`, `/api/infrastructure/topology/l3` | Implemented (feat/topology-l3) |
| Deploy | `render.yaml`, `deployment/RENDER.md` | Implemented, not yet deployed |

### Top five blockers

1. **Missing data shown as healthy, fabricated counts** (acceptance F).
   `app/services/health_score.py` returned 100 for an empty tenant, a site with
   no devices and a device with no sensors; its sensor query ran `avg()` over a
   VARCHAR column (fails on PostgreSQL once a sensor exists);
   `GET /api/assets/tenants/{id}/health` reported devices as `sensors_count`
   and a hard-coded `alerts_active = 0`; the Infrastructure Topology page
   hard-coded every site and the tenant root as "healthy". **Fixed in batch 1.**
2. **Uncommitted work in the main checkout** (244 paths on
   `feat/ui-page-kit`, including the dashboard type fix and the edge agent
   `backend/agent/hsdeep_edge_agent`). Until committed, `next build` needs
   `ignoreBuildErrors` and the CI type-check fails on 2 dashboard errors.
   Blocked on the owner of that work.
3. **Customer-site collection from a hosted backend.** A cloud API cannot
   poll private PRTG/SNMP/SSH addresses; the edge agent that solves this is in
   blocker 2. Acceptance A in a hosted deployment depends on it.
4. **Unauthenticated `/metrics` and `/ai-cto` in production.** Counters are
   global (no tenant data, see `app/core/metrics.py`) but expose platform
   internals publicly once deployed. Next batch candidate.
5. **No reachable PRTG / production integrations in this environment.**
   Acceptance A-C end-to-end needs a PRTG instance and credentials supplied by
   the user; code paths are covered by fixture-based tests only.

## Batch 1: honest health scores

Outcome: a health score is `null`/"unknown" when nothing in scope has a known
status, never 100; the tenant summary reports real sensor and active alert
counts; a foreign business service answers 404.

Files:
- `backend/app/services/health_score.py`: rewritten scoring (unknown -> no
  score; one device query plus one sensor query per scope instead of N+1;
  tenant-scoped sensor lookup; `HealthScopeNotFound`; `HealthRollup` with
  scored/total coverage).
- `backend/app/api/routes/assets.py`, `backend/app/schemas/asset.py`:
  `health_score` nullable, `status` and `devices_scored` added (additive);
  real `sensors_count` and `alerts_active`; 404 for a missing service.
- `frontend/src/lib/api.ts`, `organization/page.tsx`,
  `infrastructure-topology/page.tsx`: render "—" / "Health unknown" for null;
  no hard-coded "healthy" for sites or the tenant root.
- `backend/tests/test_health_score.py`: 10 tests.

API compatibility: `health_score` can now be `null` (previously a fabricated
100); new fields are additive. The only frontend consumers are updated.

Evidence:
- Old code, run against the test PostgreSQL: an empty tenant scored 100, and
  the sensor query raised `function avg(text) does not exist`, so the tenant
  health endpoint failed for any tenant with devices on a site.
- `tests/test_health_score.py`: 10 passed.

## Batch 2: /metrics not public in production

Outcome: `/metrics` requires `Authorization: Bearer <METRICS_BEARER_TOKEN>`
when the token is set (constant-time compare), answers 404 in production when
it is not, and stays open in development.

Files: `backend/app/core/config.py` (`metrics_bearer_token`),
`backend/app/main.py`, `render.yaml` (generated token),
`deployment/RENDER.md`, `backend/tests/test_metrics_endpoint.py` (4 tests).

`/ai-cto` was left public: it returns the same up/down dependency states as
the public `/health` probe and no tenant data.
