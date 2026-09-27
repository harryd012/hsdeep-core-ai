"""Aruba (ArubaOS / WLSX) action provider (Phase 3) - vendor MIB health.

REUSE, not duplication: connection resolution, SNMP transport, timeouts and
the honest ActionResult envelope come from
`providers/vendor_snmp.VendorSnmpHealthProvider`. This module supplies ONLY
the Aruba OID map and the reading parser.

Why a vendor adapter is genuinely required here: ArubaOS exposes controller
CPU load and memory in WLSX-SYSTEMEXT-MIB, not in standard MIB-II.

OIDs (verified against WLSX-SYSTEMEXT-MIB, base .1.3.6.1.4.1.14823.2.2.1.2):

  wlsxSysExtProcessorTable   .1.3.6.1.4.1.14823.2.2.1.2.1.13
  wlsxSysExtProcessorEntry   .1.3.6.1.4.1.14823.2.2.1.2.1.13.1
    sysExtProcessorDescr     .1.3.6.1.4.1.14823.2.2.1.2.1.13.1.2
    sysExtProcessorLoad      .1.3.6.1.4.1.14823.2.2.1.2.1.13.1.3  (percent)

  wlsxSysExtMemoryTable      .1.3.6.1.4.1.14823.2.2.1.2.1.15
  wlsxSysExtMemoryEntry      .1.3.6.1.4.1.14823.2.2.1.2.1.15.1
    sysExtMemorySize         .1.3.6.1.4.1.14823.2.2.1.2.1.15.1.2  (KB)
    sysExtMemoryUsed         .1.3.6.1.4.1.14823.2.2.1.2.1.15.1.3  (KB)

  wlsxSysExtStorageTable     .1.3.6.1.4.1.14823.2.2.1.2.1.14  (RAM fallback)
    sysExtStorageType        .1.3.6.1.4.1.14823.2.2.1.2.1.14.1.2  (1=ram)
    sysExtStorageSize        .1.3.6.1.4.1.14823.2.2.1.2.1.14.1.3  (MB)
    sysExtStorageUsed        .1.3.6.1.4.1.14823.2.2.1.2.1.14.1.4  (MB)
    sysExtStorageName        .1.3.6.1.4.1.14823.2.2.1.2.1.14.1.5

Supported actions: ARUBA_QUERY_HEALTH (READ_ONLY).
"""
from __future__ import annotations

import logging
from typing import Any

from app.actions.base import (
    ActionNotSupported,
    ActionProviderRegistry,
    DeviceActionType,
)
from app.collectors.snmp import SNMPCollector

from app.actions.providers.vendor_snmp import (
    VendorSnmpHealthProvider,
    to_int,
    walk_column,
)

logger = logging.getLogger(__name__)

# -- WLSX-SYSTEMEXT-MIB: processors ----------------------------------------
_OID_PROC_DESCR = ".1.3.6.1.4.1.14823.2.2.1.2.1.13.1.2"
_OID_PROC_LOAD = ".1.3.6.1.4.1.14823.2.2.1.2.1.13.1.3"

# -- WLSX-SYSTEMEXT-MIB: controller memory (KB) ----------------------------
_OID_MEM_SIZE = ".1.3.6.1.4.1.14823.2.2.1.2.1.15.1.2"
_OID_MEM_USED = ".1.3.6.1.4.1.14823.2.2.1.2.1.15.1.3"

# -- WLSX-SYSTEMEXT-MIB: storage (MB) - used as a RAM fallback -------------
_OID_STOR_TYPE = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.2"
_OID_STOR_SIZE = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.3"
_OID_STOR_USED = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.4"
_OID_STOR_NAME = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.5"

# sysExtStorageType enum
_STORAGE_RAM = "1"
_STORAGE_TYPE_NAMES = {"1": "ram", "2": "flashMemory"}


