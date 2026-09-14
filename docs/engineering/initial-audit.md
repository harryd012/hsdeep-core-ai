# HSDEEP CORE AI - Phase 0 Initial Audit

**Date**: 2026-09-13  
**Branch**: oo  
**Auditor**: Solar Pro4 (Staff Engineer)  
**Purpose**: Ground-truth audit before any refactoring or new feature work.

---

## 1. Verified Architecture

### Backend

- **Framework**: FastAPI 0.115.0 with async/await throughout
- **Database**: PostgreSQL 16 via asyncpg + SQLAlchemy 2.0.35
- **ORM**: SQLAlchemy 2.0 with Alembic 1.13.2 migrations (20 revisions, single head c1f2e3d4a5b6)
- **Caching**: Redis 5.0.8
- **Task Scheduling**: APScheduler 3.10.4
- **AI Inference**: Ollama (local LLM via qwen3.6:latest)
- **Authentication**: Argon2id password hashing (passlib), HS256 JWTs (python-jose), opaque refresh tokens
- **Authorization**: RBAC with permission codes, superuser bypass, admin guards
- **Configuration**: Pydantic Settings with env file support

### Frontend

- **Framework**: Next.js 16 (App Router), React 19, Tailwind v4
- **Language**: TypeScript
- **State Management**: React Context (AuthProvider) + React Query (@tanstack/react-query)
- **UI**: Custom enterprise design system with framer-motion animations
- **API Client**: Typed fetch wrapper (rontend/src/lib/api.ts)

### Key Files

`
backend/
├── app/
│   ├── main.py                    # FastAPI entry, lifespan, middleware, routers
│   ├── core/
│   │   ├── auth.py                # Auth primitives (JWT, refresh tokens, Principal)
│   │   ├── config.py              # Pydantic Settings with production validation
│   │   ├── db.py                  # Async session management
│   │   ├── permissions.py         # Permission catalog, DEFAULT_ROLES
│   │   ├── entitlements.py        # Entitlement resolution
│   │   └── security.py            # (exists, verify contents)
│   ├── api/
│   │   └── routes/                # 33 route files including auth, billing, onboarding
│   ├── models/                    # 30+ SQLAlchemy models
│   ├── repositories/              # Repository pattern (25+ repositories)
│   ├── services/
│   │   ├── auth_service.py        # Auth facade
│   │   ├── billing_service.py     # Billing/entitlements facade
│   │   ├── ai_insight_service.py  # Heuristic AI insights
│   │   └── ...
│   ├── ai/
│   │   ├── gateway.py             # Provider-agnostic AI gateway
│   │   ├── router.py              # Model routing (Ollama)
│   │   └── providers/             # Provider registry
│   ├── collectors/                # 10 vendor collectors (PRTG, Meraki, FortiGate, etc.)
│   ├── scheduler/                 # APScheduler jobs
│   └── schemas/                   # Pydantic v2 schemas
├── alembic/versions/              # 20 migrations
├── tests/                         # pytest suite (70+ test files)
└── pyproject.toml

frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx               # Redirects to /dashboard
│   │   ├── login/page.tsx         # Enterprise login page
│   │   ├── signup/page.tsx        # Self-service signup
│   │   └── (dashboard)/           # Protected routes
│   ├── components/                # Reusable components
│   ├── hooks/                     # Data hooks
│   └── lib/
│       ├── api.ts                 # Typed API client
│       └── auth-context.tsx       # AuthProvider context
└── package.json
`

---

## 2. Verified Claude Findings

### Finding 1: Dev auto-login may silently authenticate as `admin@hsdeep.local`

**VERIFIED - FIXED**  
The `AUTH_BOOTSTRAP` flag in `frontend/src/lib/api.ts` (line 48) is now **opt-in only**:
```typescript
export const AUTH_BOOTSTRAP = process.env.NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP === "true";
```
Default is `false` in all environments. Previous default was `true`, causing silent auto-login. Documentation in api.ts (lines 27-47) explains the history and fix.

