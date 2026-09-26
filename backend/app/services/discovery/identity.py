"""
Cross-source device identity resolution (Phase 4 §16/§17).

THE PROBLEM THIS SOLVES
-----------------------
`DeviceRepository.upsert` keys identity on (monitoring_source_id, external_id),
which is correct for one collector re-polling: SNMP discovery #2 updates the
device SNMP discovery #1 created. But it means the SAME physical device seen by
two DIFFERENT collectors becomes two Devices:

    FortiGate  --SNMP-->      Device A
    FortiGate  --FortiGate API-->  Device B     <-- duplicate

    Windows srv --WMI-->      Device C
    Windows srv --SNMP-->     Device D          <-- duplicate

§16/§17 require ONE canonical Device that multiple collectors enrich. The schema
already anticipates this: `uq_devices_tenant_id_serial_number` exists precisely
so one serial maps to one device in a tenant.

WHAT COUNTS AS AUTHORITATIVE EVIDENCE
-------------------------------------
Only a hardware SERIAL NUMBER. It is manufacturer-assigned, stable and globally
meaningful, so it survives a change of management address, hostname, or the
collector that happened to observe it.

Deliberately NOT used, because §6 and §18 forbid identifying devices from them:

    * hostname / display name   - not unique, easily changed, often duplicated
    * ip_address alone          - reassigned by DHCP; two hosts share one address
                                  over time. Same subnet != same device.
    * mac_address               - per-INTERFACE, not per-device; virtual chassis,
                                  reused NICs and switch ports break it.
    * site / subnet / provider  - context, not identity.

A wrong merge is far worse than a missed merge: it silently attributes one
device's interfaces, sensors and alerts to another. When in doubt this module
resolves NOTHING and the caller keeps today's source-scoped behaviour.

SERIAL NORMALISATION
--------------------
Vendors pad serials, mix case, or return placeholder junk ("To be filled by
O.E.M.", "None", "00000000", empty). Those are treated as ABSENT, never as a key,
so placeholder values cannot merge unrelated devices.
"""
from __future__ import annotations

#: Serial values that carry no identity. Compared case-folded and stripped.
_PLACEHOLDER_SERIALS: frozenset[str] = frozenset({
    "", "none", "null", "unknown", "n/a", "na", "not specified",
    "to be filled by o.e.m.", "to be filled by oem", "system serial number",
    "default string", "0", "00000000", "0000000000", "xxxxxxxx",
    "chassis serial number", "base board serial number",
})


def normalise_serial(serial: str | None) -> str | None:
    """Canonical form of a serial, or None when it carries no identity.

    Uppercases and strips surrounding/embedded whitespace so that
    '  fdo1234abcd ' and 'FDO1234ABCD' resolve to the same device, while
    placeholder values resolve to nothing at all.
    """
    if not serial:
        return None
    collapsed = " ".join(str(serial).split()).strip()
    if not collapsed:
        return None
    if collapsed.casefold() in _PLACEHOLDER_SERIALS:
        return None
    return collapsed.upper()


def is_authoritative(value: str | None) -> bool:
    """True when `value` is usable as a cross-source identity key."""
    return normalise_serial(value) is not None
