"""
Vendor-neutral switching discovery primitives (Phase 3B).

This module holds ONLY pure functions and constants: OID definitions, wire-format
decoders and vendor recognition. It performs no I/O and never touches the
database, so every rule here is unit-testable without a device - the same
separation already used by `app/collectors/classification.py`.

Applied protocols (all standard, all vendor-neutral):
  * Q-BRIDGE-MIB  - VLAN inventory, per-port PVID, tagged/untagged membership
  * BRIDGE-MIB    - STP/RSTP/MSTP bridge and per-port state
  * IEEE8023-LAG-MIB - LACP/PAgP link aggregation

Cisco and Aruba are NOT special-cased here with vendor-specific MIBs. They are
recognised from `sysObjectID` and then read through the SAME standard MIBs every
other vendor exposes, so a new switch vendor needs no new code.

HONESTY RULES (carried over from Phase 3A):
  * Every decoder returns None when it cannot decode a value. A malformed or
    unexpected wire value is never coerced into a plausible-looking one.
  * Nothing is inferred. A VLAN is reported because the device listed it, not
    because a port count or a naming convention suggested it.
"""
from __future__ import annotations

from typing import Any

# -- Standard MIB roots (verified against RFC 4188 / RFC 4363 / IEEE8023-LAG) -
_OID_SYS_OBJECT_ID = ".1.3.6.1.2.1.1.2.0"

# BRIDGE-MIB (RFC 4188)
_DOT1D_BASE_PORT_IF_INDEX = ".1.3.6.1.2.1.17.1.4.1.2"
_DOT1D_STP_PROTOCOL_SPEC = ".1.3.6.1.2.1.17.2.1.0"
_DOT1D_STP_PRIORITY = ".1.3.6.1.2.1.17.2.2.0"
_DOT1D_STP_TOP_CHANGES = ".1.3.6.1.2.1.17.2.4.0"
_DOT1D_STP_DESIGNATED_ROOT = ".1.3.6.1.2.1.17.2.5.0"
_DOT1D_STP_ROOT_COST = ".1.3.6.1.2.1.17.2.6.0"
_DOT1D_STP_ROOT_PORT = ".1.3.6.1.2.1.17.2.7.0"
_DOT1D_STP_PORT_STATE = ".1.3.6.1.2.1.17.2.15.1.3"
_DOT1D_STP_PORT_PATH_COST = ".1.3.6.1.2.1.17.2.15.1.5"
_DOT1D_STP_PORT_DESIGNATED_BRIDGE = ".1.3.6.1.2.1.17.2.15.1.8"

# Q-BRIDGE-MIB (RFC 4363)
_DOT1Q_VLAN_VERSION = ".1.3.6.1.2.1.17.7.1.1.1.0"
_DOT1Q_VLAN_STATIC_NAME = ".1.3.6.1.2.1.17.7.1.4.3.1.1"
_DOT1Q_VLAN_STATIC_EGRESS = ".1.3.6.1.2.1.17.7.1.4.3.1.2"
_DOT1Q_VLAN_STATIC_UNTAGGED = ".1.3.6.1.2.1.17.7.1.4.3.1.4"
_DOT1Q_PVID = ".1.3.6.1.2.1.17.7.1.4.5.1.1"

# IEEE8023-LAG-MIB (IEEE 802.1AX). Note the ISO-registered root
# 1.2.840.10006.300.43 - NOT under mib-2. Devices that expose no objects here
# are reported as `unavailable`, which is a fact about the device, not an error.
_LAG_AGG_TABLE = ".1.2.840.10006.300.43.1.1.1"
_LAG_AGG_MAC_ADDRESS = ".1.2.840.10006.300.43.1.1.1.1.2"
_LAG_AGG_ACTOR_SYSTEM_ID = ".1.2.840.10006.300.43.1.1.1.1.4"
_LAG_AGG_PORT_LIST = ".1.2.840.10006.300.43.1.1.2.1.1"

