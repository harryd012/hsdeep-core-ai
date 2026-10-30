# Implementation status: cloud SaaS stabilization

Release branch: `release/cloud-saas-stabilization` (cut 2026-10-30 from
`feat/platform-buildout-b1` at `4fe4c8268`; rollback tag
`rollback/pre-release-baseline` points there). Programme order R0 → R5 per the
master execution directive. Batch history before this programme:
`docs/PLATFORM_LEDGER.md`.

Statuses: NOT_STARTED, IN_PROGRESS, BLOCKED, CODE_COMPLETE, TESTING, VERIFIED,
ACCEPTED. VERIFIED means the evidence column was produced on this branch;
ACCEPTED needs the owner's sign-off.

## R0: repository and release stabilization

| ID | Task | Status | Evidence |
| --- | --- | --- | --- |
| R0.1 | Reconcile branches, cut release branch, rollback point | VERIFIED | `origin/main` (`46cc95b85`) and local `main` (`f1757216a`) are ancestors of the release branch: `git merge-base --is-ancestor`. Only file deleted since `origin/main`: `frontend/src/lib/mock/sop-seed.ts` (intentional, fabricated data). `chore/deploy-readiness` is patch-equivalent (`git cherry` shows `-`). `fix/session-rpm-fair-use` (2 commits) merged in `c71376487`; 22 targeted tests pass. See "Branch reconciliation" below. |
| R0.2 | CI/runtime alignment | CODE_COMPLETE | `c32dad8a1`: Node 24 LTS in CI (`.nvmrc`), `frontend/Dockerfile`, `package.json` engines (Node 20 EOL 2026-04-30; Next 16.2.9 supports >=20.9). Python 3.12 pinned (`.python-version`, pyproject, CI, image). Gates now run 121 frontend unit tests and require one alembic head; CI round-trips the newest migration. Remote CI run pending a push. |
| R0.3 | Secret scanner | VERIFIED | `8d8b90929`: scans what git would commit (1,095 files), tests included; value-based placeholder check; unreadable files fatal; 9 high-confidence token formats. `python -m pytest ci/test_secret_scan.py` 14 passed; `ci/secret_scan.py --self-test` passed; repo scan 0 fatal / 43 warnings (all throwaway passwords in tests). |
| R0.4 | Documentation reconciliation | VERIFIED | This file, `RELEASE_BLOCKERS.md`, `SECURITY_GAP_REGISTER.md`, `RELEASE_CHECKLIST.md`, `TEST_EVIDENCE.md`, `ROADMAP.md`. Inventory below. |
| R0.5 | Full gates on the release branch (local + remote CI) | TESTING | Local gates all passed (`TEST_EVIDENCE.md`); remote CI waits on push approval (BLK-R0-01). |

### Branch reconciliation (2026-10-30)

| Branch | Unique commits vs release | Disposition |
| --- | --- | --- |
| `origin/main`, `main` | 0 | Contained. `main` is 65 commits ahead of `origin/main` locally (never pushed). |
| `feat/platform-buildout-b1` | 0 | Release base. |
| `feat/topology-l3`, `feat/topology-meraki`, `feat/ui-page-kit`, `feat/ecc1-command-center` | 0 | Contained. |
| `chore/deploy-readiness` | 1 (patch-equivalent) | Contained. |
| `fix/session-rpm-fair-use`, `fix/api-quota-api-key-only` | 2 / 1 | Merged (`c71376487`). |
| `agents/*`, `master`, `feature/collection-pipeline-stabilization`, `backup/*` | 3-9 each, July-Sept 2026 | Historical; not merged. Review before deleting. |

Working trees: the main checkout (`I:\AI-LAB\hsdeep-core-ai`, branch
`feat/ui-page-kit`) holds about 245 uncommitted user paths (dashboard-v2,
explorer, universal resources, SOP import pipeline, edge agent). They are not
on any branch; they need the owner's commit before they can be released.

### Repository inventory (tracked files, release branch)

| Item | Count |
| --- | --- |
| Backend files | 760 |
| Frontend files | 284 |
| Backend test files | 163 |
| FastAPI route files | 44 |
| Collector files | 24 (18 registered collectors) |
| Service files | 160 |
| Model files | 71 |
| Alembic migrations | 68 (one head: `b5e8c2d4f6a1`) |
| Frontend pages | 49 |
| Frontend components | 118 |
| Frontend unit test files | 15 (121 tests) |

## R1: security and multi-tenant foundation

| ID | Task | Status | Evidence |
| --- | --- | --- | --- |
| R1.1 | Tenant identity: no frontend fallback tenant | VERIFIED | `9e979cbc4`. Backend reads no tenant from the query (0 matches for `tenant_id ... Query(` in routes). `tsc` clean; 121 frontend tests pass. Not yet checked in a signed-in browser session. |
| R1.2 | Scoped, time-bound, audited support access | BLOCKED | Needs an owner decision on the grant model (see `RELEASE_BLOCKERS.md` BLK-R1-02). |
| R1.3 | Cross-tenant isolation suite | NOT_STARTED | |
| R1.4 | Server-side session revocation | VERIFIED | `431b85c85`, `fe7af585f`; migration `c7f1a9d3e5b2` round-tripped; `tests/test_session_revocation.py` 8 passed; auth/email/pilot/security suites 64 passed; full backend suite 2,542 passed + RBAC guard fixed (15 passed). |
| R1.5 | RBAC per-role tests | NOT_STARTED | Write routes already guarded (`test_rbac_write_routes`). Role changes now end the user's sessions (R1.4). |
| R1.6 | Unified security audit | NOT_STARTED | |
| R1.7 | Signup rate limit fails closed | VERIFIED | `e5cc42668`; `tests/test_signup_rate_limit.py` 2 passed; billing/pilot suites pass. |
| R1.8 | API key expiry/last-used as timestamps, enforcement tests | NOT_STARTED | |
| R1.9 | Security configuration review | NOT_STARTED | |

