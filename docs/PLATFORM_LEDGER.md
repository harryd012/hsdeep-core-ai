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

## Batch 3: RBAC on every mutating route

Outcome: every POST/PUT/PATCH/DELETE route under `/api` requires a catalog
permission, except 9 that authenticate another way (login/refresh/logout,
signup, Stripe and ServiceNow webhooks with signatures, collector
registration with an enrollment token, ingest with a collector credential).
Approvals and rejections are attributed to the authenticated user.

Found: 89 write routes checked only the tenant. Any signed-in user of a
tenant, including Viewer, could resolve/close incidents, delete monitoring
sources, approve runbook and workflow executions, approve SOP versions,
configure ServiceNow, and the approver id was taken from the request body.

Files:
- `backend/app/core/permissions.py`: 11 new codes for the newly guarded
  routes (`sops.approve`, `runbooks.write`, `runbooks.approve`,
  `workflows.write`, `workflows.approve`, `integrations.write`, `noc.write`,
  `topology.write`, `tasks.write`, `agents.write`, `changes.write`) plus 5
  codes that 46 existing routes already required but that were missing from
  the catalog, so no role could hold them (`tickets.write`,
  `tickets.execute`, `itsm.write`, `billing.write`, `billing.read`): until
  now only superusers could create tickets, view or change the
  subscription, see onboarding status or configure ITSM. Operator also gets
  `noc.write`, `tasks.write`, `changes.write`, `tickets.write`,
  `tickets.execute`; Viewer gets `billing.read`. Approval codes are
  Owner/Admin only.
- `backend/alembic/versions/c4e8a1f3d7b2_add_write_and_approval_permissions.py`:
  seeds the codes and backfills existing tenants' Owner/Admin/Operator roles
  (same pattern as `b7d2e4f6a8c1`); downgrade removes grants then codes.
- 21 route modules under `backend/app/api/routes/`: a
  `Depends(require_permission(...))` on each guarded route's decorator.
- `backend/app/core/auth.py`: `get_acting_user_id` (403 for API keys, which
  have no user identity); runbook, workflow and SOP approve/reject handlers
  use it. The body `approved_by`/`rejected_by` fields are still accepted and
  ignored, so existing clients do not break.
- `backend/tests/test_rbac_write_routes.py`: a route-table guard (no write
  route without a permission, every code in the catalog, Viewer reaches only
  the two read-only POSTs), HTTP 403-then-pass checks, approver attribution,
  API key approval refusal.

Behaviour change: Operator users can no longer approve runbook/workflow
executions or SOP/workflow versions (segregation of duties); an Owner or
Admin approves. Read-only POSTs (`/api/copilot/chat` needs
`incidents.read`, `/api/network-intelligence/evaluate` needs `devices.read`).

## Batch 4: incident recovery check

