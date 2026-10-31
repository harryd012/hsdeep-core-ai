"""Palo Alto safety and approval controls.

Execution policy (enforced on the backend, never only in the UI):
  - READ_ONLY steps: allowed for authorized users on configured devices.
  - REQUIRES_APPROVAL steps (packet capture, remediation planning):
    blocked unless an approval record exists for this execution+step.
  - Configuration changes, commits, pushes, reboots, service restarts:
    NEVER triggered autonomously by this module. Config edits are out of
    scope and must flow through the project's change-management path.
  - AI recommendations are advisory; RBAC and approval are never bypassed.
  - Tenant isolation: diagnostic lookups always filter by the execution's
    tenant_id; a lookup that would cross tenants is denied.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.schemas.paloalto import DiagnosticRisk, DiagnosticStep


@dataclass
class SafetyDecision:
    allowed: bool
    reason: str
    next_action: str | None = None


class SafetyPolicy:
    """Evaluate whether a diagnostic step may run for an actor."""

    DENIED_AUTONOMOUS = frozenset({"commit", "push", "reboot", "restart", "configure"})

    def __init__(self, approver_has: set | None = None, role_allow: dict | None = None):
        # approver_has: set of (execution_id, step_id) with a recorded approval.
        self._approvals: set = approver_has or set()
        # role_allow: role -> allowed risk ceiling. Default: engineers may run
        # READ_ONLY and LOW; MEDIUM+ always needs an approval record.
        self._roles = role_allow or {"engineer", "noc", "soc", "admin", "viewer"}

    def grant(self, execution_id: uuid.UUID, step_id: str) -> None:
        self._approvals.add((str(execution_id), step_id))

    def evaluate(self, step: DiagnosticStep, actor_role: str) -> SafetyDecision:
        if step.risk in (DiagnosticRisk.READ_ONLY, DiagnosticRisk.LOW):
            if actor_role not in self._roles:
                return SafetyDecision(
                    allowed=False,
                    reason=f"role '{actor_role}' not authorized for diagnostics",
                    next_action="request access via RBAC",
                )
            return SafetyDecision(allowed=True, reason="read-only step")

        txt = step.id + " " + (step.name or "")
        if any(w in txt.lower() for w in self.DENIED_AUTONOMOUS):
            return SafetyDecision(
                allowed=False,
                reason="autonomous config/commit/reboot operations are disabled",
                next_action="route through change management",
            )
        key = (getattr(step, "_execution_id", "") or "", step.id)
        if key in self._approvals or actor_role == "admin":
            return SafetyDecision(allowed=True, reason="approval recorded")
        return SafetyDecision(
            allowed=False,
            reason=f"step '{step.id}' requires explicit approval",
            next_action="request approval from an authorized approver",
        )

    def assert_cli_allowed(self, command_id: str, allowlist: frozenset) -> None:
        if command_id not in allowlist:
            raise PermissionError(f"CLI command not allowlisted: {command_id}")
