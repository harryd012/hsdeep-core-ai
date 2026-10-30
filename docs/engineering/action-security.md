# Action Security Model

Status: IMPLEMENTED (enforced by code + tests).

## Credential protection

- Device/monitoring credentials are stored encrypted (Fernet) in
  `MonitoringSource` columns. Plaintext never touches disk.
- `decrypt_connection_info` decrypts per-execution, server-side only.
- Decrypted values NEVER appear in: API responses, ActionResult.output,
  ActionResult.error, logs, audit context, or exception text
  (`_safe_error` redacts passhash/username/token patterns).
- The frontend never receives infrastructure credentials.

## URL / command safety

- PRTG transport paths are whitelisted constants (`/api/table.json`,
  `/api/pause.htm`, `/api/resume.htm`). No caller - LLM, workflow, or API -
  can supply an arbitrary URL.
- No shell, subprocess, or os.system anywhere in the action plane.
- Actions are typed (`DeviceActionType`) and catalog-gated
  (`ACTION_CATALOG`); unknown or free-text actions are rejected before
  service dispatch.

## Classification routing (policy engine)

| Classification            | Behaviour                                        |
|---------------------------|--------------------------------------------------|
| READ_ONLY                 | auto-execute when tenant policy allows           |
| LOW_RISK                  | approval unless tenant explicitly opts in        |
| CHANGE_REQUIRING_APPROVAL | human approval always                            |
| HIGH_RISK                 | approval + elevated role + active maint. window  |
| DESTRUCTIVE               | approval + elevated role; never auto             |

Unimplemented adapters are policy-denied up front with an honest reason -
never a mocked execution.

## Approval integrity

- Approvals ride the existing ApprovalRequest/ApprovalHistory tables.
- The automation bridge cannot approve, deny, or bypass: an approval-flow
  step terminates in `awaiting_approval` until a human resolves it through
  the DeviceActionService approve API (RBAC: device_actions.approve).

## Tenant isolation

- Every request is scoped by `tenant_id`; devices, monitoring sources,
  capabilities, action requests, approvals and audit rows are tenant-scoped.
- Cross-tenant device/action IDs never resolve (fail closed).
- The automation bridge forwards the WORKFLOW tenant; the service
  re-validates. It never trusts payload tenant ids over authoritative
  context.

## Audit

`audit_events` records request/approval/execution/verification with
actor, correlation_id, policy reason - no secrets.

## AI boundary

The LLM may RECOMMEND actions (motivation field). It can never execute:
execution is only reachable through DeviceActionService -> policy ->
approval -> provider.
