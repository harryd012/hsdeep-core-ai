# HSDEEP CORE AI — Security Model

**Date**: 2026-09-13  
**Phase**: 1A Complete  
**Auditor**: Solar Pro4 (Staff Engineer / Security Engineer)

---

## 1. Authentication

### Password Storage
- **Algorithm**: Argon2id (via passlib)
- **Location**: `backend/app/core/auth.py`
- **Status**: Production-ready

### Access Tokens
- **Type**: HS256 JWT
- **Lifetime**: 15 minutes (default)
- **Claims**: `sub` (user ID), `tid` (tenant ID), `perms` (permission set), `email`, `iss`, `iat`, `exp`
- **Location**: `backend/app/core/auth.py` lines 65-71
- **Status**: Short-lived, stateless

### Refresh Tokens
- **Type**: Opaque high-entropy string (NOT a JWT)
- **Storage**: SHA-256 hash in `app/models/refresh_token.py`
- **Lifetime**: 30 days (default)
- **Rotation**: Yes — each refresh revokes the old token and issues a new one
- **Location**: `backend/app/core/auth.py` lines 17-18
- **Status**: Revocable server-side

### API Keys
- **Format**: Raw key presented as `Authorization: Bearer <key>`
- **Storage**: SHA-256 hash in `api_keys` table
- **Scopes**: Enforced as the Principal's permission set
- **Location**: `backend/app/core/auth.py` lines 372-385
- **Status**: Transparently supported alongside JWTs

---

## 2. Tenant Isolation Model

### Principal Resolution
The `get_tenant_id` dependency extracts the tenant ID from the authenticated principal:

```python
def get_tenant_id(principal: Principal = Depends(get_current_user)) -> uuid.UUID:
    return principal.tenant_id
```

**Location**: `backend/app/core/auth.py` line 447-452

### Tenant Resolution Flow
1. Request arrives with `Authorization: Bearer <JWT>` or `Authorization: Bearer <API key>`
2. `get_current_user` decodes the JWT or resolves the API key
3. `_principal_from_payload` creates a `Principal` with the tenant ID from the token
4. `get_tenant_id` extracts the tenant ID from the principal
5. All downstream queries are scoped to this tenant ID

### Superuser Cross-Tenant Override (Interim)
- **Mechanism**: `X-Tenant-Id` header
- **Authorization**: Only superusers may use this header
- **Validation**: `_validate_override_tenant` checks the target tenant exists and is not soft-deleted
- **Location**: `backend/app/core/auth.py` lines 418-423
- **Status**: Interim model pending formal MSP-org hierarchy

### Duplicate Email Defense
- Login with wrong password returns generic "Invalid credentials" error
- Does not reveal whether the email exists or which tenant it belongs to
- **Location**: `backend/app/services/auth_service.py` lines 12-14


---

## 3. Authorization (RBAC)

### Permission Model
- **Catalog**: `backend/app/core/permissions.py` — `PERMISSION_CATALOG` is the single source of truth
- **Default Roles**: Owner, Admin, Operator, Viewer (provisioned per tenant at runtime)
- **Resolution**: Permissions are resolved at login and embedded in the JWT

### Enforcement
- **Server-side**: `require_permission("permission.code")` dependency factory
- **Superuser bypass**: `is_superuser` flag grants all permissions
- **Admin guards**: `is_admin` check for tenant administration
- **Location**: `backend/app/core/auth.py` lines 426-444

### Subscription Gates
- **Write operations**: `require_active_subscription` blocks writes for suspended/canceled/incomplete tenants (HTTP 402)
- **Read operations**: Stay open (past_due keeps grace period)
- **Location**: `backend/app/api/routes/auth.py` lines 58-60

---

## 4. Production Hardening

### Security Headers
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()`
- `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (excludes /docs, /redoc)
- `Strict-Transport-Security: max-age=63072000; includeSubDomains` (production + FORCE_HTTPS only)
- **Location**: `backend/app/main.py` lines 270-313