# ENTITY-MIB entPhysicalTable (RFC 4133). Used ONLY for authoritative identity:
# the chassis serial is the cross-source identity key (Phase 4 §16/§17), because
# it is manufacturer-assigned and survives a change of address, hostname or the
# collector that observed the device. Indexed by entPhysicalIndex.
_ENTITY_PHYSICAL_DESCR = ".1.3.6.1.2.1.47.1.1.1.1.2"
_ENTITY_PHYSICAL_CLASS = ".1.3.6.1.2.1.47.1.1.1.1.5"
_ENTITY_PHYSICAL_NAME = ".1.3.6.1.2.1.47.1.1.1.1.7"
_ENTITY_PHYSICAL_SERIAL = ".1.3.6.1.2.1.47.1.1.1.1.11"
_ENTITY_PHYSICAL_MODEL = ".1.3.6.1.2.1.47.1.1.1.1.13"

#: entPhysicalClass enumeration: 3 == chassis. Only the chassis carries the
#: device-level serial; line cards, PSUs and fans have their own.
_ENTITY_CLASS_CHASSIS = "3"

# IANA enterprise number -> vendor name. Only the switch vendors named in the
# Phase 3B brief plus the closest neighbours; sysDescr is used as a secondary
# signal, and an unknown PEN is reported as None rather than guessed.
_ENTERPRISE_VENDORS: dict[str, str] = {
    "1.3.6.1.4.1.9": "Cisco",
    "1.3.6.1.4.1.11": "HPE",
    "1.3.6.1.4.1.14823": "Aruba",
    "1.3.6.1.4.1.47196": "Aruba",
    "1.3.6.1.4.1.6486": "Alcatel-Lucent Enterprise",
    "1.3.6.1.4.1.1916": "Extreme Networks",
    "1.3.6.1.4.1.4526": "Netgear",
    "1.3.6.1.4.1.30065": "Arista",
    "1.3.6.1.4.1.1588": "Brocade",
    "1.3.6.1.4.1.14988": "MikroTik",
    "1.3.6.1.4.1.890": "Zyxel",
    "1.3.6.1.4.1.674": "Dell",
    "1.3.6.1.4.1.2": "IBM",
    "1.3.6.1.4.1.2636": "Juniper",
    "1.3.6.1.4.1.2011": "Huawei",
}

# sysDescr substrings used ONLY when sysObjectID's PEN is absent/unknown. Kept
# deliberately short and specific to avoid mislabelling a device.
_DESCR_VENDOR_HINTS: tuple[tuple[str, str], ...] = (
    ("cisco", "Cisco"),
    ("aruba", "Aruba"),
    ("procurve", "HPE"),
    ("hewlett", "HPE"),
    ("hpe ", "HPE"),
    ("juniper", "Juniper"),
    ("arista", "Arista"),
    ("extreme", "Extreme Networks"),
    ("mikrotik", "MikroTik"),
    ("huawei", "Huawei"),
    ("netgear", "Netgear"),
)

# device_type values must match app/models/device_types.py vocabulary.
_SWITCH_TYPE_HINTS = ("switch", "catalyst", "procurve", "aruba", "nexus")
_ROUTER_TYPE_HINTS = ("router", "asr", "isr", "mx", "routerboard")


def enterprise_from_oid(oid: str | None) -> str | None:
    """The longest matching enterprise prefix for a sysObjectID, or None.

    Longest-match matters: `.1.3.6.1.4.1.9` (Cisco) is a prefix of nothing else
    in the table, but `.1.3.6.1.4.1.11` (HPE) would falsely match an OID under
    `.1.3.6.1.4.1.1100...` if we compared naively.
    """
    if not oid:
        return None
    matches = [pen for pen in _ENTERPRISE_VENDORS if oid.startswith(pen + ".") or oid == pen]
    if not matches:
        return None
    return max(matches, key=len)