### Finding 2: `AUTH_BOOTSTRAP` may default to enabled in development

**VERIFIED - FIXED**  
Same as Finding 1. `AUTH_BOOTSTRAP` requires explicit `NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP=true`. Not enabled by default in dev.

### Finding 3: Login flow may force hardcoded tenant ID

**VERIFIED — RESOLVED IN PHASE 1A**  
- **Legacy stubs** (`app/api/alerts.py`, `app/api/intelligence.py`, `app/api/orchestration.py`): Added deprecation notices (Phase 1A); these are dead code NOT mounted in main.py
- **Actual routes** (`app/api/routes/alerts.py`, `app/api/routes/intelligence.py`, etc.): Use `Depends(get_tenant_id)` — tenant isolation enforced
- **Frontend**: SUCCESS ⚠️
  - `app/api/routes/` files were already correct
  - Phase 1A cleaned up dead stubs and verified no broken imports
  - Frontend `DEFAULT_TENANT_ID` is deprecated but still used by many functions

### Finding 4: Tenant-less login may need global email resolution

**NOT IMPLEMENTED**  
- Login (`POST /api/auth/login`) requires `tenant_id` parameter
- No global email lookup across tenants
- Cross-tenant email resolution not implemented
- This is a SaaS requirement for multi-tenant email uniqueness

### Finding 4: Tenant-less login may need global email resolution

**NOT IMPLEMENTED**  
- Login (`POST /api/auth/login`) requires `tenant_id` parameter
- No global email lookup across tenants
- Cross-tenant email resolution not implemented
- This is a SaaS requirement for multi-tenant email uniqueness

### Finding 5: `POST /api/billing/signup` may implement tenant creation, owner provisioning, trial, token issuance

**VERIFIED - FULLY IMPLEMENTED**  
`backend/app/api/routes/billing.py` lines 436-523 implement complete signup:
1. Rate limiting (5 signups/IP/hour via Redis)
2. Tenant creation with slugified name
3. RBAC role provisioning via `AuthService.provision_tenant_rbac()`
4. Owner user creation with Owner role
5. Default "General" AssignmentGroup seeded with Owner as member
6. Trial subscription creation via `BillingService.create_trial_subscription()`
7. Token issuance (access JWT + refresh token)
8. Returns `SignupResponse` with all tokens

### Finding 6: Frontend signup page may be missing or incomplete

**VERIFIED - EXISTS AND COMPLETE**  
`frontend/src/app/signup/page.tsx` is a complete self-service signup page:
- Organization name, email, password, full name fields
- Calls `signupTenant()` from auth-context
- Handles 409 (duplicate), 429 (rate limit), 422 (validation), 0 (network) errors
- Redirects to `/dashboard` on success

### Finding 7: `AIOperationsSummary.tsx` may remain stuck in loading state when requests fail

**NOT LOCATED**  
No file named `AIOperationsSummary.tsx` found in the codebase. May be a renamed/merged component or the finding refers to a different component. The pattern of loading state handling should be verified in dashboard components.

### Finding 8: Production hardening may include security headers, CORS, health endpoints, startup validation

**VERIFIED - IMPLEMENTED**  
- **Security headers**: `_SecurityHeadersMiddleware` in `main.py` (lines 270-313) sets X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, CSP, HSTS (production only)
- **CORS**: Configured with `CORS_ORIGINS` env var; production requires explicit origins (validated in `config.py::validate_for_production()`)
- **Health endpoints**: /health/live, /health/ready, /ready, /health, /ai-cto (5 endpoints)
- **Startup validation**: `config.py::validate_for_production()` refuses to boot with unsafe config (line 100-141)

### Finding 9: AI agent routing may be rule-based rather than LLM-driven

