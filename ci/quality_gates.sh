#!/usr/bin/env bash
# HSDEEP CORE AI - CI quality / security / build gates (Phase 9A).
#
# Provider-neutral: any CI (or a developer) can run this file. `set -euo pipefail`
# means the FIRST failing gate stops the pipeline, so a failed or missing gate can
# never produce a green build. Nothing here is skipped to obtain a pass.
#
# Requirements: python3.12 with backend/requirements-dev.txt installed, node 24 (.nvmrc) with
# frontend/node_modules installed, and a reachable PostgreSQL
# (backend/.env DATABASE_URL). Redis is optional: readiness degrades, the API
# still serves.
#
# Usage:  bash ci/quality_gates.sh [--with-containers] [--with-browser]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Resolve the backend interpreter. Prefer the project's pinned virtualenv so a
# developer machine cannot accidentally run the suite under a system `python`
# carrying a stale pytest-asyncio (see backend/tests/conftest.py's version guard).
# CI installs backend/requirements-dev.txt into a clean interpreter and has no
# venv, so it falls back to `python`.
# An explicit PYTHON=... (e.g. a shared virtualenv used by a git worktree) wins.
if [ -n "${PYTHON:-}" ]; then
  :
elif [ -x "$ROOT/backend/.venv/bin/python" ]; then
  PYTHON="$ROOT/backend/.venv/bin/python"
elif [ -x "$ROOT/backend/.venv/Scripts/python.exe" ]; then
  PYTHON="$ROOT/backend/.venv/Scripts/python.exe"
else
  PYTHON=python
fi

# Resolve the dedicated test database URL for DB-backed backend suites (the
# conftest guard requires TEST_DATABASE_URL so tests never fall back to the dev
# database). CI exports TEST_DATABASE_URL directly; local developers keep it in
# the gitignored backend/.env.test - NOT backend/.env, which would make
# app/core/db.py select the test DB for normal application startup too.
if [ -z "${TEST_DATABASE_URL:-}" ] && [ -f "$ROOT/backend/.env.test" ]; then
  TEST_DATABASE_URL="$(sed -n 's/^TEST_DATABASE_URL=//p' "$ROOT/backend/.env.test")"
  TEST_DATABASE_URL="${TEST_DATABASE_URL%$'\r'}"
  export TEST_DATABASE_URL
fi

WITH_CONTAINERS=0
WITH_BROWSER=0
for arg in "$@"; do
  case "$arg" in
    --with-containers) WITH_CONTAINERS=1 ;;
    --with-browser) WITH_BROWSER=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n=== %s ===\n' "$1"; }

step "1/6 security + dependency gates"
"$PYTHON" ci/secret_scan.py --self-test
"$PYTHON" -m pytest ci/test_secret_scan.py -q -p no:cacheprovider
"$PYTHON" ci/secret_scan.py
"$PYTHON" ci/requirements_check.py

step "2/6 frontend type-check, lint ratchet, unit tests, production build"
cd frontend
npx tsc --noEmit
cd "$ROOT"
"$PYTHON" ci/lint_ratchet.py
cd frontend
npm test
npm run build
cd "$ROOT"

step "2b/6 database migrations: one head, newest revisions reversible"
cd backend
HEADS="$("$PYTHON" -m alembic heads 2>/dev/null | grep -c '(head)' || true)"
if [ "$HEADS" != "1" ]; then
  echo "expected exactly one alembic head, found $HEADS" >&2
  "$PYTHON" -m alembic heads >&2
  exit 1
fi
# The round trip downgrades the newest revision, which drops its tables. It
# runs only in CI (a throwaway database) or when explicitly requested, never
# against a developer's working database by default.
if [ -n "${CI:-}" ] || [ "${MIGRATION_ROUNDTRIP:-0}" = "1" ]; then
  # Handles merge heads, where `downgrade -1` is ambiguous.
  "$PYTHON" ../ci/migration_roundtrip.py
fi
cd "$ROOT"

step "3/6 backend focused suites (blockers + contracts)"
cd backend
"$PYTHON" -m pytest \
  tests/test_source_rate_limiter.py \
  tests/test_rca_shutdown.py \
  tests/test_runtime_mode.py \
  tests/test_health_readiness.py \
  tests/test_scheduler_single_owner.py \
  tests/test_tenant_isolation.py \
  tests/test_api_quota.py \
  tests/test_auth_core.py \
  tests/test_auth_service.py \
  tests/test_auth_tenants.py \
  tests/test_rca_service_unit.py \
  tests/test_rca_lifecycle.py \
  tests/test_rca_trigger.py \
  tests/test_rca_activation.py \
  tests/test_rca_recommendation_sop.py \
  tests/test_dependency_intelligence.py \
  tests/test_dependency_integration.py \
  tests/test_topology_integration.py \
  -q
cd "$ROOT"

step "4/6 backend FULL suite"
cd backend
"$PYTHON" -m pytest -q
cd "$ROOT"

if [ "$WITH_CONTAINERS" = "1" ]; then
  step "5/6 production container builds (no secrets baked in)"
  docker build -t hsdeep-core-ai-backend:ci backend
  docker build -t hsdeep-core-ai-frontend:ci frontend
fi

if [ "$WITH_BROWSER" = "1" ]; then
  step "6/6 browser regression (requires a seeded dev stack on :8000 and :3000)"
  cd frontend
  node tests/portal-authenticated-incidents.cjs
  node tests/portal-responsive-validation.cjs
  cd "$ROOT"
fi

step "ALL GATES PASSED"
