# Enterprise security, privacy and compliance programme

Status: **S1–S6 implemented and tested** (2026-11-03 / 04), except SAML 2.0 (see S6).

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

## S4: encryption and integrity (implemented)

**Key rotation** (`app/core/security.py`, `app/services/key_rotation.py`):

- `SecretCrypto` encrypts with `SECRET_ENCRYPTION_KEY` and also decrypts with the keys in
  `SECRET_ENCRYPTION_KEYS_PREVIOUS` (comma-separated), so keys rotate without downtime.
- Every encrypted column is registered in `ENCRYPTED_FIELDS`: monitoring source credentials,
  ServiceNow secrets and OAuth tokens, ITSM credentials (per value in the JSON) and webhook
  secrets, and MFA secrets.
- `python scripts/rotate_encryption_keys.py --status | --rotate [--tenant]` reports, per column,
  how many values the current key protects, how many still need an older key and how many no key
  can read; `--rotate` re-encrypts the older ones in batches (`FOR UPDATE SKIP LOCKED`). Only
  counts and 8-character key fingerprints are shown, never a secret. Exit code 1 if anything is
  unreadable. `GET /api/admin/security/encryption` gives the same status to platform superusers.
- Procedure: set the new key, move the old one to the previous list, restart, run `--rotate`,
  remove the old key once `--status` shows nothing on it.
- Note: in development, where `JWT_SECRET_KEY` is unset, the JWT secret falls back to the
  encryption key, so changing it there also signs everyone out. Production requires a separate
  `JWT_SECRET_KEY` (existing validator).

**Tamper-evident audit log** (`audit_chain`, migration `a7c5e1f3b9d4`, `app/services/audit_chain.py`):

- Each tenant's audit events form a SHA-256 hash chain: every link hashes the previous link
  plus the event's canonical content as stored. A per-tenant advisory lock keeps it sequential.
- `AuditService` links each event as it is written (in its own savepoint; a failure leaves it to
  the sealing job). Events that a few services insert directly are linked by the scheduler's
  sealing job every 5 minutes. Existing events were linked oldest first by the job after upgrade.
- Links live in their own table, keyed by event id without a foreign key, so a deleted event
  leaves its link and shows as `event_deleted`; an edited event shows as `event_modified`; an
  altered link as `broken_link`.
- `GET /api/audit/integrity` (`audit.read`) checks the most recent 2,000 events (anchored on the
  stored link before them) so it stays fast at any size; `?full=true` or
  `python scripts/verify_audit_chain.py` checks the whole history (dev tenant: 16,694 events in
  about 2 seconds warm).
- The chain head (sequence and hash) is shown on the Audit log page with "Copy checkpoint" and
  sent with every CSV export (`X-Audit-Chain-Head`). Someone with full database write access could
  rebuild the chain; a checkpoint kept outside the platform detects that.

**Per-tenant keys (design, not built):** the next step towards customer-managed keys is envelope
encryption: a random data key per tenant encrypts that tenant's secrets, and only the data keys
are encrypted with the platform key (later, a key in the customer's own KMS: AWS KMS, Azure Key
Vault, GCP KMS). Rotating the platform key then re-wraps a handful of data keys instead of every
secret, and revoking a customer's KMS key makes their data unreadable (crypto-shredding). It needs
a `tenant_keys` table, a `key_id` prefix on stored ciphertexts and a KMS client abstraction; the
`ENCRYPTED_FIELDS` registry and rotation job above are the migration path.

Tests: `tests/test_security_s4.py`. Full backend suite green (2799 passed).

## S5: privacy and compliance (implemented)

Settings: `tenant_privacy_settings` (migration `b8d6f2a4c0e5`), Settings > Privacy & data,
`GET/PUT /api/settings/privacy`. New permissions: `privacy.manage` (Owner, Admin) and
`tenant.delete` (Owner only; excluded from Admin like `roles.*`).

- **Redaction** (`app/core/redaction.py`): passwords / tokens / keys in key=value form, bearer and
  basic credentials, JWTs, private-key blocks, AWS access keys, connection-string passwords and
  Luhn-valid card numbers; optionally email and IP addresses. Applied to every application log
  line (a filter on the root handler) and to every AI prompt. Pattern based: it lowers the risk
  of a leak, it does not prove there is none.
- **AI data use**: one chokepoint, `AIGateway.chat`. The tenant (from the signed-in request via a
  context variable set in `core.auth`) can turn AI off: the gateway then makes no model call and
  returns `ai_disabled_by_tenant`. Credentials are always removed from prompts; email addresses
  by default; IP addresses on request. Background jobs without a tenant use the defaults.
