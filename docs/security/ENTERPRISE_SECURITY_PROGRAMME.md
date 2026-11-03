# Enterprise security, privacy and compliance programme

Status: **S1 implemented and tested** (2026-11-03). S2–S6 approved as the plan, not built.

Every item is labelled by its real state. Nothing here claims a certification: HSDEEP CORE AI
holds no SOC 2, ISO 27001 or similar attestation.

## Baseline before S1 (audited 2026-11-03)

| Area | State before S1 |
|---|---|
| Passwords | Argon2id hashing; schema minimum 8 characters |
| Sessions | 15-minute access JWT with a session id; single-use refresh tokens (rotation, replay ends the session); logout, logout-all |
| Lockout | Per account after `LOGIN_MAX_FAILED_ATTEMPTS` (5) for 15 minutes; admin reset unlocks |
| RBAC | Per-tenant Owner / Admin / Operator / Viewer and custom roles over a permission catalog |
| Tenant isolation | Tenant from the token; superuser override only with a live, audited break-glass grant |
| Secrets at rest | Fernet (key from `SECRET_ENCRYPTION_KEY`) for source credentials and ITSM secrets; collector and API keys stored as hashes |
| Security headers | nosniff, frame DENY, referrer policy, permissions policy, strict CSP; HSTS when forced |
| **Gaps** | Security settings page stored a policy nothing enforced and an MFA toggle that did nothing; no way to read the audit log; role / user / policy changes not audited; no session list |

## S1: enforce and see (implemented)

**Tenant security policy** (`app/services/security_policy.py`, Settings > Security):

- Password rules (minimum length 8–64, optional upper-case / number / symbol, never the email
  name) apply wherever a password is set: sign-up, admin create and reset, invitation, self-service
  reset. A violation is a 422 that names every missing rule.
- Defaults follow NIST SP 800-63B: length over composition, so no character classes are forced
  until a tenant turns them on. (The old stored defaults, which were never enforced, said
  otherwise; enforcing them would have broken existing passwords.)
- Lockout: a saved policy's attempt count (3–20) is the threshold; a tenant that never saved a
  policy keeps the deployment's `LOGIN_MAX_FAILED_ATTEMPTS`.
- Idle timeout (15 minutes to 30 days, default 8 hours): a refresh token older than the timeout
  cannot be refreshed and its whole session ends. The lower bound is the access-token lifetime,
  so a session in use is never cut between two refreshes. An open, active tab keeps refreshing,
  so this ends sessions on closed or sleeping devices.
- MFA cannot be switched on until S2 (422), so the page never claims protection it lacks.
- Changing the policy needs the new `security.manage` permission (Owner, Admin; it was
  superuser-only) and is audited with old and new values (`security.policy_updated`).

**Sessions** (`auth_sessions`, migration `d4f2b8a6c1e3`):

- `GET /api/auth/sessions`: the caller's live sessions with device, address, start and last use;
  the current one is marked. Liveness stays with the refresh tokens; the table only adds details,
  so sessions from before S1 are listed without them.
- `DELETE /api/auth/sessions/{id}`, `POST /api/auth/sessions/end-others`: end your own sessions
  (another user's session id is a 404).
- `POST /api/auth/users/{id}/sessions/end` (`security.manage`): sign a user out everywhere (lost
  laptop, offboarding).
- All audited (`auth.session_ended`, `auth.other_sessions_ended`, `auth.signed_out_everywhere`,
  `auth.user_signed_out`).

**Audit log** (`app/api/routes/audit_log.py`, Settings > Audit log):

- `GET /api/audit/events` (filters: action or action prefix, actor, entity, time window, text),
  `GET /api/audit/events/facets`, `GET /api/audit/events/export` (CSV, at most 50,000 rows).
- New `audit.read` permission: Owner and Admin. It is deliberately not part of the read-only
  Viewer role (which otherwise gets every `.read`).
- CSV cells that a spreadsheet would run as a formula are prefixed with an apostrophe.
- The export is itself an audit event (`audit.exported`).
- Newly audited: `role.created / updated / deleted` (permission codes before and after),
  `user.created / password_set / roles_changed / deleted`.

**Fixes found on the way:**

- `POST /api/auth/roles` always failed (500): the role repository's full refresh expired the
  permissions it was about to return. Untested before; covered now.
- After `PATCH /api/auth/roles/{id}` and `POST /api/auth/users/{id}/roles` the response could show
  the old permissions or roles (a collection loaded earlier in the session was not replaced).
- `get_optional_user` (currently unused) now applies the same session-liveness and break-glass
  checks as `get_current_user`, so it can never become a way around them.

Tests: `tests/test_security_s1.py`; `tests/test_rbac_write_routes.py` lists the two new
self-service session routes. Full backend suite green (2770 passed).

**Known limits:** the login rate limit is still per process (S3); a composite
`audit_events (tenant_id, created_at)` index was left out because the test database's
`audit_events` table is owned by `postgres` (see the test-database note in
`docs/design/OBSERVABILITY_PLATFORM_PLAN.md`); `tenant_id` is already indexed.

## Approved plan, not built yet

| Phase | Scope |
|---|---|
| S2 MFA | TOTP authenticator apps with recovery codes, tenant "require MFA", admin MFA reset |
| S3 Network & API | Per-tenant IP allowlist, API key scopes validated against the catalog, shared (DB/Redis) login rate limit |
| S4 Encryption & integrity | Versioned keys with rotation (MultiFernet) and a re-encryption job, tamper-evident audit hash chain, per-tenant key design (for customer-managed keys later) |
| S5 Privacy & compliance | Per-tenant retention, tenant data export, offboarding with a grace period, secret / PII redaction in logs and AI prompts, AI data-use control, an honest Trust page |
| S6 Enterprise identity | OIDC (Entra ID, Okta, Google), SAML 2.0, SCIM provisioning, domain verification, require SSO |