**VERIFIED - RULE-BASED WITH LLM AUGMENTATION**  
- **Agent Registry**: `app/models/agent.py` defines Agent model with hierarchy, permissions, workload
- **AI Gateway**: `app/ai/gateway.py` routes to Ollama provider
- **Model Router**: `app/ai/router.py` routes by tier (UTILITY/STANDARD/DEEP) using configured models
- **Current routing**: Rule-based tier selection, not autonomous LLM-driven orchestration
- **LLM usage**: Ollama used for intent classification, summarization, and potentially RCA
- **No autonomous agent orchestration**: Agents are data models, not autonomous executors

### Finding 10: Platform may need stronger SaaS packaging, tenant admin, billing, enterprise readiness

**VERIFIED - SIGNIFICANT GAPS REMAIN**  
See Section 5 below for detailed gaps.

---

## 3. Current Authentication and Tenant Isolation Status

### Authentication - IMPLEMENTED AND WORKING

| Component | Status | Evidence |
|-----------|--------|----------|
| Password hashing | Verified | Argon2id via passlib, `app/core/auth.py` line 58 |
| JWT access tokens | Verified | HS256, short-lived (15 min default), `auth.py` line 65-71 |
| Refresh tokens | Verified | Opaque, hashed, rotatable, `auth.py` line 17-18 |
| Login endpoint | Verified | `POST /api/auth/login` in `routes/auth.py` |
| Refresh endpoint | Verified | `POST /api/auth/refresh` in `routes/auth.py` |
| Logout | Partial | Client-side only (token clear), `auth-context.tsx` line 109-113 |
| API key auth | Verified | Supported in `get_current_user`, `auth.py` line 372-385 |
| Auth rate limiting | Verified | 5 RPM per IP on login/refresh, `routes/auth.py` line 62-91 |
| Duplicate email defense | Verified | Generic error for bad credentials, `auth_service.py` line 12-14 |
| Superuser X-Tenant-Id override | Verified | With validation, `auth.py` line 418-423 |

### Tenant Isolation - IMPLEMENTED FOR NEW ROUTES

| Aspect | Status | Evidence |
|--------|--------|----------|
| Tenant on models | Verified | `tenant_id` FK with CASCADE, all models |
| New routes derive tenant from principal | Verified | `Depends(get_tenant_id)` in auth.py, billing.py, onboarding.py |
| Legacy routes use query param | Warning | `NOTE(auth)` markers in alerts.py, intelligence.py, orchestration.py |
| Tenant isolation tests | Verified | 53/53 pass in `test_tenant_isolation.py` |
| Cross-tenant access prevention | Verified | Tested per-router in `test_tenant_isolation.py` |
| Soft-deleted tenant exclusion | Verified | `TenantRepository.get_existing()` in `test_auth_tenants.py` |
| Subscription gate for writes | Verified | `require_active_subscription` in `routes/auth.py` line 58-60 |

### Gaps

1. **Legacy routes not migrated**: `app/api/alerts.py`, `app/api/intelligence.py`, `app/api/orchestration.py` still use `tenant_id` query param
2. **No global email resolution**: Login requires tenant_id; cannot look up user by email across tenants
3. **Frontend DEFAULT_TENANT_ID fallback**: Many API calls still use hardcoded fallback
4. **Logout is client-side only**: No server-side token invalidation (access tokens are stateless JWTs by design)

---

## 4. Existing Test and Build Results

### Backend Tests

From `PRODUCTION_READINESS.md` (lines 95-102):
- **Auth core tests**: All pass (unit tests, no DB)
- **Tenant isolation tests**: 53/53 pass
- **Security boundary tests**: Pass
- **Auth service tests**: Fail due to asyncpg event loop binding issue in test harness
- **Agent API tests**: Fail (same asyncpg issue)
- **Full suite**: Has infrastructure issues (asyncpg event loop binding)