def recognise_vendor(sys_object_id: str | None, sys_descr: str | None) -> str | None:
    """Vendor name from sysObjectID, falling back to an explicit sysDescr hint.

    Returns None when neither signal identifies the vendor. An unknown vendor is
    reported as unknown - it is not defaulted to Cisco or "Generic".
    """
    pen = enterprise_from_oid(sys_object_id)
    if pen:
        return _ENTERPRISE_VENDORS[pen]
    if sys_descr:
        lowered = sys_descr.lower()
        for needle, vendor in _DESCR_VENDOR_HINTS:
            if needle in lowered:
                return vendor
    return None


def classify_switch(device_type: str | None, sys_descr: str | None) -> str | None:
    """Device type for a device that speaks bridging MIBs, else None.

    Only used to REFINE a device the collector already knows is a network device;
    it never overrides a server/VM classification.
    """
    if device_type not in (None, "switch", "router", "other", "unknown"):
        return device_type
    if sys_descr:
        lowered = sys_descr.lower()
        if any(hint in lowered for hint in _ROUTER_TYPE_HINTS):
            return "router"
        if any(hint in lowered for hint in _SWITCH_TYPE_HINTS):
            return "switch"
    return device_type


# -- Wire-format decoders ----------------------------------------------------
# `str()` on a pysnmp OctetString yields the RAW BYTES (verified: all 256 byte
# values round-trip losslessly through str() -> .encode("latin-1")). Hex form
# only ever comes from `.prettyPrint()`, which the walk does not use. Every
# decoder therefore works on raw bytes and returns None rather than guessing.

def to_octets(value: Any) -> bytes | None:
    """Raw bytes for a value the walk produced via `str()`, else None."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    text = str(value)
    if not text:
        return None
    return text.encode("latin-1", errors="replace")


def decode_int(value: Any) -> int | None:
    """Integer value, or None when it is not one.

    Gauge32/Counter32 arrive as plain digits; an octet string does not, and is
    rejected instead of being partially parsed.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def decode_mac(value: Any) -> str | None:
    """6-byte MacAddress -> 'aa:bb:cc:dd:ee:ff'. None for any other length."""
    raw = to_octets(value)
    if raw is None or len(raw) != 6:
        return None
    return ":".join(f"{b:02x}" for b in raw)


def decode_port_list(value: Any) -> set[int] | None:
    """Q-BRIDGE `PortList` bitmap -> set of bridge port numbers (1-based).

    RFC 4363: "the first octet specifies ports 1-8, the second ports 9-16, etc.
    Within each octet, the most significant bit represents the lowest numbered
    port."

    Returns None (unknown) for a value that is absent OR empty. A zero-length
    OctetString is not treated as "no ports": the walk's `str()` contract cannot
    tell a genuinely empty bitmap apart from a column that was not returned, so
    asserting "this VLAN has no member ports" would be a guess. Callers that
    need the distinction branch on `collected` vs `unavailable` in the protocol
    status, not on this return value.
    """
    raw = to_octets(value)
    if raw is None:
        return None
    ports: set[int] = set()
    for octet_index, octet in enumerate(raw):
        if octet == 0:
            continue
        for bit in range(8):
            # bit 0 == most significant bit == lowest numbered port
            if octet & (0x80 >> bit):
                ports.add(octet_index * 8 + bit + 1)
    return ports


# BRIDGE-MIB enumerations (RFC 4188)
_STP_PROTOCOL_NAMES = {"1": "unknown", "2": "decLb100", "3": "ieee8021d"}
_STP_PORT_STATES = {
    "1": "disabled", "2": "blocking", "3": "listening",
    "4": "learning", "5": "forwarding", "6": "broken",
}


def stp_protocol_name(value: Any) -> str | None:
    """dot1dStpProtocolSpecification -> 'ieee8021d' etc. None if unrecognised."""
    code = decode_int(value)
    if code is None:
        return None
    return _STP_PROTOCOL_NAMES.get(str(code))


def stp_port_state_name(value: Any) -> str | None:
    """dot1dStpPortState enum -> name. None if unrecognised."""
    code = decode_int(value)
    if code is None:
        return None
    return _STP_PORT_STATES.get(str(code))