@ActionProviderRegistry.register("vendor:aruba")
class ArubaActionProvider(VendorSnmpHealthProvider):
    display_name = "Aruba ArubaOS (SNMP)"
    implemented = True

    health_actions = frozenset({DeviceActionType.ARUBA_QUERY_HEALTH})

    async def read_health(
        self, collector: SNMPCollector, action: DeviceActionType
    ) -> dict[str, Any]:
        """Read controller CPU load + memory usage from WLSX-SYSTEMEXT-MIB.

        The shared envelope hands over the requested action so one provider can
        serve several device actions through one transport (see the Cisco
        provider). Aruba implements exactly one action, so anything else is a
        routing mistake, not a device failure - it raises ActionNotSupported
        instead of being reported as an SNMP error.

        Memory is read from the dedicated memory table; when a controller
        does not populate it, the RAM row of the storage table is used
        instead. Units differ (KB vs MB) and are reported explicitly so a
        caller can never misread the scale.
        """
        if action is not DeviceActionType.ARUBA_QUERY_HEALTH:
            raise ActionNotSupported(
                f"Aruba provider does not support action={action.value}"
            )
        descr = walk_column({}, await collector._walk(_OID_PROC_DESCR))  # noqa: SLF001
        load = walk_column({}, await collector._walk(_OID_PROC_LOAD))  # noqa: SLF001

        processors: list[dict[str, Any]] = []
        for index in sorted(set(descr) | set(load)):
            pct = to_int(load.get(index))
            text = descr.get(index, "")
            if pct is None and not text:
                continue
            entry: dict[str, Any] = {"index": index, "descr": text}
            if pct is not None:
                entry["load_pct"] = pct
            processors.append(entry)

        memory = await self._read_memory(collector)
        if not processors and memory is None:
            return {}

        return {
            "vendor": "aruba",
            "processors": processors,
            "processor_count": len(processors),
            "memory": memory,
        }

    async def _read_memory(self, collector: SNMPCollector) -> dict[str, Any] | None:
        """Dedicated memory table first, storage-RAM fallback second."""
        scalars = await collector._get(_OID_MEM_SIZE, _OID_MEM_USED)  # noqa: SLF001
        total_kb = to_int(scalars.get(_OID_MEM_SIZE))
        used_kb = to_int(scalars.get(_OID_MEM_USED))
        if total_kb is not None or used_kb is not None:
            entry: dict[str, Any] = {
                "source": "wlsxSysExtMemoryTable",
                "unit": "KB",
                "total": total_kb,
                "used": used_kb,
                "free": (
                    total_kb - used_kb
                    if total_kb is not None and used_kb is not None
                    else None
                ),
            }
            if total_kb and used_kb is not None:
                entry["used_pct"] = round(used_kb * 100.0 / total_kb, 1)
            return entry
        return await self._read_storage_ram(collector)

    async def _read_storage_ram(self, collector: SNMPCollector) -> dict[str, Any] | None:
        """Fallback: the RAM row of wlsxSysExtStorageTable (values in MB)."""
        types = walk_column({}, await collector._walk(_OID_STOR_TYPE))  # noqa: SLF001
        sizes = walk_column({}, await collector._walk(_OID_STOR_SIZE))  # noqa: SLF001
        used = walk_column({}, await collector._walk(_OID_STOR_USED))  # noqa: SLF001
        names = walk_column({}, await collector._walk(_OID_STOR_NAME))  # noqa: SLF001

        for index in sorted(set(types) | set(sizes) | set(used)):
            is_ram = types.get(index) == _STORAGE_RAM
            label = (names.get(index) or "").lower()
            if not is_ram and "ram" not in label and "memory" not in label:
                continue
            total_mb = to_int(sizes.get(index))
            used_mb = to_int(used.get(index))
            if total_mb is None and used_mb is None:
                continue
            entry = {
                "source": "wlsxSysExtStorageTable",
                "unit": "MB",
                "storage_type": _STORAGE_TYPE_NAMES.get(types.get(index, ""), "ram"),
                "name": names.get(index, ""),
                "total": total_mb,
                "used": used_mb,
                "free": (
                    total_mb - used_mb
                    if total_mb is not None and used_mb is not None
                    else None
                ),
            }
            if total_mb and used_mb is not None:
                entry["used_pct"] = round(used_mb * 100.0 / total_mb, 1)
            return entry
        return None