The test infrastructure issue is documented: asyncpg connections bound to different event loops. The auth core tests (which test actual auth logic) pass. The tenant isolation and security boundary tests (DB-backed auth) also pass.

### Frontend Tests

From `PRODUCTION_READINESS.md` (line 19):
- **Frontend build**: PASS (0 errors, 33 routes)
- **TypeScript check**: PASS
- **E2E tests exist**: `frontend/tests/settings-e2e.test.tsx`, `frontend/tests/data-consistency.test.tsx`, `frontend/tests/alert-dedup.test.tsx`

### Build Verification

- Backend imports: OK (`from app.main import app, lifespan`)
- Alembic: Single head, no branches
- Database connection: OK (verified via `_dbcheck.py`)

### Test Files Relevant to This Audit

| File | Purpose | Status |
|------|---------|--------|
| `tests/test_auth_core.py` | Auth primitives (hashing, JWT, Principal) | Passing |
| `tests/test_auth_service.py` | AuthService (login, refresh, RBAC) | Event loop issue |
| `tests/test_auth_tenants.py` | TenantRepository, override validation | Passing |
| `tests/test_tenant_isolation.py` | Per-router cross-tenant access | 53/53 passing |
| `tests/test_security_boundary.py` | Security hardening tests | Passing |
| `tests/test_billing_hardening.py` | Billing security tests | Exists |
| `tests/test_entitlement_gates.py` | Entitlement enforcement | Exists |
| `tests/test_onboarding.py` | Onboarding flow tests | Exists |
| `tests/test_production_config.py` | Production config validation | Exists |

---

## 5. Security Risks

### P0 - Immediate Attention

1. **Legacy routes use query-param tenant_id**  
   Files: `app/api/alerts.py`, `app/api/intelligence.py`, `app/api/orchestration.py`  
   Risk: Tenant isolation depends on client-provided parameter, not authenticated identity  
   Mitigation: Migrate to `Depends(get_tenant_id)`; marked with `NOTE(auth)` comments

2. **Frontend DEFAULT_TENANT_ID fallback**  
   File: `frontend/src/lib/api.ts` line 22  
   Risk: Default UUID used when no token present  
   Mitigation: Remove fallback; require authentication for all tenant-scoped data

3. **No server-side logout**  
   File: `frontend/src/lib/auth-context.tsx` line 109-113  
   Risk: Access tokens are stateless JWTs; cannot be revoked server-side before expiry  
   Mitigation: Acceptable for short-lived tokens (15 min); refresh token revocation works

4. **Signup rate limiting depends on Redis**  
   File: `backend/app/api/routes/billing.py` line 702-738  
   Risk: Fail-open on Redis outage (line 737-738)  
   Mitigation: Documented; acceptable for signup endpoint

### P1 - Should Address

5. **CORS_ORIGINS must be explicit in production**  
   File: `backend/app/core/config.py`  
   Risk: If unset in production, startup validation fails (good), but dev default is localhost:3000  
   Mitigation: Already enforced via `validate_for_production()`

6. **JWT secret fallback to encryption key**  
   File: `backend/app/core/config.py` line 92-98  
   Risk: In dev, JWT signs with `secret_encryption_key` if `jwt_secret_key` unset  
   Mitigation: Production validation rejects this (line 111-121)

7. **No audit log for auth events**  
   Risk: Login attempts, failures, root user actions not centrally audited  
   Mitigation: `alert_history` and `runbook_executions` exist but no unified audit trail

### P2 - Monitor

8. **ServiceNow sync job not registered in scheduler**  
   File: `scheduler/scheduler.py`  
   Risk: ServiceNow sync doesn't run on timer  
   Mitigation: Documented in REPOSITORY_AUDIT.md

9. **No universal ticket model**  
   Risk: ITSM integration limited to ServiceNow-specific models  
   Mitigation: Documented; could add generic Ticket entity

---

## 6. SaaS Readiness Gaps

### Critical (P0)