Outcome: `GET /api/incidents/{id}/recovery-check` answers "did it recover,
and what is the evidence?" from persisted telemetry. Member alerts must be
resolved; the alerts' sensors and the affected devices must report up with
an observation newer than `since` (default: the incident's last alert).
Stale or missing telemetry is `inconclusive`, never recovered. Read-only.
The Incidents page runs the check before "Recover" and asks the operator to
confirm, listing the failing and unverified checks, when it does not pass.

Found: the automated lifecycle publishes `VALIDATION_STARTED` after an SOP
completes, but nothing consumes it or ever publishes `VALIDATION_PASSED`,
and `_on_sop_completed` starts validation even when the SOP failed. The
orchestrator is deliberately not started in any runtime mode
(`app/main.py` `_start_rca_consumers`), so this does not close incidents
today; it must be fixed before automated remediation is enabled.

Files: `backend/app/services/recovery_check.py`,
`backend/app/schemas/incident.py` (`RecoveryCheckOut`),
`backend/app/api/routes/incidents.py`, `backend/tests/test_recovery_check.py`
(6 tests), `frontend/src/lib/incidents/recovery.ts`,
`frontend/src/lib/api.ts` (`getIncidentRecoveryCheck`),
`frontend/src/app/(dashboard)/incidents/page.tsx`,
`frontend/tests/incidents/recovery.test.ts` (3 tests).

## Handoff

Branch `feat/platform-buildout-b1` (worktree `quirky-nightingale-9ab069`).

Remaining blockers: items 2, 3 and 5 of the top five (uncommitted main
checkout work and the edge agent; no reachable PRTG or credentials).

Next task, in order:
1. Consume `VALIDATION_STARTED` with `check_incident_recovery` (poll until
   recovered or a timeout, with a stability window), publish
   `VALIDATION_PASSED`/`VALIDATION_FAILED` with the checks as evidence, and
   skip validation when `SOP_COMPLETED.success` is false.
2. `POST /api/alerts/{id}/acknowledge` and incident acknowledge still take
   the actor id from the body (the UI sends the nil UUID); attribute them to
   the principal like approvals.
3. Read routes: GETs are tenant-scoped but most check no `.read` code;
   decide whether Viewer-only scoping is enough or guard them too.
4. Staleness in health scores: an "up" sensor with an old `last_value_at`
   still scores 100 (the recovery check already treats it as stale).

## Batch 5: pilot safety

Outcome: an invite-only pilot can run on Render without public sign-up or
password guessing.

- `PUBLIC_SIGNUP_ENABLED` (default true; `render.yaml` sets false):
  `POST /api/billing/signup` answers 403 when off; the sign-up page explains
  that access is by invitation.
- Per-account lockout: `LOGIN_MAX_FAILED_ATTEMPTS` (5) consecutive wrong
  passwords lock the account for `LOGIN_LOCKOUT_MINUTES` (15); stored on
  `users.failed_login_count` / `users.locked_until` (migration
  `e3a7c5b9d1f4`), so it holds across restarts and API instances. A locked
  account gets the same generic 401 even with the right password; a success
  resets the counter; an admin password reset unlocks. The login route now
  commits on every 401 so the counter persists. Trade-off: anyone who knows an
  email can lock that account for 15 minutes (the per-IP limit of 5/min slows
  this).
- `render.yaml`: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (`sync: false`;
  empty keeps billing off with 501s).
- `backend/tests/test_pilot_safety.py`: 6 tests.
- Email foundation design: `docs/architecture/email-foundation.md`
  (awaiting approval).

## CI: second run failure (run 40964164635)

The gates step passed type-check, lint, build and the focused suites, then
the full backend suite failed (exit 1). Reproduced locally against a fresh
database migrated the way CI does it: 12 failed + 5 errors, all in
`test_agent_api.py`, `test_task_api.py` and `test_incident_lifecycle.py`.
They address a fixed tenant (`54dd0ca8-...`) that existed only in long-lived
local test databases. Fix: a `dev_tenant` fixture in `tests/conftest.py`
creates it with an active subscription when missing; the three suites use it.

CI result: run 40967439726 on `chore/deploy-readiness` (`ebad688d0`)
passed every gate, including both Docker image builds.

## Batch 6: acknowledgements attributed to the signed-in user

Found: `POST /api/alerts/{id}/acknowledge` required a client-supplied
`acknowledged_by`; the Incidents page acknowledged incidents with the nil
UUID, which violates the `users` foreign key on `incidents.acknowledged_by`.

Fix: `get_actor_user_id` (`app/core/auth.py`) gives the signed-in user, or
None for an API key (no user identity, never an invented id). Alert and
incident acknowledge use it; the body field is accepted and ignored. The
frontend `acknowledgeAlert` / `acknowledgeIncident` send no actor; the
Investigation workspace note is corrected. Tests:
`backend/tests/test_actor_attribution.py` (3).

## Batch 8: only fresh evidence counts in health scores

A device's own status counts only when `last_seen_at` is within
`DEVICE_FRESHNESS_WINDOW_SECONDS` (24 h, the canonical classification
window shared with the frontend), and a sensor's only when `last_value_at`
is. Stale or never-seen "up" no longer scores 100. Test added in
`backend/tests/test_health_score.py`.

## Batch 7: automated remediation is validated from fresh telemetry

`app/services/events/recovery_validator.py` consumes `VALIDATION_STARTED`
(previously consumed by nothing) and runs `check_incident_recovery` with
`since` = the moment validation started, so only post-remediation telemetry
counts. `VALIDATION_PASSED` needs `RECOVERY_VALIDATION_CONSECUTIVE_PASSES`
(2) recovered checks in a row, re-checked every
`RECOVERY_VALIDATION_INTERVAL_SECONDS` (60) up to
`RECOVERY_VALIDATION_ATTEMPTS` (10); a failing check or running out of
attempts publishes `VALIDATION_FAILED` with the checks as evidence, which
escalates to an engineer and never resolves the incident. Each validation runs
in its own task (the bus awaits every handler of an event). The
`AutonomousLifecycleOrchestrator` registers it; a failed SOP
(`SOP_COMPLETED.success == false`) now goes straight to `VALIDATION_FAILED`
instead of starting validation. Limitation: in-flight validations live in
the process, so a restart leaves the incident open (never wrongly resolved).
The orchestrator itself is still not started in any runtime mode.
Tests: `backend/tests/test_recovery_validator.py` (5).

Test fixture note: `dev_tenant` now also creates a real dev user
(`DEV_USER_ID`), because acknowledgements are attributed to the token's user
and `*_by` columns reference `users.id`.

## Batch 9: email foundation (password reset, verification, invitations)

Design: `docs/architecture/email-foundation.md` (approved; build-time
differences listed there).

- `app/services/email/`: SMTP / log / none transports; `email_outbox` table,
  enqueued in the request transaction and delivered by the worker job
  `email_outbox_delivery` every 30 s (`FOR UPDATE SKIP LOCKED`, backoff 1/5/15/60
  min, `failed` after 5 attempts with the last error kept).
- `account_tokens`: SHA-256 hashed, single use, reset 30 min / verify 24 h /
  invite 7 days; a new token retires the user's earlier unused ones.
- API (public, per-IP rate limited): `POST /api/auth/password/forgot` (same
  202 for any email; max 3 per account per hour), `/password/reset` (also
  revokes refresh tokens and clears lockout), `/email/verify`,
  `/invite/accept`. `POST /api/auth/users` accepts `send_invite` instead of a
  password. Unverified self-sign-ups get 403 on sign-in.
- Audit events: `auth.password_reset_requested`, `auth.password_reset`,
  `auth.email_verified`, `auth.invite_sent`, `auth.invite_accepted`.
- Migration `f6c2d8a4b7e9` (existing users backfilled as verified; round trip
  verified). Production refuses `EMAIL_TRANSPORT=log`; `render.yaml` ships
  `none` plus `SMTP_*`, `EMAIL_FROM`, `APP_PUBLIC_URL`.
- Frontend: `/forgot-password`, `/reset-password`, `/verify-email`,
  `/accept-invite` (shared `AccountCard` / `SetPasswordForm`), "Forgot
  password?" on `/login`, server message shown for 403 on sign-in.
- Tests: `backend/tests/test_email_foundation.py` (9).

## Batch 10: incident notifications (email, Slack, PagerDuty)

The per-tenant notification settings were stored but nothing ever sent.

- `app/services/notifications/dispatcher.py`, run by the worker job
  `incident_notifications` every 30 s (not the in-process event bus, so
  incidents created by any process are covered). Incidents opened at or above
  the channel's minimum severity (warning for email/Slack, PagerDuty's
  `pagerduty_severity`, default warning) notify once; "resolved" goes only to
  channels that were told about the opening. PagerDuty uses Events API v2
  with `dedup_key` = incident id, so the resolve closes the page it opened.
  Email goes through the outbox (batch 9).