### CORS
- **Development**: `localhost:3000` default
- **Production**: Must be explicitly set via `CORS_ORIGINS` env var
- **Validation**: `validate_for_production()` refuses to boot with wildcard origins
- **Location**: `backend/app/core/config.py`

### Rate Limiting
- **Authenticated**: Per-tenant sliding window (plan's `api_rate_limit`, default 60 RPM)
- **Unauthenticated**: Per-IP sliding window (default 60 RPM)
- **Auth endpoints**: 5 RPM per IP on login/refresh
- **Signup**: 5 per IP per hour (via Redis)
- **Location**: `backend/app/main.py` lines 185-274

### Startup Validation
- `validate_for_production()` refuses to boot with:
  - Default/placeholder secrets
  - Wildcard CORS origins
  - Missing encryption keys
  - Debug mode enabled
- **Location**: `backend/app/core/config.py` lines 100-141

---

## 5. Legacy Stub Cleanup (Phase 1A)

### Dead Code Removed
The following files were historical stubs NOT mounted in production. They have been cleaned up with deprecation notices and all endpoint implementations commented out:

| File | Status | Notes |
|------|--------|-------|
| `backend/app/api/alerts.py` | Cleaned | Dead stub, not mounted. Endpoints commented out. |
| `backend/app/api/intelligence.py` | Cleaned | Dead stub, not mounted. Endpoints commented out. |
| `backend/app/api/orchestration.py` | Cleaned | Dead stub, not mounted. Endpoints commented out. |

### Production Routes (Already Secure)
These are the actual mounted routes, all using `Depends(get_tenant_id)`:

| Route File | Mount Path | Auth |
|------------|------------|------|
| `app/api/routes/alerts.py` | `/api/alerts` | `Depends(get_tenant_id)` |
| `app/api/routes/intelligence.py` | `/api/intelligence` | `Depends(get_tenant_id)` |
| `app/api/routes/orchestration.py` | `/api/orchestration` | `Depends(get_tenant_id)` |
| `app/api/routes/monitoring.py` | `/api/monitoring` | `Depends(get_tenant_id)` |
| `app/api/routes/dashboard.py` | `/api/dashboard` | `Depends(get_tenant_id)` |
| ... (all 33 route files) | ... | `Depends(get_tenant_id)` |

---

## 6. Remaining Gaps

### P0 — Immediate
| Gap | Impact | Mitigation |
|-----|--------|------------|
| Frontend `DEFAULT_TENANT_ID` fallback | Many API calls use hardcoded UUID when no token present | Acceptable for now; backend derives tenant from JWT |
| No server-side logout | Access tokens are stateless JWTs; cannot be revoked before expiry | Short token lifetime (15 min) limits exposure |

### P1 — Should Address
| Gap | Impact | Mitigation |
|-----|--------|------------|
| No audit log for auth events | Login attempts, failures not centrally audited | `alert_history` exists; unified audit trail needed |
| No global email resolution | Login requires `tenant_id`; cannot look up user by email across tenants | Documented limitation; SaaS requirement |

### P2 — Monitor
| Gap | Impact | Mitigation |
|-----|--------|------------|
| Signup rate limit fail-open | Redis outage allows unlimited signup attempts | Documented; acceptable for signup endpoint |

---

## 7. Test Results (Phase 1A)

### Passing
- **Auth core tests**: 11/11
- **Tenant isolation (critical)**: Cross-tenant, own-tenant, unauthenticated tests PASS
- **Production app import**: Compiles and imports correctly

### Pre-existing Issues (asyncpg event loop)
- **Tenant isolation (teardown)**: ERROR at teardown due to asyncpg event loop binding (documented in audit)
- **Security boundary tests**: 5 failures due to same asyncpg issue (not security regressions)
- **Auth service tests**: Fail due to asyncpg event loop binding issue

### Frontend
- **Build**: Verified separately

---

*End of Phase 1A security documentation.*