## R2 to R5

NOT_STARTED; plan in `ROADMAP.md`.

## Module status

"Tests" means automated tests on this branch pass. "Live" means exercised
against the real external system. Nothing here has been live-verified against
a customer provider; that is a release blocker for R3.

| # | Module | Status | Evidence / gap |
| --- | --- | --- | --- |
| 1 | Authentication | Implemented, tests | Argon2, JWT access + hashed refresh tokens, lockout, password reset, verification, invites (`test_auth_*`, `test_pilot_safety`, `test_email_foundation`). Gap: access tokens not revocable before expiry (SEC-04). |
| 2 | Tenant management | Implemented, tests | `test_admin_tenant_*`, `test_tenant_isolation`. Gap: support override not time-bound (SEC-02). |
| 3 | MSP customer management | Partial | Tenant-per-customer model only; no provider → customer hierarchy (R4.1). |
| 4 | Enterprise organization | Partial | Sites/zones; no business-unit model (R4.1). |
| 5 | Users and roles | Implemented, tests | Owner/Admin/Operator/Viewer, permission catalog, `test_rbac_write_routes` (every write route guarded). |
| 6 | Subscription and billing | Implemented, tests | Plans, entitlements, Stripe webhook code (`test_billing_hardening`, `test_entitlement_gates`). Not tested against a Stripe account. |
| 7 | Monitoring integrations | Implemented, tests | 18 collectors registered; none live-verified on this branch. |
| 8 | PRTG collector | Implemented, tests | `test_prtg_collector`, `test_prtg_provider`. The dev database has 78 PRTG sources and none has a successful run. |
| 9 | Universal Discovery | Implemented, tests | `test_discovery_*`, `test_universal_*`. |
| 10 | Device and asset management | Implemented, tests | `test_inventory_read_regressions`, `test_device_*`. |
| 11 | Network intelligence | Implemented, tests | `test_network_*`, `test_failover_intelligence_*`. |
| 12 | Alert management | Implemented, tests | `test_alert_normalization`, `test_concurrent_dedup`, `test_actor_attribution`. |
| 13 | Incident management | Implemented, tests | `test_incident_lifecycle`, `test_recovery_check`, `test_incident_notifications`. |
| 14 | Priority Engine | Implemented, not verified | Routes exist; no dedicated test file found. |
| 15 | Topology Engine | Implemented, tests | `test_topology*` (L2 tree, L3, coverage). Links only from evidence. |
| 16 | RCA Engine | Implemented, tests | `test_rca_*`. Explainability per R3.8 not yet checked. |
| 17 | AI Copilot | Implemented, tests | `test_ollama_*`, `test_ai_*`. Needs a reachable Ollama. |
| 18 | SOP Engine | Implemented, tests | `test_rca_recommendation_sop`; UI shows only real SOPs since `4fe4c8268`. No SOP CRUD test file. |
| 19 | Runbook Intelligence | Implemented, not verified | Routes exist; no dedicated tests. |
| 20 | Workflow Automation | Implemented, tests | `test_automation_runner`, `test_recovery_validator`, `test_task_*`. Autonomous orchestrator not started in any runtime mode (owner decision). |
| 21 | Device Action Plane | Implemented, tests | `test_device_action_plane`, `test_action_claim_concurrency`. |
| 22 | ServiceNow | Implemented, not verified | No tests; no live instance. |
| 23 | ITSM / tickets | Implemented, tests | `test_ticket_pipeline_race`. |
| 24 | SLA management | Partial | `test_operational_sla_policy_crud`; calculations per R4.7 not defined. |
| 25 | Command Center | Implemented, tests | `test_command_center_brief`. |
| 26 | Executive Dashboard | Implemented | API-backed; coverage row added `4fe4c8268`; verified in a browser on demo data. |
| 27 | Operations Dashboard | Implemented | `/noc` pages; not reviewed in this programme yet. |
| 28 | Network Dashboard | Implemented | Not reviewed yet. |
| 29 | Infrastructure Explorer | Partial | Committed pages exist; the newer explorer is uncommitted user work. |
| 30 | API metering | Implemented, tests | `test_metering`, `test_api_quota`, `test_session_rate_cap`. |
| 31 | Audit logging | Partial | AuditService + auth events; no unified security audit model (SEC-06). |
| 32 | Reporting | Partial | Report pages exist; definitions per R4.7 missing. |
| 33 | Cloud deployment | Partial | `render.yaml` blueprint; not deployed. |
| 34 | Worker and scheduler | Implemented, tests | `test_runtime_mode`, `test_scheduler_single_owner`. Event bus is in-process (BLK-R2-01). |
| 35 | Health and readiness | Implemented, tests | `test_health_readiness`, `test_readiness_probe`, `test_metrics_endpoint`. |
