# Design: web-UI sessions should not use up the API allowance (option A approved)

Session: api-quota design (worktree wonderful-blackburn-ab39ca). Status: option A APPROVED 2026-10-29; implementing on branch fix/api-quota-api-key-only.

## What the code does today (verified 2026-10-29)

1. `_RateLimiter.key_for_request` (backend/app/main.py:323) only decodes **JWTs**. It gives
   `tenant:<tid>` for a session JWT, and `ip:<addr>` for everything else.
2. API keys are raw strings sent as `Authorization: Bearer <key>`, so the JWT decode fails
   and they get an `ip:` key. `_RateLimitMiddleware.dispatch` meters and enforces only
   `tenant:` keys, so **API-key traffic is never metered and never gets a 429.**
   **Only the web UI uses up the allowance.** That is the opposite of the intent.
3. `is_control_plane_path` / `CONTROL_PLANE_PATHS` exist in `api_quota.py` but **nothing calls them**,
   so `/api/auth/me` and `/api/auth/refresh` are metered and blocked at 429 too. A locked-out tenant cannot
   finish logging in.
4. Burst limiter: tenant keys use `plan.api_rate_limit` as the per-minute limit (50,000 rpm on Starter, so
   effectively none). IP keys, which today include API keys, get 60 rpm. The limiter sleeps rather than rejecting.
5. Collector ingest (`/api/v1/ingest/*`) authenticates with a collector credential, not an API key or JWT,
   so it is not metered. The collector has its own `collector.run` meter. Out of scope.

## Options

| | Rule | Pros | Cons |
|---|---|---|---|
| **A (recommended)** | Meter and enforce the period quota only for **API-key** principals. Session JWTs are never counted. Control-plane paths are exempt for everyone. Burst limiter unchanged. | Matches what the plan sells ("API calls" = integrations). Fixes the UI lockout and the fact that API keys are not metered at all. Small change. | A user can script against their session JWT (login + refresh) to get unmetered API access. The burst cap for a session is about 50k rpm, so it does not slow this down. |
| A+ | A, plus a fair-use per-minute cap for session traffic (for example 600 rpm per tenant) | Closes the scripted-session gap | Changes the burst behaviour for every tenant. Needs real UI traffic numbers to choose the cap. Better as a separate change. |
| B | Exempt requests that carry a "web UI" header or Origin | Trivial | Easy to spoof. Rejected. |
| C | Exempt a list of dashboard GET paths | No auth changes | Fragile, mixes up who is calling with what is called, and keeps drifting. Rejected. |
| D | Frontend: poll less, pause polling in hidden tabs | Worth doing anyway | Does not fix the principle. A busy NOC wall display would still lock the tenant out. |

## Proposed implementation (option A)

- `api_quota.py`: add `api_key_tenant(raw) -> UUID | None`. It looks up the SHA-256 hash in `api_keys`
  (active, not deleted, not expired), caches positive and negative results for 60s, and fails open to None.
  Authentication itself is still done by `get_current_user`; this is only used for metering.
- `dispatch`: burst limiting stays exactly as it is. Then: if `is_control_plane_path(path)`, pass through.
  If the bearer is a valid session JWT, pass through (not metered). If it resolves to an API key,
  meter and enforce against that key's tenant (same 429 body as today).
- Tests in `tests/test_api_quota.py`:
  - session request is not counted, and is served even when over quota
  - API-key request is counted, and gets a 429 over quota
  - `/api/auth/me` is exempt even for an over-quota tenant
  - the existing JWT 429 test is changed to use an API key

## Side effects to know

- Current-period `api.call` counters already include UI traffic. They will read high until the
  next period starts. The option is a one-off reset, which is not proposed here.
- The Usage page "API calls" will then show API-key/integration usage only. Label copy may need adjusting.
