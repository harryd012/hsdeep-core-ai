# Security gap register

Each entry was checked against the code on `release/cloud-saas-stabilization`
on 2026-10-30. Severity: Critical / High / Medium / Low. Status: OPEN,
IN_PROGRESS, FIXED (with commit), ACCEPTED (owner-approved risk).

| ID | Area | Finding (verified) | Severity | Status | Phase |
| --- | --- | --- | --- | --- | --- |
| SEC-01 | Tenant identity | Was: `DEFAULT_TENANT_ID` fell back to the nil UUID and was sent as `?tenant_id=` on ~119 calls. Verified no backend route reads a tenant from the query (`grep tenant_id.*Query(` in `app/api/routes`: 0); tenant comes only from the principal, superuser switching only from the validated `X-Tenant-Id` header. Now: no fallback value, `withTenant` no longer sends `tenant_id`, report pages stop appending it. | Medium | FIXED (R1.1 commit) | R1.1 |
| SEC-02 | Support access | Was: any superuser could act in any tenant via `X-Tenant-Id` with no time limit or reason, and the audit was fire-and-forget. Now (owner chose break-glass): the override needs a live grant (`support_access_grants`, migration `d2b8e6f1a4c9`) with a reason of 10+ characters and an expiry of up to 8 h (`SUPPORT_GRANT_MAX_MINUTES`); grants and revocations are audited (awaited) in the customer's tenant; customers list grants (`users.read`) and revoke them (`users.update`); each use is logged with the grant id. Normal users' overrides now return 403 (was 401). `tests/test_support_access.py` (11). | High | FIXED (R1.2 commit) | R1.2 |
| SEC-03 | Tenant isolation tests | `test_tenant_isolation`, `test_security_boundary` and per-feature tests exist; there is no systematic cross-tenant suite over every tenant-scoped resource listed in R1.3. | High | OPEN | R1.3 |
| SEC-04 | Session revocation | Was: access JWTs stayed valid after logout until expiry. Now: access tokens carry a session id (`sid`); every request checks the session is live (unrevoked token, active user; 10 s per-process cache, `SESSION_CHECK_CACHE_SECONDS`). Logout, logout-all, password change/reset, role change, user deletion and refresh-token replay end the session. Tokens without `sid` (minted before this change) last until their 15 min expiry. `tests/test_session_revocation.py` (8). | High | FIXED (R1.4 commit) | R1.4 |
| SEC-05 | RBAC | Every write route requires a permission (`test_rbac_write_routes`). Read routes rely on authentication + tenant scoping; per-role read tests missing. | Medium | OPEN | R1.5 |
| SEC-06 | Audit | `AuditService` records auth token events and approvals; no unified security audit (outcome, source IP, correlation id) for login failure, role change, API key lifecycle, configuration changes. | Medium | OPEN | R1.6 |
| SEC-07 | Rate limiting | Was: signup rate limit failed open when Redis was unavailable, using a blocking client in an async route. Now: async shared client; on any Redis error the same 5/hour/IP limit is enforced in-process (per replica) and the outage is logged at error level. `tests/test_signup_rate_limit.py` (2). Login/refresh limiter is in-process by design (5 rpm/IP). | High | FIXED (R1.7 commit) | R1.7 |
| SEC-08 | API keys | Keys are hashed with scopes, `expires_at`, `last_used_at` (`backend/app/models/api_key.py`). `expires_at`/`last_used_at` are strings, not timestamps; expiry enforcement to be verified. | Medium | OPEN | R1.8 |
| SEC-09 | Secrets at rest | Collector credentials are encrypted (`SECRET_ENCRYPTION_KEY`); notification secrets (Slack URL, PagerDuty key) are stored in plain columns, masked in the API. | Medium | OPEN | R1.9 |
| SEC-10 | Secret scanning | Scanner skipped every `tests` directory and let placeholder words anywhere on a line hide a value. | High | FIXED `8d8b90929` | R0.3 |
| SEC-11 | Runtime | Node 20 (end of life 2026-04-30) in CI and the production image. | High | FIXED `c32dad8a1` (Node 24 LTS) | R0.2 |
| SEC-12 | Production config | `validate_for_production()` refuses unsafe settings (log email transport, missing keys); CORS/cookie/TLS review per R1.9 not done. | Medium | OPEN | R1.9 |

Not performed: penetration testing, container image scanning, dependency CVE
scanning (no scanner wired yet; R5.7).
