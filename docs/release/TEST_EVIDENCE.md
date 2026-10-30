# Test evidence

Each entry: date, revision, command, environment, result. Failures are
classified (new regression, pre-existing defect, environment, external,
flaky, unsupported) and never dropped.

## 2026-10-30 - R0 local gates, `release/cloud-saas-stabilization`

Revision: working tree at `8d8b90929` plus the R0 documentation and the
`PYTHON=` override in `ci/quality_gates.sh` (committed with this file).
Environment: Windows 11, Git Bash, Python 3.11.6 virtualenv
(`I:\AI-LAB\hsdeep-core-ai\backend\.venv`; CI and the image use 3.12),
Node 24.18.0, PostgreSQL test database `hsdeep_test` at head `b5e8c2d4f6a1`.
Docker is not installed locally, so gate 5 (container builds) runs only in CI.

Command:

```
PYTHON=I:/AI-LAB/hsdeep-core-ai/backend/.venv/Scripts/python.exe bash ci/quality_gates.sh
```

| Gate | Result |
| --- | --- |
| Secret scan self-test | PASSED (10 positives, 6 placeholders excluded) |
| Secret scan tests (`ci/test_secret_scan.py`) | 14 passed |
| Secret scan (1,095 files) | PASSED: 0 fatal, 43 warnings (throwaway passwords in tests) |
| Runtime dependency check | PASSED |
| `tsc --noEmit` | PASSED |
| Lint ratchet | PASSED: src 9 errors (baseline 13), repo 10 (baseline 62), no new errors |
| Frontend unit tests (`npm test`) | 121 passed, 0 failed |
| `next build` | PASSED (includes `/infrastructure/firewalls`) |
| Alembic heads | exactly 1 |
| Backend focused suites | 266 passed |
| Backend full suite | 2,533 passed, 1 skipped, 0 failed (4 min 7 s) |
| Container builds | not run locally (no Docker); CI only |
| Migration round trip (newest revision) | CI only by design; run manually on `hsdeep_test` earlier the same day: downgrade `b5e8c2d4f6a1` → upgrade head OK |

Classified failures during the run:

- First attempt exited 4 at the focused suites: the worktree has no
  `backend/.venv`, so the script fell back to a system Python with
  pytest-asyncio 0.24 and `conftest.py` refused it. Environment failure; fixed
  by letting `PYTHON=` override interpreter discovery. Not a code defect.
- Two earlier test processes hung because a new test left uncommitted rows
  that blocked the tenant fixture's teardown (`test_topology_coverage.py`);
  fixed in `4fe4c8268` by committing inside the test. New-test defect, caught
  before commit.

## 2026-10-30 - Live checks (browser pane)

- Firewalls page and Tool gateway panel on a throwaway demo tenant in
  `hsdeep_test`: values matched the seeded data; the tenant was deleted.
- Executive Command Center coverage row and SOP Engine on a throwaway demo
  tenant: firewall, topology-link and SOP counts matched; SOP Engine showed
  only the tenant's SOPs.

## 2026-10-30 - R1 batch 1 (R1.1, R1.4, R1.7)

| Command | Result |
| --- | --- |
| `pytest tests/test_session_revocation.py` | 8 passed |
| `pytest test_session_revocation test_auth_core test_auth_service test_auth_tenants test_email_foundation test_pilot_safety test_security_boundary` | 64 passed |
| `pytest tests/test_signup_rate_limit.py test_pilot_safety test_billing_hardening` | 13 passed |
| `alembic upgrade head; downgrade -1; upgrade head` on `hsdeep_test` | `c7f1a9d3e5b2` OK both ways |
| Full backend suite (`pytest -q`) at `9e979cbc4` | 2,542 passed, 1 skipped, 1 failed: `test_every_mutating_route_requires_a_permission` flagged the new `/api/auth/logout-all` (new regression in the guard list, not in auth). Fixed in `fe7af585f`; file re-run 15 passed. |
| Frontend `tsc --noEmit`, `npm test` | clean; 121 passed |

## 2026-10-30 - R1.2 break-glass support access

Test database changed to `hsdeep_test_release` (this worktree's
`backend/.env.test`): another line of work migrated the shared `hsdeep_test`
to a revision not on this branch. The new database was built from empty
through all 70 migrations to `d2b8e6f1a4c9`, which also proves the full chain
from a blank database.

| Command | Result |
| --- | --- |
| `pytest tests/test_support_access.py tests/test_auth_tenants.py tests/test_auth_core.py tests/test_tenant_isolation.py tests/test_security_boundary.py tests/test_session_revocation.py` | 105 passed |
| Full backend suite | 2,553 passed, 1 skipped, 1 failed: RBAC guard flagged the two new grant routes (authorised in the handler); listed with reason; `test_rbac_write_routes` + `test_support_access` 26 passed |
| Frontend `tsc`, `npm test`, eslint on changed files | clean; 121 passed; 0 errors |
| Browser (throwaway demo tenants, removed afterwards) | grant form shown, reason required, grant opened, override active; revoked grant sends the app back to the home tenant |

## Remote CI

| Run | Branch | Revision | Result |
| --- | --- | --- | --- |
| 40976698591 | `feat/platform-buildout-b1` | `372863197` | success |
| 40993533063 | `feat/platform-buildout-b1` | `2c6831db7` | success (firewall gateway) |
| - | `release/cloud-saas-stabilization` | - | not pushed (BLK-R0-01) |
