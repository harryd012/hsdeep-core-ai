"""
Firewall tables over SNMP (firewall gateway, docs/architecture/firewall-gateway.md).

The generic fallback for firewalls without a native API adapter. The vendor is
chosen by the device's own sysObjectID, never guessed from a name:

  - Fortinet FortiGate (FORTINET-FORTIGATE-MIB, 1.3.6.1.4.1.12356.101): CPU,
    memory, session count, HA mode and per-member sync, IPsec tunnels;
  - Palo Alto Networks (PAN-COMMON-MIB, 1.3.6.1.4.1.25461.2.3 devices):
    sessions and limit, HA mode/state and peer state, management-plane CPU
    from HOST-RESOURCES-MIB.

PAN-COMMON-MIB has no configuration-sync object; for PAN-OS over SNMP
`ha_sync` reports whether the HA peer is present and healthy. Every read is a
WALK so "the agent does not implement this" (no rows) is told apart from
"the read failed" (an error), and nothing is written to the device.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.collectors.base import NormalizedBundle, NormalizedFirewallStatus, NormalizedVpnTunnel

Walk = Callable[[str], Awaitable[tuple[list[tuple[str, str]], str | None]]]

FORTIGATE_PREFIX = "1.3.6.1.4.1.12356.101"
PANOS_PREFIX = "1.3.6.1.4.1.25461.2.3"

# -- FORTINET-FORTIGATE-MIB -------------------------------------------------
_FG_SYS = "1.3.6.1.4.1.12356.101.4.1"          # .3 cpu, .4 mem %, .8 sessions
_FG_HA_MODE = "1.3.6.1.4.1.12356.101.13.1.1"   # 1 standalone, 2 a-a, 3 a-p
_FG_HA_SERIAL = "1.3.6.1.4.1.12356.101.13.2.1.1.2"
_FG_HA_SYNC = "1.3.6.1.4.1.12356.101.13.2.1.1.12"  # 0 unsynchronized, 1 synchronized
_FG_VPN_P1 = "1.3.6.1.4.1.12356.101.12.2.2.1.2"
_FG_VPN_GW = "1.3.6.1.4.1.12356.101.12.2.2.1.4"
_FG_VPN_STATUS = "1.3.6.1.4.1.12356.101.12.2.2.1.20"  # 1 down, 2 up
_FG_HA_MODES = {1: "standalone", 2: "active-active", 3: "active-passive"}

# -- PAN-COMMON-MIB / HOST-RESOURCES-MIB ------------------------------------
_PAN_SYS = "1.3.6.1.4.1.25461.2.1.2.1"         # .11 HA state, .12 peer state, .13 HA mode
_PAN_SESSION = "1.3.6.1.4.1.25461.2.1.2.3"     # .2 max, .3 active
_HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"
_PAN_PEER_OK = {"active", "passive", "active-primary", "active-secondary"}


def firewall_family(sys_object_id: str | None) -> str | None:
    oid = (sys_object_id or "").strip().lstrip(".")
    if oid.startswith(FORTIGATE_PREFIX + ".") or oid == FORTIGATE_PREFIX:
        return "fortigate"
    if oid.startswith(PANOS_PREFIX + ".") or oid == PANOS_PREFIX:
        return "panos"
    return None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _suffix(oid: str, base: str) -> str | None:
    oid = oid.lstrip(".")
    return oid[len(base) + 1:] if oid.startswith(base + ".") else None


async def collect_firewall(walk: Walk, family: str) -> dict[str, Any]:
    """Raw column maps {base: {suffix: value}} plus per-walk errors."""
    bases = (
        (_FG_SYS, _FG_HA_MODE, _FG_HA_SERIAL, _FG_HA_SYNC, _FG_VPN_P1, _FG_VPN_GW, _FG_VPN_STATUS)
        if family == "fortigate"
        else (_PAN_SYS, _PAN_SESSION, _HR_PROCESSOR_LOAD)
    )
    columns: dict[str, dict[str, str]] = {}
    errors: dict[str, str] = {}
    for base in bases:
        rows, err = await walk(base)
        columns[base] = {s: v for oid, v in rows if (s := _suffix(oid, base)) is not None}
        if err and not rows:
            errors[base] = err
    return {"family": family, "columns": columns, "errors": errors}


def _status_of(rows: bool, errors: list[bool]) -> str:
    if rows:
        return "collected"
    return "failed" if any(errors) else "unavailable"


def normalize_firewall(raw: dict[str, Any], bundle: NormalizedBundle, device_external_id: str) -> None:
    if raw.get("failed"):
        bundle.firewall_status.update({"status": "failed", "vpn": "failed"} if raw.get("family") == "fortigate"
                                      else {"status": "failed"})
        return
    cols: dict[str, dict[str, str]] = raw.get("columns") or {}
    errs: dict[str, str] = raw.get("errors") or {}
    fw = NormalizedFirewallStatus(device_external_id=device_external_id)

    if raw.get("family") == "fortigate":
        sys = cols.get(_FG_SYS, {})
        fw.cpu_percent = float(v) if (v := _int(sys.get("3.0"))) is not None else None
        fw.memory_percent = float(v) if (v := _int(sys.get("4.0"))) is not None else None
        fw.session_count = _int(sys.get("8.0"))
        fw.ha_mode = _FG_HA_MODES.get(_int(cols.get(_FG_HA_MODE, {}).get("0")))
        syncs = [_int(v) for v in cols.get(_FG_HA_SYNC, {}).values()]
        if fw.ha_mode not in (None, "standalone") and _FG_HA_SYNC not in errs:
            fw.ha_sync = "in_sync" if len(syncs) >= 2 and all(s == 1 for s in syncs) else "out_of_sync"
            serials = list(cols.get(_FG_HA_SERIAL, {}).values())
            fw.ha_peer_serial = serials[1] if len(serials) >= 2 else None

        tunnels: dict[str, list[int | None]] = {}
        gateways: dict[str, str] = {}
        for idx, p1 in cols.get(_FG_VPN_P1, {}).items():
            tunnels.setdefault(p1, []).append(_int(cols.get(_FG_VPN_STATUS, {}).get(idx)))
            gateways.setdefault(p1, cols.get(_FG_VPN_GW, {}).get(idx) or "")
        for name, states in tunnels.items():
            up = sum(1 for s in states if s == 2)
            bundle.vpn_tunnels.append(NormalizedVpnTunnel(
                device_external_id=device_external_id, name=name, status="up" if up else "down",
                remote_gateway=gateways.get(name) or None, phase2_up=up, phase2_total=len(states),
            ))
        bundle.firewall_status["vpn"] = _status_of(bool(tunnels), [_FG_VPN_P1 in errs, _FG_VPN_STATUS in errs])
        status_errors = [_FG_SYS in errs, _FG_HA_MODE in errs]
    else:
        sys = cols.get(_PAN_SYS, {})
        mode = (sys.get("13.0") or "").strip().lower()
        if mode == "disabled":
            fw.ha_mode = "standalone"
        elif mode in ("active-passive", "active-active"):
            fw.ha_mode = mode
            fw.ha_role = (sys.get("11.0") or "").strip().lower() or None
            fw.ha_sync = "in_sync" if (sys.get("12.0") or "").strip().lower() in _PAN_PEER_OK else "out_of_sync"
        session = cols.get(_PAN_SESSION, {})
        fw.session_limit = _int(session.get("2.0"))
        fw.session_count = _int(session.get("3.0"))
        loads = [v for v in (_int(x) for x in cols.get(_HR_PROCESSOR_LOAD, {}).values()) if v is not None]
        fw.cpu_percent = round(sum(loads) / len(loads), 1) if loads else None
        status_errors = [_PAN_SYS in errs, _PAN_SESSION in errs]

    reported = any(getattr(fw, f) is not None for f in ("ha_mode", "session_count", "cpu_percent", "memory_percent"))
    if reported:
        bundle.firewalls.append(fw)
    bundle.firewall_status["status"] = _status_of(reported, status_errors)
