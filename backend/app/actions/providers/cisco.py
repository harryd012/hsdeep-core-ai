"""Cisco IOS action provider (Phase 3) - vendor MIB health counters.

REUSE, not duplication: connection resolution, the SNMP transport, timeouts,
and the honest ActionResult envelope all come from
`providers/vendor_snmp.VendorSnmpHealthProvider` (which itself drives the
single SNMPCollector transport). This module supplies ONLY the Cisco OID map
and the reading parser.

Why a vendor adapter is genuinely required here: CPU utilisation and
memory-pool usage are NOT in standard MIB-II. They live in Cisco's own MIBs,
so no vendor-neutral provider can honestly answer this action.

OIDs (verified against CISCO-PROCESS-MIB / CISCO-MEMORY-POOL-MIB):

  CISCO-PROCESS-MIB
    cpmCPUTotalTable            .1.3.6.1.4.1.9.9.109.1.1.1
    cpmCPUTotalEntry            .1.3.6.1.4.1.9.9.109.1.1.1.1
    cpmCPUTotalPhysicalIndex    .1.3.6.1.4.1.9.9.109.1.1.1.1.2
    cpmCPUTotal1minRev          .1.3.6.1.4.1.9.9.109.1.1.1.1.7   (percent 0-100)
    cpmCPUTotal5minRev          .1.3.6.1.4.1.9.9.109.1.1.1.1.8   (percent 0-100)

  CISCO-MEMORY-POOL-MIB
    ciscoMemoryPoolTable        .1.3.6.1.4.1.9.9.48.1.1
    ciscoMemoryPoolEntry        .1.3.6.1.4.1.9.9.48.1.1.1
    ciscoMemoryPoolName         .1.3.6.1.4.1.9.9.48.1.1.1.2
    ciscoMemoryPoolUsed         .1.3.6.1.4.1.9.9.48.1.1.1.5   (bytes)
    ciscoMemoryPoolFree         .1.3.6.1.4.1.9.9.48.1.1.1.6   (bytes)

Supported actions: CISCO_QUERY_HEALTH (READ_ONLY).
"""
from __future__ import annotations

import logging
from typing import Any

from app.actions.base import (
    ActionProviderRegistry,
    DeviceActionType,
)
from app.collectors.snmp import SNMPCollector

from app.actions.providers.vendor_snmp import (
    VendorSnmpHealthProvider,
    to_int,
    walk_column,
    walk_composite,
)

logger = logging.getLogger(__name__)

# -- CISCO-PROCESS-MIB ------------------------------------------------------
_OID_CPU_1MIN_REV = ".1.3.6.1.4.1.9.9.109.1.1.1.1.7"
_OID_CPU_5MIN_REV = ".1.3.6.1.4.1.9.9.109.1.1.1.1.8"

# -- CISCO-MEMORY-POOL-MIB --------------------------------------------------
_OID_MEM_NAME = ".1.3.6.1.4.1.9.9.48.1.1.1.2"
_OID_MEM_USED = ".1.3.6.1.4.1.9.9.48.1.1.1.5"
_OID_MEM_FREE = ".1.3.6.1.4.1.9.9.48.1.1.1.6"

# -- per-process CPU + memory (CISCO-PROCESS-MIB cpmProcessExtRevTable) -----
_OID_PROC_NAME = ".1.3.6.1.4.1.9.9.109.1.2.1.2"          # cpmProcessName
_OID_PROC_UTIL_5SEC_REV = ".1.3.6.1.4.1.9.9.109.1.2.1.1.5"  # cpmProcExtUtil5SecRev
_OID_PROC_UTIL_1MIN_REV = ".1.3.6.1.4.1.9.9.109.1.2.1.1.6"  # cpmProcExtUtil1MinRev
_OID_PROC_UTIL_5MIN_REV = ".1.3.6.1.4.1.9.9.109.1.2.1.1.7"  # cpmProcExtUtil5MinRev
_OID_PROC_MEM_ALLOC_REV = ".1.3.6.1.4.1.9.9.109.1.2.1.1.1"  # cpmProcExtMemAllocatedRev
_OID_PROC_MEM_FREED_REV = ".1.3.6.1.4.1.9.9.109.1.2.1.1.2"  # cpmProcExtMemFreedRev