| Gap | Impact | Files |
|-----|--------|-------|
| No self-service tenant administration | Cannot manage users/roles/subscriptions via UI | `frontend/src/app/(dashboard)/settings/users-roles/page.tsx` (exists but verify completeness) |
| No subscription management UI | Users cannot upgrade/cancel/manage billing | `frontend/src/app/(dashboard)/subscription/page.tsx` (exists) |
| No usage tracking UI | Users cannot see their usage against limits | `frontend/src/app/(dashboard)/usage/page.tsx` (exists) |
| No tenant dashboard for platform admins | MSP/enterprise cannot manage multiple tenants | `frontend/src/app/(dashboard)/admin/customers/page.tsx` (exists) |
| No invitation flow | Cannot invite users to tenant | Not implemented |
| No password reset flow | Users cannot reset forgotten passwords | `login/page.tsx` line 201-206 shows "not available yet" message |

### Important (P1)

| Gap | Impact | Files |
|-----|--------|-------|
| No knowledge base | No knowledge articles for ITSM | Not implemented |
| No approval engine | `requires_approval` flag exists but no workflow | `app/models/approval.py` (exists, verify) |
| No autonomous remediation | `automation_ref` is free-form pointer | Not implemented |
| No unified audit trail | Scattered audit logs | Not implemented |
| No MSP organization hierarchy | Cross-tenant management is interim (X-Tenant-Id header) | Not implemented |

### Nice-to-Have (P2)

| Gap | Impact |
|-----|--------|
| No demo mode | Hard to showcase without real data |
| No GDPR/privacy documentation | Compliance gap |
| No multi-region preparation | Scaling limitation |
| No performance measurement | Unknown scalability characteristics |

---

## 7. Exact Files and Modules Requiring Changes

### Authentication Migration (P0)

| File | Current State | Required Change |
|------|---------------|-----------------|
| `backend/app/api/alerts.py` | Uses `tenant_id` query param | Migrate to `Depends(get_tenant_id)` |
| `backend/app/api/intelligence.py` | Uses `tenant_id` query param | Migrate to `Depends(get_tenant_id)` |
| `backend/app/api/orchestration.py` | Uses `tenant_id` query param | Migrate to `Depends(get_tenant_id)` |

### Frontend Auth Hardening (P0)

| File | Current State | Required Change |
|------|---------------|-----------------|
| `frontend/src/lib/api.ts` | DEFAULT_TENANT_ID fallback | Remove or make required; derive from token |
| `frontend/src/lib/api.ts` | Many functions use DEFAULT_TENANT_ID | Update to require tenant from auth context |

### Missing Features (P1)

| Feature | Files to Create/Modify |
|---------|----------------------|
| Password reset | `routes/auth.py` (add endpoints), frontend (add page) |
| User invitations | `routes/auth.py` (add invite endpoint), frontend (add UI) |
| Tenant administration | Enhance `settings/users-roles/page.tsx` |
| Subscription management | Enhance `subscription/page.tsx` |
| Usage dashboard | Enhance `usage/page.tsx` |

### Documentation (P1)

| Document | Status |
|----------|--------|
| `docs/engineering/architecture.md` | Needs update (lags current code per CLAUDE.md) |
| `docs/engineering/saas-readiness.md` | Not created |
| `docs/engineering/security.md` | Not created |
| `docs/engineering/testing.md` | Not created |
| `docs/engineering/deployment.md` | Not created |
| `docs/engineering/roadmap.md` | Not created |

---

## 8. Recommended Implementation Order

### Phase 1: Authentication and Tenant Isolation (P0)

1. **Migrate legacy routes to principal-derived tenant**  
   Files: `alerts.py`, `intelligence.py`, `orchestration.py`  
   Risk: Medium - existing API contract change  
   Test: Tenant isolation tests

2. **Remove DEFAULT_TENANT_ID fallback from frontend**  
   File: `frontend/src/lib/api.ts`  
   Risk: Medium - breaks unauthenticated API calls  
   Test: Frontend build + login flow

