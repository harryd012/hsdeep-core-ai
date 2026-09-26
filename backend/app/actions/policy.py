"""
Action Policy Engine - the gate between a requested action and execution.

Enforces, in order:
  1. The action exists in ACTION_CATALOG (no ad-hoc action strings).
  2. The device capability satisfies the action's required_capability.
  3. A provider for the action's transport is registered AND implemented
     (an unimplemented adapter is policy-denied up front with an honest
     reason - never a surprise at execution time).
  4. The risk classification routes the request:
       READ_ONLY                 -> auto-execute permitted
       LOW_RISK                  -> auto only when tenant policy allows
       CHANGE_REQUIRING_APPROVAL -> human approval required
       HIGH_RISK                 -> approval + elevated role + an active
                                    maintenance window covering the device
       DESTRUCTIVE               -> approval + elevated role; NEVER auto

Permission checking itself (device_actions.execute / .approve) is done by
the route layer via require_permission - the policy engine receives the
already-authenticated principal and only makes the classification-driven
decision, keeping it a pure, unit-testable function.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.actions.base import (
    ACTION_CATALOG,
    IMPLEMENTED_ACTIONS,
    ActionClassification,
    ActionProviderRegistry,
    DeviceActionType,
)

# Tenant policy override keys (defaults are conservative).
DEFAULT_TENANT_POLICY: dict[str, Any] = {
    "allow_auto_low_risk": False,   # LOW_RISK needs explicit tenant opt-in
    "allow_auto_read_only": True,
}

# Roles allowed to approve HIGH_RISK / DESTRUCTIVE actions.
ELEVATED_APPROVAL_ROLES: frozenset[str] = frozenset({"Owner", "Admin"})


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of evaluating one action request."""
    allowed: bool                     # may the request proceed at all?
    auto_execute: bool                # execute now without human approval?
    requires_approval: bool
    required_role: str | None         # elevated role needed to approve
    maintenance_window_required: bool
    reason: str

    @property
    def status(self) -> str:
        """The DeviceActionRequest.status this decision maps to."""
        if not self.allowed:
            return "policy_denied"
        if self.requires_approval:
            return "awaiting_approval"
        return "pending"