@ActionProviderRegistry.register("vendor:cisco")
class CiscoActionProvider(VendorSnmpHealthProvider):
    display_name = "Cisco IOS (SNMP)"
    implemented = True

    health_actions = frozenset({
    DeviceActionType.CISCO_QUERY_HEALTH,
    DeviceActionType.CISCO_QUERY_PROCESSES,
})

    async def read_health(
        self, collector: SNMPCollector, action: DeviceActionType
    ) -> dict[str, Any]:
        """Read the requested Cisco vendor-MIB view.

        * ``CISCO_QUERY_HEALTH`` - overall CPU totals (CISCO-PROCESS-MIB
          cpmCPUTotal*) + memory pools (CISCO-MEMORY-POOL-MIB).
        * ``CISCO_QUERY_PROCESSES`` - per-process CPU + memory from the
          cpmProcessExtRev table, labeled via cpmProcessName and sorted by
          CPU descending so the top-talkers appear first.
        """
        if action is DeviceActionType.CISCO_QUERY_HEALTH:
            return await self._read_health(collector)
        if action is DeviceActionType.CISCO_QUERY_PROCESSES:
            return await self._read_processes(collector)
        raise ActionNotSupported(
            f"Cisco provider does not support action={action.value}"
        )

    async def _read_health(self, collector: SNMPCollector) -> dict[str, Any]:
        cpu_1min_rows = await collector._walk(_OID_CPU_1MIN_REV)  # noqa: SLF001
        cpu_5min_rows = await collector._walk(_OID_CPU_5MIN_REV)  # noqa: SLF001
        mem_name_rows = await collector._walk(_OID_MEM_NAME)  # noqa: SLF001
        mem_used_rows = await collector._walk(_OID_MEM_USED)  # noqa: SLF001
        mem_free_rows = await collector._walk(_OID_MEM_FREE)  # noqa: SLF001

        cpu_1min = walk_column({}, cpu_1min_rows)
        cpu_5min = walk_column({}, cpu_5min_rows)

        cpus: list[dict[str, Any]] = []
        for index in sorted(set(cpu_1min) | set(cpu_5min)):
            entry: dict[str, Any] = {"index": index}
            one = to_int(cpu_1min.get(index))
            five = to_int(cpu_5min.get(index))
            if one is not None:
                entry["cpu_1min_pct"] = one
            if five is not None:
                entry["cpu_5min_pct"] = five
            if len(entry) > 1:
                cpus.append(entry)

        pool_names = walk_column({}, mem_name_rows)
        pool_used = walk_column({}, mem_used_rows)
        pool_free = walk_column({}, mem_free_rows)

        memory_pools: list[dict[str, Any]] = []
        for index in sorted(set(pool_used) | set(pool_free) | set(pool_names)):
            used = to_int(pool_used.get(index))
            free = to_int(pool_free.get(index))
            entry = {
                "index": index,
                "name": pool_names.get(index, ""),
                "used_bytes": used,
                "free_bytes": free,
            }
            if used is not None and free is not None and (used + free) > 0:
                entry["used_pct"] = round(used * 100.0 / (used + free), 1)
            if used is not None or free is not None or entry["name"]:
                memory_pools.append(entry)

        if not cpus and not memory_pools:
            return {}

        return {
            "vendor": "cisco",
            "cpus": cpus,
            "cpu_count": len(cpus),
            "memory_pools": memory_pools,
            "memory_pool_count": len(memory_pools),
        }

    async def _read_processes(self, collector: SNMPCollector) -> dict[str, Any]:
        """Per-process CPU + memory from cpmProcessExtRevTable, labeled via
        cpmProcessName, sorted by CPU desc (5-sec > 1-min > 5-min).
        """
        name_rows = await collector._walk(_OID_PROC_NAME)  # noqa: SLF001
        util5 = walk_composite(  # noqa: SLF001
            await collector._walk(_OID_PROC_UTIL_5SEC_REV), arcs=2)
        util1 = walk_composite(  # noqa: SLF001
            await collector._walk(_OID_PROC_UTIL_1MIN_REV), arcs=2)
        util5m = walk_composite(  # noqa: SLF001
            await collector._walk(_OID_PROC_UTIL_5MIN_REV), arcs=2)
        alloc = walk_composite(  # noqa: SLF001
            await collector._walk(_OID_PROC_MEM_ALLOC_REV), arcs=2)
        freed = walk_composite(  # noqa: SLF001
            await collector._walk(_OID_PROC_MEM_FREED_REV), arcs=2)

        names = walk_composite(name_rows, arcs=2)
        keys = sorted(set(names) | set(util5) | set(util1)
                      | set(util5m) | set(alloc) | set(freed))
        processes: list[dict[str, Any]] = []
        for key in keys:
            index, pid = key.rsplit(".", 1)
            cpu5 = to_int(util5.get(key))
            cpu1 = to_int(util1.get(key))
            cpu5m = to_int(util5m.get(key))
            a = to_int(alloc.get(key))
            f = to_int(freed.get(key))
            entry: dict[str, Any] = {"index": index, "pid": pid,
                                     "name": names.get(key, "")}
            if cpu5 is not None:
                entry["cpu_5sec_pct"] = cpu5
            if cpu1 is not None:
                entry["cpu_1min_pct"] = cpu1
            if cpu5m is not None:
                entry["cpu_5min_pct"] = cpu5m
            if a is not None:
                entry["mem_allocated_bytes"] = a
            if f is not None:
                entry["mem_freed_bytes"] = f
            if a is not None and f is not None:
                net = a - f
                entry["mem_net_bytes"] = net
                if a > 0:
                    entry["mem_net_pct_of_allocated"] = round(net * 100.0 / a, 1)
            processes.append(entry)

        if not processes:
            return {}

        def _sort_key(p: dict[str, Any]) -> tuple:
            five = p.get("cpu_5sec_pct")
            one = p.get("cpu_1min_pct")
            fivem = p.get("cpu_5min_pct")
            if five is not None:
                primary = (0, five)
            elif one is not None:
                primary = (1, one)
            elif fivem is not None:
                primary = (2, fivem)
            else:
                primary = (3, 0)
            return primary + (p.get("name", ""),)

        processes.sort(key=_sort_key, reverse=True)
        return {
            "vendor": "cisco",
            "processes": processes,
            "process_count": len(processes),
        }
