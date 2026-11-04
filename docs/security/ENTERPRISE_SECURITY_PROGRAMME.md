# Enterprise security, privacy and compliance programme

Status: **S1–S3 implemented and tested** (2026-11-03 / 04). S4–S6 approved as the plan, not built.

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

## S2: multi-factor authentication (implemented)

- **Authenticator apps (TOTP)** (`app/services/mfa/totp.py`): RFC 6238, HMAC-SHA1, 30-second
  steps, 6 digits, ±1 step for clock drift; checked against the RFC test vectors. No new
  dependency for TOTP; QR codes are rendered server-side as SVG with `segno` (pure Python).
- **Enrolment** (`user_mfa`, migration `e5a3c9d1f7b2`): set-up stores a new secret, encrypted with
  the platform key, unconfirmed; it becomes MFA only after the first valid code (confirm). The
  secret is never returned after set-up.
- **Recovery codes** (`mfa_recovery_codes`): 10 one-time codes shown once at confirm or
  regenerate, stored as SHA-256 hashes; regenerating replaces the set. Using one at sign-in is an
  audit event with the number left.
- **Sign-in** becomes two steps for a user with MFA: `POST /api/auth/login` answers
  `{mfa_required, mfa_token}` (a 5-minute challenge token that is rejected as a bearer token);
  `POST /api/auth/login/mfa` takes the token and a code. A TOTP step is accepted once (replay
  protection) and wrong codes count towards the account lockout like wrong passwords. The
  password step alone neither issues tokens nor clears the failure count.
- **Tenant policy "Require two-step verification"** (the existing `mfa_enabled` column): a user
  without MFA still signs in with the password, but their access token carries `mfa_enroll`; until
  they set MFA up every API answers 403 `MFA_ENROLLMENT_REQUIRED` except MFA set-up, `/auth/me`,
  sign-out, sessions and reading the security policy. The frontend sends them to set-up. After
  confirming, the session refreshes without the restriction. Existing access tokens pick the
  policy up at their next refresh (at most 15 minutes).
- **Turning MFA off** needs a current code and is refused while the policy requires MFA.
- **Admin reset** (`POST /api/auth/users/{id}/mfa/reset`, `security.manage`): removes the user's
  MFA and ends their sessions (lost device). Also on Settings > Users & Roles, with "sign out of
  every session".
- **Audit:** `mfa.enabled`, `mfa.disabled`, `mfa.recovery_codes_regenerated`,
  `mfa.recovery_code_used`, `mfa.reset_by_admin`.
- **UI:** login code step (app code or recovery code), Two-step verification panel on
  Settings > Security (QR, key, confirm, recovery codes to copy or download, replace, turn off),
  the policy switch, and the admin actions.

Tests: `tests/test_mfa_totp.py`, `tests/test_security_s2_mfa.py`. Full backend suite green
(2781 passed).

**Known limits:**
- The dev auto-login (`NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP`) signs in with a password only, so the
  bootstrap account must not have MFA (or the tenant must not require it).
- WebAuthn / passkeys and SMS are not offered.
- The login code step was type-checked and tested through the API, not clicked through in a browser.

## S3: network and API controls (implemented)

- **Client address** (`app/core/client_ip.py`): the TCP peer, or behind `TRUSTED_PROXY_HOPS`
  proxies the address the outermost trusted proxy saw. Addresses a client writes into
  `X-Forwarded-For` itself are never trusted. Set `TRUSTED_PROXY_HOPS` to the number of proxies
  in front of the API in each deployment (0 by default).
- **Per-tenant IP allowlist** (`tenant_ip_rules`, migration `f6b4d0e2a8c3`,
  `app/services/network_policy.py`): no rules = open to every network (existing tenants are
  unaffected). With rules, the tenant's users can only sign in, refresh and call the API, and its
  API keys can only be used, from inside one of them (IPv4 / IPv6 ranges or single addresses;
  IPv4-mapped IPv6 handled). Refusals are 403 `IP_NOT_ALLOWED`.
  - Sign-in checks the network only after the password is right, so the answer tells nothing to
    someone without it; a refused sign-in is audited (`auth.login_blocked_network`, with address).
  - The caller's home tenant decides; platform support under a break-glass grant is governed by
    the grant, not the customer's allowlist.
  - Lock-out guard: adding or removing a rule is refused when the resulting allowlist would not
    include the admin's own address.
  - Rules are cached per process for 30 seconds (cleared at once where the change was made).
  - API `GET/POST/DELETE /api/settings/network/ip-rules` (`security.manage`) and
    `GET /api/settings/network/my-ip`; changes audited (`network.ip_rule_added / removed`).
- **API key scopes**: creating a key needs at least one scope, every scope must be in the
  permission catalog, and the creator must hold each one (no privilege escalation). Managing API
  keys moved from superuser-only to `security.manage`; listing keys now needs it too.
- **Shared rate limits** (`app/core/shared_rate_limit.py`): the login / refresh / account
  self-service limit (5 per minute per address) is now a Redis sliding window shared by every API
  process, like the sign-up limit (which now uses the same helper). When Redis is down the same
  limit is enforced per process, never skipped.
- **UI**: Network access panel on Settings > Security (your address, rules, add / remove), and
  readable messages for a refused network at sign-in and in the app.

Tests: `tests/test_security_s3_network.py`.

## Approved plan, not built yet

| Phase | Scope |
|---|---|
| S4 Encryption & integrity | Versioned keys with rotation (MultiFernet) and a re-encryption job, tamper-evident audit hash chain, per-tenant key design (for customer-managed keys later) |
| S5 Privacy & compliance | Per-tenant retention, tenant data export, offboarding with a grace period, secret / PII redaction in logs and AI prompts, AI data-use control, an honest Trust page |
| S6 Enterprise identity | OIDC (Entra ID, Okta, Google), SAML 2.0, SCIM provisioning, domain verification, require SSO |