def vlan_id_from_index(suffix: str) -> int | None:
    """dot1qVlanStaticTable / dot1qVlanCurrentTable index -> VLAN id.

    These tables are indexed by dot1qVlanIndex, which is a single unsigned
    integer. A compound or malformed index is rejected rather than truncated.
    """
    parts = suffix.split(".")
    if len(parts) != 1 or not parts[0].isdigit():
        return None
    vlan_id = int(parts[0])
    if not 1 <= vlan_id <= 4094:
        return None
    return vlan_id


def bridge_port_from_index(suffix: str) -> int | None:
    """dot1qPvid / dot1dStpPortTable / dot1dBasePortTable index -> bridge port."""
    parts = suffix.split(".")
    if len(parts) != 1 or not parts[0].isdigit():
        return None
    return int(parts[0])


def select_chassis_identity(
    classes: dict[str, str],
    serials: dict[str, str],
    models: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Pick the DEVICE-level (chassis) serial/model from ENTITY-MIB rows.

    Returns (serial, model, chassis_name). Selection rule, deliberately strict:

      * when any row reports entPhysicalClass == chassis(3), use THAT row. A line
        card, PSU or fan serial is a real serial, but it is not the device's -
        picking one would give the same device different identity depending on
        which component the agent listed first;
      * when no row reports a class at all, use the sole serial IF exactly one
        non-empty serial exists. With several candidates the answer is genuinely
        ambiguous, so return None rather than invent an identity. Device identity
        must never be a guess.

    Credentials/secret material never appear here: ENTITY-MIB serial/model/name
    are hardware identifiers.
    """
    serials = {k: v for k, v in (serials or {}).items() if v not in (None, "")}
    models = {k: v for k, v in (models or {}).items() if v not in (None, "")}
    names = {k: v for k, v in (names or {}).items() if v not in (None, "")}

    chassis_indexes = [
        index for index, klass in (classes or {}).items()
        if str(klass).strip() == _ENTITY_CLASS_CHASSIS
    ]
    if chassis_indexes:
        # Prefer a chassis that actually reports a serial.
        for index in chassis_indexes:
            if index in serials:
                return serials[index], models.get(index), names.get(index)
        index = chassis_indexes[0]
        return None, models.get(index), names.get(index)

    if len(serials) == 1:
        index = next(iter(serials))
        return serials[index], models.get(index), names.get(index)

    return None, None, None


def build_switching_metadata(
    switching: dict[str, Any] | None,
    if_index_to_name: dict[int, str] | None = None,
) -> dict[str, Any] | None:
    """Vendor-neutral switching facts for `Device.custom_metadata`.

    Returns None when the device exposed no bridging data at all, so a server or
    a router does not carry an empty switching block that implies discovery ran
    and found nothing.

    Every number here comes from the device. Nothing is inferred, and any value
    that failed to decode is reported as `null` rather than defaulted.

    Interface NAMES are used as the join key (not Interface row ids) because
    Device.custom_metadata is written before interfaces are necessarily resolved.
    Callers that need a relational join should promote this to columns later;
    the shape is deliberately flat to make that easy.
    """
    if not switching:
        return None
    names = if_index_to_name or {}
    bridge_port_if = switching.get("bridge_port_if_index") or {}
    status = switching.get("status") or {}

    def _name_for_bridge_port(bridge_port: int) -> str | None:
        if_index = bridge_port_if.get(bridge_port)
        if if_index is None:
            return None
        return names.get(if_index)

    # -- VLANs ---------------------------------------------------------------
    vlan_names = switching.get("vlan_names") or {}
    vlan_egress = switching.get("vlan_egress") or {}
    vlan_untagged = switching.get("vlan_untagged") or {}
    vlan_ids = sorted(set(vlan_names) | set(vlan_egress) | set(vlan_untagged))
    vlans: list[dict[str, Any]] = []
    for vlan_id in vlan_ids:
        tagged = decode_port_list(vlan_egress.get(vlan_id))
        untagged = decode_port_list(vlan_untagged.get(vlan_id))
        name = vlan_names.get(vlan_id)
        vlans.append({
            "vlan_id": vlan_id,
            "name": str(name) if name not in (None, "") else None,
            "member_ports": sorted(
                n for n in (_name_for_bridge_port(bp) for bp in (tagged or set()))
                if n is not None
            ),
            "untagged_ports": sorted(
                n for n in (_name_for_bridge_port(bp) for bp in (untagged or set()))
                if n is not None
            ),
        })

    # Per-port PVID, keyed by interface name. A port whose name could not be
    # resolved is omitted rather than reported under a synthetic name.
    pvid_by_interface: dict[str, int] = {}
    for bridge_port, pvid in sorted((switching.get("pvid") or {}).items()):
        name = _name_for_bridge_port(bridge_port)
        if name is not None:
            pvid_by_interface[name] = pvid

    # -- Spanning tree -------------------------------------------------------
    raw_stp = switching.get("stp") or {}
    stp: dict[str, Any] | None = None
    if raw_stp:
        protocol = stp_protocol_name(raw_stp.get("protocol"))
        stp_ports: dict[str, dict[str, Any]] = {}
        states = raw_stp.get("port_state") or {}
        costs = raw_stp.get("port_path_cost") or {}
        for bridge_port in sorted(set(states) | set(costs)):
            name = _name_for_bridge_port(bridge_port)
            if name is None:
                continue
            entry: dict[str, Any] = {}
            if bridge_port in states:
                entry["state"] = states[bridge_port]
            if bridge_port in costs:
                entry["path_cost"] = costs[bridge_port]
            stp_ports[name] = entry
        stp = {
            "protocol": protocol,
            "priority": decode_int(raw_stp.get("priority")),
            "designated_root": decode_mac(raw_stp.get("designated_root")),
            "root_cost": decode_int(raw_stp.get("root_cost")),
            "root_port": decode_int(raw_stp.get("root_port")),
            "topology_changes": decode_int(raw_stp.get("top_changes")),
            "is_root_bridge": (
                decode_mac(raw_stp.get("designated_root")) == switching.get("bridge_address")
                if switching.get("bridge_address") else None
            ),
            "ports": stp_ports,
        }

    # -- Link aggregation ----------------------------------------------------
    aggregators: list[dict[str, Any]] = []
    agg_macs = switching.get("lag_aggregators") or {}
    agg_lists = switching.get("lag_port_lists") or {}
    for agg_index in sorted(set(agg_macs) | set(agg_lists)):
        members = decode_port_list(agg_lists.get(agg_index))
        aggregators.append({
            # dot3adAggIndex IS an ifIndex, so it resolves in the same map.
            "name": names.get(agg_index),
            "mac_address": decode_mac(agg_macs.get(agg_index)),
            "member_ports": sorted(
                n for n in (_name_for_bridge_port(bp) for bp in (members or set()))
                if n is not None
            ),
        })

    if not vlans and stp is None and not aggregators:
        # No switching data at all. Distinguish "we could not read it" from
        # "this device is not a switch":
        #   * a FAILED read is still recorded - it is a real collection fault an
        #     operator needs to see, and must not look like "no VLANs here";
        #   * unavailable/empty with no data means the device simply does not do
        #     802.1Q/STP/LACP, so nothing is written. Storing a status-only block
        #     on every server in the estate would be noise that implies switching
        #     discovery ran and found nothing.
        if any(value == "failed" for value in status.values()):
            return {
                "vlan_status": status.get("vlan"),
                "stp_status": status.get("stp"),
                "lag_status": status.get("lag"),
                "vlan_count": 0,
            }
        return None

    metadata: dict[str, Any] = {
        "vlan_status": status.get("vlan"),
        "stp_status": status.get("stp"),
        "lag_status": status.get("lag"),
        "vlan_count": len(vlans),
    }
    if vlans:
        metadata["vlans"] = vlans
        metadata["pvid_by_interface"] = pvid_by_interface
    if stp:
        metadata["stp"] = stp
    if aggregators:
        metadata["link_aggregation"] = aggregators
    return metadata