- **Retention**: a tenant can set its own metric / sensor history and raw-event retention (7 days
  to 10 years); the hourly job applies it per tenant and the platform default
  (`METRIC_RETENTION_DAYS`) only to tenants without their own. Normalized events, alerts,
  incidents and the audit log are not purged (they feed correlation and compliance).
- **Data export** (`tenant_exports`, `app/services/tenant_export.py`): `POST
  /api/settings/privacy/exports` queues a full export; the job writes a ZIP with `manifest.json`
  (row counts, omitted tables and columns, audit chain head) and one JSON-lines file per table
  that has a `tenant_id` (106 tables today; new ones are included automatically). Sign-in internals
  (sessions, refresh / account tokens, MFA secrets, email outbox, earlier exports) and every
  credential-like column are left out. One export at a time; the file is kept 7 days under
  `EXPORT_DIR` (default `backend/var/exports`, git-ignored); requests and downloads are audited.
- **Offboarding** (`app/services/offboarding.py`): an Owner schedules deletion by typing the
  organization's name; it happens 30 days later unless cancelled. The hourly purge job deletes the
  tenant row (every tenant table cascades) and its export files, and logs the purge at platform
  level (the tenant's own audit log is part of what is deleted).
- **Trust & security page** (`/trust`, public, linked from the sign-in screens and Privacy & data):
  states only implemented controls, marks SSO / SCIM as not available yet and says plainly that
  no certification is held.

Tests: `tests/test_security_s5_privacy.py`, `tests/test_security_s5_export.py`. Full backend suite
green (2820 passed).

**Known limits:** exports run in the API process's scheduler and write to local disk (a shared
object store is needed with several API replicas); deletion is not yet surfaced to platform
admins on the Customers page; redaction does not cover free-text personal data such as names.

## S6: enterprise identity (implemented, SAML deferred)

Migration `c9e7a3b5d1f6`; Settings > Single sign-on (`security.manage`); `app/api/routes/identity.py`,
`app/api/routes/scim.py`, `app/services/identity/`.

- **Verified domains**: a tenant adds an email domain and publishes
  `_hsdeep-challenge.<domain> TXT "hsdeep-verify=<token>"`; verification looks it up (dnspython,
  pinned). A verified domain belongs to one tenant only (partial unique index), so one customer
  can never route another's employees to its identity provider.
- **OIDC single sign-on** (Authorization Code + PKCE S256, state, nonce) against any OpenID Connect
  provider (Entra ID, Okta, Google Workspace, Auth0, Ping …): issuer discovery, the client secret
  encrypted with the platform key (and in the S4 rotation registry), ID token signature checked
  against the provider's JWKS (RS/PS/ES algorithms only, unknown key id re-fetched once), issuer,
  audience, expiry and nonce checked, email on a verified domain required. The browser gets a
  one-time 60-second handoff code, never tokens in a URL (`/sso/complete`). Users are matched by
  linked subject, then email; just-in-time creation with a default role is optional. SSO sign-ins
  do not add the platform MFA step (the identity provider handles it). Audited (`auth.sso_login`).
- **Require SSO**: password sign-in is refused (403 `SSO_REQUIRED`) for users on the tenant's
  verified domains, except Owners (break-glass accounts to repair a broken identity provider);
  users on other domains (contractors) keep passwords.
- **SCIM 2.0** at `{API_PUBLIC_URL}/api/scim/v2` with a per-tenant bearer token (stored as a hash,
  shown once, replace / revoke): ServiceProviderConfig and Users (list with `userName` /
  `externalId` / `emails.value eq` filters, get, create, replace, patch in both the Entra and
  Okta forms, delete). Deactivating or deleting a user ends their sessions at once; new users get
  the default role and no password. Audited with actor type `scim`. Groups are not provisioned
  (roles are assigned in the platform). SCIM errors use the standard status codes; the body is
  wrapped in FastAPI's `detail`.
- **Login page**: "Sign in with SSO" uses the email typed in the form to find the organization's
  provider; a `SSO_REQUIRED` refusal shows the reason.
- **Configuration**: `API_PUBLIC_URL` (the callback base, default `http://localhost:8000`) and
  `APP_PUBLIC_URL` (where the browser lands) must be the public URLs in each deployment.

**SAML 2.0 is not implemented.** Verifying SAML assertions safely needs an XML-signature library
(`signxml` + `lxml`) and careful protection against signature-wrapping attacks; that is a separate
step. Every major identity provider supports OpenID Connect, which is available now.

Tests: `tests/test_security_s6_identity.py` (a fake identity provider with real RSA keys, JWKS and
a token endpoint).

## Not built yet

| Item | Scope |
|---|---|
| SAML 2.0 | Assertion consumer service with signxml-based verification, IdP metadata import |
| SCIM Groups | Map identity-provider groups to platform roles |
| Per-tenant keys | Envelope encryption and customer-managed keys (design in S4) |