def evaluate_action_policy(
    *,
    action: DeviceActionType,
    capabilities: dict[str, bool | None],
    tenant_policy: dict[str, Any] | None = None,
    maintenance_windows: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Pure policy evaluation - no I/O, no DB, fully unit-testable.

    `capabilities` is the device's resolved capability map (explicit row
    overlaid on derived defaults); `maintenance_windows` is a list of
    {device_id?, site_id?, starts_at, ends_at} dicts already scoped to the
    tenant (the service layer loads them; this function only reasons).
    """
    spec = ACTION_CATALOG.get(action)
    if spec is None:
        return PolicyDecision(
            allowed=False, auto_execute=False, requires_approval=False,
            required_role=None, maintenance_window_required=False,
            reason=f"Unknown action {action!r} - not in the action catalog.",
        )

    # 2. Capability gate - unknown (None) or False both deny. Never guess.
    cap = capabilities.get(spec.required_capability)
    if not cap:
        return PolicyDecision(
            allowed=False, auto_execute=False, requires_approval=False,
            required_role=None, maintenance_window_required=False,
            reason=(
                f"Device does not support {action.value}: capability "
                f"'{spec.required_capability}' is "
                f"{'unknown' if cap is None else 'disabled'}."
            ),
        )

    # 3. Provider availability gate (honest up-front reporting).
    # This is a TWO-LEVEL check: the action must be in IMPLEMENTED_ACTIONS
    # (an adapter for it actually shipped) AND the registered provider for
    # its transport must confirm supports(action). Checking only transport
    # registration would wrongly report e.g. GET_CONFIG_STATUS as available
    # because it shares the "snmp" kind with the implemented read actions.
    if action not in IMPLEMENTED_ACTIONS:
        return PolicyDecision(
            allowed=False, auto_execute=False, requires_approval=False,
            required_role=None, maintenance_window_required=False,
            reason=(
                f"Action {action.value} is catalogued but its adapter has "
                "not shipped (honest unavailability, never a mock execution)."
            ),
        )
    provider_cls = ActionProviderRegistry.try_get(spec.provider_kind)
    provider_ok = False
    if provider_cls is not None and provider_cls.implemented:
        try:
            provider_ok = provider_cls().supports(action)
        except Exception:  # noqa: BLE001 - constructor hiccup = unavailable
            provider_ok = False
    if not provider_ok:
        return PolicyDecision(
            allowed=False, auto_execute=False, requires_approval=False,
            required_role=None, maintenance_window_required=False,
            reason=(
                f"No implemented provider supports {action.value} on transport "
                f"'{spec.provider_kind}' yet - unavailable, never mocked."
            ),
        )

    return _route_by_classification(
        spec=spec,
        tenant_policy=tenant_policy,
        maintenance_windows=maintenance_windows or [],
        now=now or datetime.now(timezone.utc),
    )


def _route_by_classification(
    *,
    spec: Any,
    tenant_policy: dict[str, Any] | None,
    maintenance_windows: list[dict[str, Any]],
    now: datetime,
) -> PolicyDecision:
    policy = {**DEFAULT_TENANT_POLICY, **(tenant_policy or {})}

    if spec.classification == ActionClassification.READ_ONLY:
        if policy.get("allow_auto_read_only", True):
            return PolicyDecision(
                allowed=True, auto_execute=True, requires_approval=False,
                required_role=None, maintenance_window_required=False,
                reason="Read-only diagnostic; tenant policy permits automatic execution.",
            )
        return PolicyDecision(
            allowed=True, auto_execute=False, requires_approval=True,
            required_role=None, maintenance_window_required=False,
            reason="Read-only diagnostic, but tenant policy requires approval for all actions.",
        )

    if spec.classification == ActionClassification.LOW_RISK:
        if policy.get("allow_auto_low_risk", False):
            return PolicyDecision(
                allowed=True, auto_execute=True, requires_approval=False,
                required_role=None, maintenance_window_required=False,
                reason="Low-risk change; tenant policy explicitly allows automatic execution.",
            )
        return PolicyDecision(
            allowed=True, auto_execute=False, requires_approval=True,
            required_role=None, maintenance_window_required=False,
            reason="Low-risk change requires approval (tenant policy does not allow auto-execution).",
        )

    if spec.classification == ActionClassification.CHANGE_REQUIRING_APPROVAL:
        return PolicyDecision(
            allowed=True, auto_execute=False, requires_approval=True,
            required_role=None, maintenance_window_required=False,
            reason="State-changing action requires human approval.",
        )

    # HIGH_RISK / DESTRUCTIVE: approval + elevated role + (high-risk)
    # an active maintenance window that covers the device.
    window_required = spec.classification == ActionClassification.HIGH_RISK
    if window_required and not _in_maintenance_window(maintenance_windows, now):
        return PolicyDecision(
            allowed=False, auto_execute=False, requires_approval=False,
            required_role="Admin", maintenance_window_required=True,
            reason=(
                f"{spec.action.value} is high-risk and requires an active "
                "maintenance window covering this device - none is active now."
            ),
        )

    return PolicyDecision(
        allowed=True, auto_execute=False, requires_approval=True,
        required_role="Admin", maintenance_window_required=window_required,
        reason=(
            f"{spec.classification.value} action requires approval by an "
            "elevated role (Owner/Admin)."
        ),
    )


def _in_maintenance_window(windows: list[dict[str, Any]], now: datetime) -> bool:
    """True when any window's [starts_at, ends_at] contains `now`.

    Accepts datetime or ISO-string bounds (the service layer passes rows
    serialized or live - both are fine for a pure check).
    """
    for w in windows:
        starts, ends = w.get("starts_at"), w.get("ends_at")
        if starts is None or ends is None:
            continue
        if isinstance(starts, str):
            starts = datetime.fromisoformat(starts)
        if isinstance(ends, str):
            ends = datetime.fromisoformat(ends)
        if starts <= now <= ends:
            return True
    return False


def catalog_for_api() -> list[dict[str, Any]]:
    """ACTION_CATALOG serialized for GET /api/device-actions/catalog so the
    frontend NEVER hard-codes action metadata.

    `implemented` is per-ACTION (backed by IMPLEMENTED_ACTIONS + provider
    supports()), not per-transport: several unimplemented actions share a
    shipped transport kind (e.g. GET_CONFIG_STATUS is "snmp" but has no
    adapter - it must report implemented=False)."""
    out: list[dict[str, Any]] = []
    for action, spec in ACTION_CATALOG.items():
        out.append({
            "action": spec.action.value,
            "classification": spec.classification.value,
            "provider_kind": spec.provider_kind,
            "required_capability": spec.required_capability,
            "description": spec.description,
            "implemented": action in IMPLEMENTED_ACTIONS,
        })
    return out
