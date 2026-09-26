"""
SNMP action provider (Phase 1) - read-only device diagnostics.

REUSE, not duplication: SNMP transport (pysnmp GET/WALK, v1/v2c/v3 auth,
timeouts/retries) is the SNMPCollector's proven implementation
(app/collectors/snmp.py). This provider drives that collector's transport
methods against the device's decrypted MonitoringSourceConnectionInfo so
there is exactly ONE SNMP implementation in the codebase.

Supported actions: GET_STATUS, GET_INTERFACES, GET_FIRMWARE (READ_ONLY).
GET_CONFIG_STATUS is deliberately NOT supported here yet - it needs
vendor-specific config OIDs; requesting it resolves to an honest
"not supported by provider" instead of fabricated output.
"""
from __future__ import annotations

import logging
import re
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
from app.collectors.snmp import SNMPCollector

logger = logging.getLogger(__name__)

# Standard MIB OIDs (same constants the collector walks).
_OID_SYS_DESCR = ".1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME = ".1.3.6.1.2.1.1.5.0"
_OID_SYS_UPTIME = ".1.3.6.1.2.1.1.3.0"
_OID_IF_DESCR = ".1.3.6.1.2.1.2.2.1.2"
_OID_IF_ADMIN_STATUS = ".1.3.6.1.2.1.2.2.1.7"
_OID_IF_OPER_STATUS = ".1.3.6.1.2.1.2.2.1.8"
_OID_IF_IN_OCTETS = ".1.3.6.1.2.1.2.2.1.10"
_OID_IF_OUT_OCTETS = ".1.3.6.1.2.1.2.2.1.16"
_OID_IF_IN_ERRORS = ".1.3.6.1.2.1.2.2.1.14"
_OID_IF_OUT_ERRORS = ".1.3.6.1.2.1.2.2.1.20"
_OID_IF_SPEED = ".1.3.6.1.2.1.2.2.1.5"

_OPER_STATUS = {"1": "up", "2": "down", "3": "testing", "4": "unknown", "5": "dormant", "6": "notPresent", "7": "lowerLayerDown"}
_ADMIN_STATUS = {"1": "up", "2": "down", "3": "testing"}

# Best-effort firmware/version extraction from sysDescr (conservative:
# returns None when no version-like token exists - never a guess).
_VERSION_RE = re.compile(
    r"(?:Version|version|Ver\.?|v)\s*[:=]?\s*([0-9]+(?:[.\-][0-9A-Za-z]+)+)"
)


def extract_firmware(sys_descr: str) -> str | None:
    """Pull a firmware/OS version token out of sysDescr, or None."""
    if not sys_descr:
        return None
    m = _VERSION_RE.search(sys_descr)
    return m.group(1) if m else None


@ActionProviderRegistry.register("snmp")
class SnmpActionProvider(DeviceActionProvider):
    display_name = "SNMP (v1/v2c/v3)"
    implemented = True

    def supported_actions(self) -> frozenset[DeviceActionType]:
        return frozenset({
            DeviceActionType.GET_STATUS,
            DeviceActionType.GET_INTERFACES,
            DeviceActionType.GET_FIRMWARE,
        })

    def _collector(self, target: ActionTarget) -> SNMPCollector:
        """The device's linked monitoring source supplies connection info
        (already decrypted by the capability resolver)."""
        if target.connection is None:
            raise ActionNotSupported(
                "Device has no linked SNMP monitoring source with credentials."
            )
        return SNMPCollector(source=target.connection)

    async def execute(
        self,
        action: DeviceActionType,
        target: ActionTarget,
        params: dict[str, Any] | None = None,
    ) -> ActionResult:
        if not self.supports(action):
            raise ActionNotSupported(
                f"SNMP provider does not support action={action.value} "
                "(GET_CONFIG_STATUS requires vendor-specific OIDs - not implemented)."
            )
        started = datetime.now(timezone.utc)
        try:
            collector = self._collector(target)
            if action == DeviceActionType.GET_STATUS:
                output = await self._get_status(collector)
            elif action == DeviceActionType.GET_INTERFACES:
                output = await self._get_interfaces(collector)
            else:
                output = await self._get_firmware(collector)
        except ActionNotSupported:
            raise
        except Exception as exc:  # noqa: BLE001 - device-level failure
            logger.warning("SNMP action failed device=%s err=%s", target.device_id, exc)
            return ActionResult(
                success=False,
                error=f"SNMP query failed: {type(exc).__name__}",
                provider=self.provider_kind,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

        ok = bool(output)
        return ActionResult(
            success=ok,
            output=output,
            error=None if ok else "SNMP: no response (unreachable, wrong community, or SNMP disabled).",
            provider=self.provider_kind,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )

    # -- action implementations ------------------------------------------

    async def _get_status(self, collector: SNMPCollector) -> dict[str, Any]:
        scalars = await collector._get(_OID_SYS_DESCR, _OID_SYS_NAME, _OID_SYS_UPTIME)  # noqa: SLF001
        if not scalars:
            return {}
        return {
            "sys_descr": scalars.get(_OID_SYS_DESCR, ""),
            "sys_name": scalars.get(_OID_SYS_NAME, ""),
            "uptime_raw": scalars.get(_OID_SYS_UPTIME, ""),
            "firmware": extract_firmware(scalars.get(_OID_SYS_DESCR, "")),
        }

    async def _get_firmware(self, collector: SNMPCollector) -> dict[str, Any]:
        scalars = await collector._get(_OID_SYS_DESCR)  # noqa: SLF001
        if not scalars:
            return {}
        descr = scalars.get(_OID_SYS_DESCR, "")
        return {"sys_descr": descr, "firmware": extract_firmware(descr)}

    async def _get_interfaces(self, collector: SNMPCollector) -> dict[str, Any]:
        columns: dict[str, dict[str, str]] = {}
        for key, base_oid in (
            ("descr", _OID_IF_DESCR),
            ("admin_status", _OID_IF_ADMIN_STATUS),
            ("oper_status", _OID_IF_OPER_STATUS),
            ("in_octets", _OID_IF_IN_OCTETS),
            ("out_octets", _OID_IF_OUT_OCTETS),
            ("in_errors", _OID_IF_IN_ERRORS),
            ("out_errors", _OID_IF_OUT_ERRORS),
            ("speed_bps", _OID_IF_SPEED),
        ):
            rows = await collector._walk(base_oid)  # noqa: SLF001
            columns[key] = {oid.split(".")[-1]: val for oid, val in rows}

        descrs = columns.get("descr", {})
        if not descrs:
            return {}

        interfaces = []
        for if_index, name in descrs.items():
            oper = columns.get("oper_status", {}).get(if_index, "")
            admin = columns.get("admin_status", {}).get(if_index, "")
            interfaces.append({
                "index": if_index,
                "name": name,
                "oper_status": _OPER_STATUS.get(oper, oper or "unknown"),
                "admin_status": _ADMIN_STATUS.get(admin, admin or "unknown"),
                "speed_bps": columns.get("speed_bps", {}).get(if_index),
                "in_octets": columns.get("in_octets", {}).get(if_index),
                "out_octets": columns.get("out_octets", {}).get(if_index),
                "in_errors": columns.get("in_errors", {}).get(if_index),
                "out_errors": columns.get("out_errors", {}).get(if_index),
            })
        return {"interfaces": interfaces, "count": len(interfaces)}
