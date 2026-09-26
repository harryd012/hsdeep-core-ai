"""
ICMP action provider (Phase 1) - read-only reachability diagnostics.

REUSE, not duplication: the actual ping execution + parsing is the
ConnectivityCollector's proven implementation
(app/collectors/connectivity.py: _icmp_result / parse_ping_output -
stdlib-only, asyncio.to_thread, conservative parsing, no fabricated
values). This provider wraps that seam for the Action Plane so there is
exactly ONE ping implementation in the codebase.

Supported actions: PING, HEALTH_CHECK (both READ_ONLY).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.actions.base import (
    ActionNotSupported,
    ActionResult,
    ActionTarget,
    DeviceActionProvider,
    DeviceActionType,
    ActionProviderRegistry,
)

# Reused from the Collector Framework (same package family). Private-name
# import is deliberate: promoting these to a shared transport module is a
# future refactor; duplicating ping logic here would violate the
# "one implementation" rule.
from app.collectors.connectivity import _icmp_result  # noqa: PLC2701

logger = logging.getLogger(__name__)

DEFAULT_COUNT = 4
DEFAULT_TIMEOUT_SECONDS = 3.0
_MAX_COUNT = 10
_MAX_TIMEOUT_SECONDS = 10.0


@ActionProviderRegistry.register("icmp")
class IcmpActionProvider(DeviceActionProvider):
    display_name = "ICMP (ping)"
    implemented = True

    def supported_actions(self) -> frozenset[DeviceActionType]:
        return frozenset({DeviceActionType.PING, DeviceActionType.HEALTH_CHECK})

    async def execute(
        self,
        action: DeviceActionType,
        target: ActionTarget,
        params: dict[str, Any] | None = None,
    ) -> ActionResult:
        if not self.supports(action):
            raise ActionNotSupported(
                f"ICMP provider does not support action={action.value}"
            )
        address = target.address
        started = datetime.now(timezone.utc)
        if not address:
            return ActionResult(
                success=False,
                error="Device has no IP address or hostname to ping.",
                provider=self.provider_kind,
                started_at=started,
                completed_at=started,
            )

        p = params or {}
        count = self._clamp(int(p.get("count", DEFAULT_COUNT)), 1, _MAX_COUNT)
        timeout = self._clamp(
            float(p.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            0.5, _MAX_TIMEOUT_SECONDS,
        )

        try:
            icmp = await _icmp_result(address, count, timeout)
        except Exception as exc:  # noqa: BLE001 - device-level failure, never a 500
            logger.warning("ICMP action failed target=%s err=%s", address, exc)
            return ActionResult(
                success=False,
                error=f"Ping execution failed: {type(exc).__name__}",
                provider=self.provider_kind,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

        icmp["address"] = address
        icmp["probes"] = count
        # HEALTH_CHECK adds a coarse operational verdict on top of the raw
        # probe result - derived ONLY from the real response, never assumed.
        if action == DeviceActionType.HEALTH_CHECK:
            icmp["verdict"] = "reachable" if icmp.get("ok") else "unreachable"

        return ActionResult(
            success=bool(icmp.get("ok")),
            output=icmp,
            error=None if icmp.get("ok") else "No ICMP reply (host unreachable or filtered).",
            provider=self.provider_kind,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _clamp(value: int | float, lo: int | float, hi: int | float) -> int | float:
        return max(lo, min(hi, value))
