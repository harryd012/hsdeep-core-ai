# Automation Action Bridge

Status: IMPLEMENTED (Phase 3 sprint, verified by 46 unit tests + 180-test
regression suite).

## Architecture (actual)

    SOP / Automation workflow
        |  step = {action, target_device_id, parameters, description}
        v
    AutomationRunner (session_factory path)
        |  DatabaseStepHandler  <- production default
        v
    DeviceActionService.request_action(...)   <- SINGLE execution boundary
        |  RBAC (device_actions.execute) enforced upstream at the API
        |  tenant-scoped device + monitoring-source resolution
        |  policy evaluation (classification, capability, maintenance window)
        |  approval routing (ApprovalRequest / ApprovalHistory)
        v
    ActionProviderRegistry -> vendor provider (PRTG / ICMP / SNMP)
        v
    Verification (semantic re-check, never HTTP-200-alone)
        v
    Audit (audit_events via AuditService)

`MockStepHandler` is retained ONLY as a test double and for the legacy
explicit-repos path. The production `session_factory` path wires
`DatabaseStepHandler` automatically.

## StepHandler contract

The existing protocol is UNCHANGED:

    async def execute(step: dict, context: dict) -> StepResult

`StepResult` gained one additive field, `status`, mapping the
DeviceActionRequest lifecycle to the runner:

| DeviceActionRequest.status | StepResult.success | StepResult.status   |
|----------------------------|--------------------|---------------------|
| verified                   | True               | succeeded           |
| pending                    | True               | pending             |
| policy_denied              | False              | policy_denied       |
| awaiting_approval          | False              | awaiting_approval   |
| anything else              | False              | (the raw status)    |

CHANGE_REQUIRING_APPROVAL steps NEVER auto-execute. The bridge returns
`awaiting_approval` with the `approval_request_id`; the existing gated-step
machinery in the runner pauses the SOP and the approval is resolved through
the existing DeviceActionService approve endpoint. The bridge never grants,
denies, or bypasses approval.

## Action resolution

Only typed actions from `ACTION_CATALOG` / `DeviceActionType` are accepted.
Unknown actions, free-text actions, shell fragments, and URLs are rejected
BEFORE any service call (fail closed, `state.calls == []`).

## Identity propagation

- tenant:  `context["tenant_id"]` -> `request_action(tenant_id=...)`;
  DeviceActionService re-scopes the device to that tenant, so cross-tenant
  targeting fails closed.
- actor:   constructor `actor_id`, else `context["actor_id"]` (ignored when
  not a valid UUID - the bridge never spoofs "system"/"automation" as an
  actor). `None` stays `None`.
- correlation: `context["correlation_id"]` -> `request_action(correlation_id=...)`.
  `DeviceActionService.request_action` now accepts an OPTIONAL
  `correlation_id`; when omitted (API route path) a fresh one is generated
  exactly as before, so the HTTP API behaviour is unchanged.

## Security

- The bridge module never imports providers, httpx/requests, subprocess, or
  os.system (enforced by tests).
- Credentials never flow through the bridge; only workflow params do.
- Approval cannot be bypassed from a workflow.
- RBAC is enforced at the API layer (device_actions.execute/.read/.approve)
  and the service layer; automation inherits the same gates.

## Error behaviour

DeviceActionError, missing step fields, unknown actions, and unexpected
exceptions all map to `StepResult(success=False, status="failed")` with a
safe error string. Failures are never converted to successes.

## Tests

tests/test_automation_runner.py: 46 tests
- construction, READ_ONLY dispatch (PING / PRTG reads), approval dispatch,
  policy denial, invalid steps (missing/blank/unknown/shell/URL actions),
  tenant isolation + cross-tenant fail-closed, actor propagation (context,
  constructor, never-spoofed, invalid-UUID ignored), correlation propagation
  (to service, into motivation, echoed in output, generated when absent),
  credential hygiene (params only; no provider imports; no subprocess/http
  imports), MockStepHandler behaviour, AutomationRunner handler selection,
  StepResult mapping incl. unknown-status never-success.

Regression: 180 tests pass across device action plane (44), PRTG provider
(22), automation runner (46), PRTG collector, tenant isolation, security
boundary.

## Known limitations

- Live PRTG integration remains BLOCKED (no development PRTG tenant).
- The `resume`/`approve` path for approval-flow SOPs (resuming an SOP after
  the DeviceActionService approval is granted) is handled by the existing
  SOPExecution gated-step machinery; wiring the DeviceActionService approval
  resolution INTO the SOP resume endpoint is future work.
- Pre-existing pytest-asyncio harness defect still blocks DB-fixture-heavy
  suites (test_repositories, test_task_service, ...): asyncpg "attached to a
  different loop" from the deprecated session-scoped event_loop override in
  conftest.py. Unrelated to this bridge (those suites do not import it).
