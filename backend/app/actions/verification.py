"""
Post-Action Verification (Phase 1 architecture).

"Never assume an action succeeded because the command returned success."

After a provider returns, the orchestrator runs an action-specific
verification plan that re-checks REAL device state and compares it to the
expected outcome. Only a passing verification moves a DeviceActionRequest
to `verified`; otherwise it stays `verification_failed` (and, for
state-changing actions in later phases, triggers the rollback path via
the existing RollbackJob model).

Phase 1 ships verification for the read-only diagnostics (re-probe /
response sanity). The plan table is the extension point: each new adapter
phase (SSH, vendor APIs, firmware) registers richer plans - e.g.
RESTART_SERVICE -> check interface up -> check traffic flowing -> check
errors stable - without changing the orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.actions.base import ActionTarget, DeviceActionType

# A verification step: (title, async probe) -> (passed: bool, detail: dict).
# The probe is supplied by the orchestrator (it owns the provider/transport
# instances); this module only defines plans and reduces results.
VerifyProbe = Callable[[], Awaitable[tuple[bool, dict[str, Any]]]]


@dataclass
class VerificationStepResult:
    title: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationOutcome:
    passed: bool
    steps: list[VerificationStepResult] = field(default_factory=list)
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verified_at": self.verified_at.isoformat(),
            "steps": [
                {"title": s.title, "passed": s.passed, "detail": s.detail}
                for s in self.steps
            ],
        }


async def run_verification(steps: list[tuple[str, VerifyProbe]]) -> VerificationOutcome:
    """Execute the plan's probes in order; all must pass. A probe raising
    counts as a failed step (never a crash, never an assumed pass)."""
    results: list[VerificationStepResult] = []
    for title, probe in steps:
        try:
            passed, detail = await probe()
        except Exception as exc:  # noqa: BLE001
            passed, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
        results.append(VerificationStepResult(title=title, passed=passed, detail=detail))
    return VerificationOutcome(
        passed=bool(results) and all(r.passed for r in results),
        steps=results,
    )


def plan_for(action: DeviceActionType, target: ActionTarget) -> list[str]:
    """The verification plan (step titles) for an action against a target.

    Read-only actions verify that the response reflects live state:
      * PING / HEALTH_CHECK -> an independent second probe must agree
        with the first (guards against a one-off fluke).
      * GET_* (SNMP)        -> the response carried identifying data
        (sysName/sysDescr/interfaces present), i.e. the device really
        answered rather than an empty/timeout being misread as success.
      * CISCO_QUERY_HEALTH / ARUBA_QUERY_HEALTH (vendor MIB) ->
        the response carried live health counters (CPU and/or memory),
        i.e. the vendor MIB really answered.

    State-changing actions get their full multi-step plans when their
    adapters ship (Phases 3-7); until then those actions cannot execute
    at all (policy denies unimplemented providers), so the fallback plan
    is intentionally unreachable for them.
    """
    if action in (DeviceActionType.PING, DeviceActionType.HEALTH_CHECK):
        return ["independent_reachability_recheck"]
    if action in (
        DeviceActionType.GET_STATUS,
        DeviceActionType.GET_INTERFACES,
        DeviceActionType.GET_FIRMWARE,
    ):
        return ["response_carries_real_device_data"]
    if action in (
        DeviceActionType.CISCO_QUERY_HEALTH,
        DeviceActionType.ARUBA_QUERY_HEALTH,
    ):
        return ["vendor_health_carries_real_device_data"]
    # Fallback for not-yet-executable actions (policy blocks them first).
    return ["provider_result_self_consistency"]