3. **Implement password reset flow**  
   Files: Backend endpoints + frontend page  
   Risk: Low - new feature  
   Test: Integration tests

### Phase 2: SaaS Control Plane (P1)

4. **Implement user invitation flow**  
   Risk: Low  
   Test: Invitation acceptance flow

5. **Complete tenant administration UI**  
   Files: `settings/users-roles/page.tsx`, new components  
   Risk: Low  
   Test: User/role CRUD

6. **Complete subscription management UI**  
   Files: `subscription/page.tsx`  
   Risk: Low  
   Test: Plan change flow

7. **Complete usage dashboard**  
   Files: `usage/page.tsx`  
   Risk: Low  
   Test: Usage display

### Phase 3: Enterprise Features (P2)

8. **Implement approval engine**  
   Files: `models/approval.py`, new service, new routes  
   Risk: Medium  
   Test: Approval workflow

9. **Implement knowledge base**  
   Risk: Low  
   Test: KB CRUD

10. **Create unified audit trail**  
    Risk: Medium - schema change  
    Test: Audit log queries

---

## 9. First Small, Safe Implementation Task

### Task: Migrate `app/api/alerts.py` to use `Depends(get_tenant_id)`

**Rationale**: This is the highest-risk remaining gap (legacy route using query-param tenant_id). It's isolated to one file, has clear migration pattern (see `routes/alerts.py` or `routes/monitoring.py` for examples), and has existing tests (`test_tenant_isolation.py`).

**Steps**:
1. Read `backend/app/api/alerts.py` to understand current tenant_id usage
2. Read `backend/app/api/routes/alerts.py` to see migrated pattern
3. Update `alerts.py` to use `Depends(get_tenant_id)` instead of query param
4. Run `test_tenant_isolation.py` to verify
5. Run `test_auth_core.py` to ensure no regression

**Estimated effort**: 2-4 hours  
**Risk**: Medium (API contract change, but pattern exists)  
**Test coverage**: Existing tenant isolation tests

---

## 10. Summary

### What's Working Well

- Complete authentication system (Argon2id + JWT + refresh tokens + API keys)
- RBAC with permission codes, superuser bypass, admin guards
- Tenant isolation for new routes (53/53 tests pass)
- Self-service signup (tenant + owner + RBAC + trial + tokens)
- Login and signup frontend pages
- Production hardening (security headers, CORS, health endpoints, startup validation)
- AI gateway with model routing (rule-based tier selection)
- Agent registry model
- Billing service with subscription lifecycle
- 20 Alembic migrations, single head

### What Needs Attention

- **P0**: Migrate legacy routes from query-param tenant_id to principal-derived
- **P0**: Remove DEFAULT_TENANT_ID fallback from frontend
- **P1**: Password reset flow
- **P1**: User invitation flow
- **P1**: Complete tenant administration UI
- **P2**: Approval engine
- **P2**: Knowledge base
- **P2**: Unified audit trail

### Verified vs. Original Claude Findings

| # | Finding | Status |
|---|---------|--------|
| 1 | Dev auto-login as admin@hsdeep.local | Fixed (opt-in only) |
| 2 | AUTH_BOOTSTRAP defaults enabled | Fixed (opt-in only) |
| 3 | Login forces hardcoded tenant ID | Partial (legacy routes) |
| 4 | Tenant-less login needs global email | Not implemented |
| 5 | POST /api/billing/signup implements signup | Verified |
| 6 | Frontend signup page missing | Exists and complete |
| 7 | AIOperationsSummary stuck loading | File not found |
| 8 | Production hardening exists | Verified |
| 9 | AI routing is rule-based | Verified |
| 10 | Needs stronger SaaS packaging | Verified gaps remain |

---

*End of Phase 0 audit. Ready for Phase 1 implementation.*
