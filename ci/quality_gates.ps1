# HSDEEP CORE AI - CI quality / security / build gates (Phase 9A).
# Windows twin of ci/quality_gates.sh - identical gates, so a developer can run
# exactly what CI runs. The first failing gate aborts the run.
#
# Usage:
#   pwsh -File ci/quality_gates.ps1
#   pwsh -File ci/quality_gates.ps1 -WithContainers -WithBrowser
#
# Requires: python (backend deps incl. requirements-dev.txt), node
# (frontend/node_modules), and a reachable PostgreSQL via backend/.env.

[CmdletBinding()]
param(
    [switch]$WithContainers,
    [switch]$WithBrowser
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step([string]$title) { Write-Output "`n=== $title ===" }

function Native([string]$label, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) {
        Write-Output "GATE FAILED: $label (exit $LASTEXITCODE)"
        exit 1
    }
}

Step '1/6 security + dependency gates'
Native 'secret scan self-test' { python ci/secret_scan.py --self-test }
Native 'secret scan' { python ci/secret_scan.py }
Native 'runtime dependency completeness' { python ci/requirements_check.py }

Step '2/6 frontend type-check, lint ratchet, production build'
Set-Location frontend
Native 'tsc --noEmit' { npx tsc --noEmit }
Set-Location $root
Native 'lint ratchet' { python ci/lint_ratchet.py }
Set-Location frontend
Native 'next build' { npm run build }
Set-Location $root

Step '3/6 backend focused suites (blockers + contracts)'
$focused = @(
    'tests/test_source_rate_limiter.py',
    'tests/test_rca_shutdown.py',
    'tests/test_runtime_mode.py',
    'tests/test_health_readiness.py',
    'tests/test_scheduler_single_owner.py',
    'tests/test_tenant_isolation.py',
    'tests/test_api_quota.py',
    'tests/test_auth_core.py',
    'tests/test_auth_service.py',
    'tests/test_auth_tenants.py',
    'tests/test_rca_service_unit.py',
    'tests/test_rca_lifecycle.py',
    'tests/test_rca_trigger.py',
    'tests/test_rca_activation.py',
    'tests/test_rca_recommendation_sop.py',
    'tests/test_dependency_intelligence.py',
    'tests/test_dependency_integration.py',
    'tests/test_topology_integration.py'
)
Set-Location backend
Native 'focused suites' { python -m pytest @focused -q }

Step '4/6 backend FULL suite'
Native 'full backend suite' { python -m pytest -q }
Set-Location $root

if ($WithContainers) {
    Step '5/6 production container builds (no secrets baked in)'
    Native 'backend image' { docker build -t hsdeep-core-ai-backend:ci backend }
    Native 'frontend image' { docker build -t hsdeep-core-ai-frontend:ci frontend }
}

if ($WithBrowser) {
    Step '6/6 browser regression (requires a seeded dev stack on :8000 and :3000)'
    Set-Location frontend
    Native 'portal authenticated harness' { node tests/portal-authenticated-incidents.cjs }
    Native 'portal responsive harness' { node tests/portal-responsive-validation.cjs }
    Set-Location $root
}

Step 'ALL GATES PASSED'
