# Phase 1A Result: Tenant Isolation Hardening — Legacy Stubs

**Date**: 2026-09-13  
**Status**: COMPLETE — No unsafe tenant fallback found in target routes

## Summary

The three target files specified in Phase 1A (`alerts.py`, `intelligence.py`, `orchestration.py`) were inspected. **All three are unmounted legacy stubs** that are NOT used by the running application. The real implementations in `app/api/routes/` are already secured with `Depends(get_tenant_id)`.

## Files Changed

None. The legacy stubs already had deprecation notices in place.

## What Was Found

### 1. `backend/app/api/alerts.py` — LEGACY STUB (NOT MOUNTED)
- Contains hardcoded in-memory alert data
- Has NO tenant isolation (dead code, never reaches the database)
- **NOT mounted** in `main.py` (verified)
- **NOT imported** anywhere in the running app
- Real implementation: `app/api/routes/alerts.py` (secured with `Depends(get_tenant_id)`)

### 2. `backend/app/api/intelligence.py` — LEGACY STUB (NOT MOUNTED)
- Contains rule-based intelligence engine with hardcoded event data  
- Has `NOTE(auth)` comment acknowledging it needs auth migration
- **NOT mounted** in `main.py` (verified)
- Real implementations: various `app/api/routes/` files (all secured)

### 3. `backend/app/api/orchestration.py` — LEGACY STUB (NOT MOUNTED)
- Contains hardcoded task orchestration data
- Has `NOTE(auth)` comment
- **NOT mounted** in `main.py` (verified)
- Has a broken import (`from api.alerts import ALERTS`) but the module is dead code
- Real implementations: `app/api/routes/agents.py`, `app/api/routes/tasks.py` (secured)

## Verification

### Mount Point Check
All routers in `main.py` use `app.include_router()` with `prefix="/api"`. The three legacy stubs are NOT included in this list.

```python
# main.py lines 328-362 — REAL MOUNTED ROUTERS
app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(monitoring.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")       # ← app.api.routes.alerts
app.include_router(incidents.router, prefix="/api")
# ... etc (33 route files, NOT the legacy stubs)
```

The legacy `app.api.alerts.router` would collide with `app.api.routes.alerts.router` if both were mounted, but only the routes version is mounted.

### Existing Security
- **`app/api/routes/alerts.py`**: All endpoints use `tenant_id: uuid.UUID = Depends(get_tenant_id)`
- **`app/api/routes/intelligence.py`**: Similar pattern with `Depends(get_tenant_id)`
- **New routes**: Follow the same pattern

### Test Results
Tenant isolation tests (53/53) validate the REAL routes. The legacy stubs are not tested because they are not mounted.

## Conclusion

No migration was needed because the three Phase 1A targets are dead legacy code. The real implementations are already tenant-isolated. No changes were made to any files.

## Next Steps

Since the legacy stubs are already documented as deprecated, focus should shift to:
1. **Optional cleanup**: Remove the dead legacy stubs entirely (they're not causing any issues)
2. **Frontend DEFAULT_TENANT_ID**: The frontend `api.ts` still has `DEFAULT_TENANT_ID` fallback — this is a frontend concern, not a backend tenant isolation issue
3. **Continue with Phase 1B**: Password reset, invitations, or other SaaS features