- `incident_notifications` (migration `a1d4f7b2c9e3`, round trip verified):
  unique per (incident, channel, event) for idempotency; failures keep their
  error and retry up to 3 times. Enabling a channel never floods history:
  only incidents since the settings row was created and within 24 h.
- Security fixes found on the way: `GET /api/settings/notifications`
  returned the Slack webhook URL and PagerDuty key in full to any tenant
  user; both are now masked (`••••` + last 4) and an unchanged masked value
  keeps the stored secret on save. The Slack URL accepted any http(s)
  address, which with delivery would let a tenant make the platform call
  internal hosts (SSRF); it must now be `https://hooks.slack.com/...`, and
  the dispatcher re-checks stored values before sending.
- `GET /api/settings/notifications/deliveries` (tenant-scoped log) and
  `POST /api/settings/notifications/test` (admin; PagerDuty test triggers and
  immediately resolves). The settings page shows the log and a test button.
- Tests: `backend/tests/test_incident_notifications.py` (7).

Not verified against the real services: Slack, PagerDuty and SMTP calls are
exercised with fakes; a live check needs real webhook/integration keys.

## Next

1. Edge agent: commit and ship the uncommitted `backend/agent/hsdeep_edge_agent`
   (owner of the main checkout), required for customer-site collection.
2. Decide whether to start the autonomous orchestrator in the worker runtime
   now that validation exists (material decision; off by default).
3. Before public sign-up: CAPTCHA or equivalent abuse protection.
4. Encrypt notification secrets at rest (Slack URL, PagerDuty key) with the
   existing SECRET_ENCRYPTION_KEY, like collector credentials.
