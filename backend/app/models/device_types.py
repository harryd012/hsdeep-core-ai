"""
Canonical network-device-type vocabulary.

Problem this solves
------------------
The codebase currently has THREE inconsistent type vocabularies that never
agree:

1. `app/collectors/base.py` documents (as a *comment*) the canonical set
   ``router | switch | firewall | wireless_ap | server | vm |
   cloud_resource | storage | other``.
2. `scripts/seed_cmdb_data.py` seeds devices with ``network``, ``database``,
   ``wireless``, ``virtualization`` ... (different strings, and it lumps a
   router + a switch both under ``"network"``).
3. The live DB currently only contains ``"server"`` and ``"other"``.

No normalization exists, so a filter on the Infrastructure page or the
dashboard breakdown can never match all the ways the same concept is spelled.

This module is the single source of truth for the canonical vocabulary and a
``normalize_device_type()`` helper that maps every known alias onto it.
Collectors call ``normalize_device_type()`` before writing ``device_type``,
the service layer uses it to bucket counts, and the frontend filter chips map
their labels to the same canonical strings.

"Unclassified" is a real bucket, not a fabrication: a device whose
``device_type`` is ``None`` / empty / ``"other"`` / ``"unknown"`` /
``"network"`` (ambiguous router-vs-switch) normalizes to ``"unclassified"``.
The UI shows this bucket explicitly so ops can see how many devices still
need a real classification rather than silently dropping them.
"""
from __future__ import annotations

# Canonical device types. Collectors should normalize to one of these before
# persisting; the frontend filter chips map 1:1 to this set.
CANONICAL_DEVICE_TYPES: list[str] = [
    "router",
    "switch",
    "access_point",
    "firewall",
    "security_appliance",
    "wireless_controller",
    "load_balancer",
    "wan_optimizer",
    "server",
    "sensor",
    "vm",
    "hypervisor",
    "cloud_resource",
    "storage",
    "backup_appliance",
    "other",
]

# The first-class network-infrastructure categories the task asks the UI to
# surface as filter chips / dashboard tiles. Everything NOT in this set is
# treated as "unclassified" for the breakdown (servers/sensors/etc. remain
# recognizable types but are not "unclassified").
NETWORK_DEVICE_TYPES: frozenset[str] = frozenset(
    {"router", "switch", "access_point", "firewall"}
)

# "Unclassified" is a derived bucket, not a stored device_type value.
UNCLASSIFIED = "unclassified"

# Maps every observed alias / spelling onto a canonical type. "network" is
# intentionally mapped to UNCLASSIFIED: it's ambiguous (router vs. switch) and
# the SNMP/PRTG collectors classify from sysDescr/name when they can; if they
# can't tell, the device stays unclassified rather than being invented.
_ALIAS_TO_CANONICAL: dict[str, str] = {
    # access point family
    "wireless_ap": "access_point",
    "wireless": "access_point",
    "accesspoint": "access_point",
    "access_point": "access_point",
    "wifi_ap": "access_point",
    # virtualisation
    "virtualization": "vm",
    "virtual_machine": "vm",
    "virtualmachine": "vm",
    # the seed data's "database" servers are still servers
    "database": "server",
    "load_balancer": "server",
    "loadbalancer": "server",
    # ambiguous / unknown -> unclassified
    "network": UNCLASSIFIED,
    "other": UNCLASSIFIED,
    "unknown": UNCLASSIFIED,
    "generic": UNCLASSIFIED,
    "": UNCLASSIFIED,
}


def normalize_device_type(raw: str | None) -> str:
    """Normalize an arbitrary device_type spelling to a canonical value.

    Returns one of ``CANONICAL_DEVICE_TYPES`` or ``"unclassified"``.

    ``unclassified`` covers: ``None``, empty string, ``"other"``, ``"unknown"``,
    and the ambiguous ``"network"`` — i.e. anything we cannot confidently map
    to a concrete category. This keeps the "Unclassified" bucket honest rather
    than forcing every legacy value into a made-up category.
    """
    if not raw:
        return UNCLASSIFIED
    key = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if key == "":
        return UNCLASSIFIED
    # Known alias first — checked BEFORE the canonical set so historical
    # spellings like "other" and "wireless" map to "unclassified" /
    # "access_point" even though "other" is also a valid *stored* device_type.
    if key in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[key]
    # Already a canonical type the caller used directly (e.g. "router")?
    if key in CANONICAL_DEVICE_TYPES:
        return key
    # Anything unrecognized is treated as unclassified rather than
    # force-fit into one of the network categories.
    return UNCLASSIFIED


def is_network_infrastructure(device_type: str | None) -> bool:
    """True if the (possibly raw) type normalizes to a network-infra category
    (router/switch/access_point/firewall)."""
    return normalize_device_type(device_type) in NETWORK_DEVICE_TYPES


# Field spec per network device type. These are the keys written into
# ``Device.custom_metadata`` by the collectors, and the keys the frontend
# device-detail view renders. Centralised here so the schema of each type's
# metadata is a single editable dictionary rather than scattered across
# collectors and UI.
#
# Routing mode values: "static" | "dynamic" (dynamic carries routing_protocol).
DEVICE_TYPE_METADATA_FIELDS: dict[str, list[str]] = {
    "router": ["routing_mode", "routing_protocol"],
    "switch": ["layer", "port_count", "vlans"],
    "access_point": ["connected_clients", "ssid", "band"],
    "firewall": ["rule_count", "active_sessions", "wan_status", "lan_status"],
}


def type_metadata_fields(device_type: str | None) -> list[str]:
    """The custom_metadata keys that are meaningful for the given device type."""
    return DEVICE_TYPE_METADATA_FIELDS.get(normalize_device_type(device_type), [])


# ---------------------------------------------------------------------------
# Vendor catalog (Sections 14/15). Canonical vendor string + device families
# of the network/security vendors HSDEEP CORE AI supports through modular
# connectors (SNMP/SSH/API/telemetry). Used by docs, UI filter chips and
# collector targeting — never as a hardcoded limit.
# ---------------------------------------------------------------------------
VENDOR_CATALOG: dict[str, tuple[str, ...]] = {
    "Cisco": (
        "router", "switch", "firewall", "security_appliance", "wireless_controller",
        "access_point", "load_balancer",
    ),
    "Juniper": ("router", "switch", "firewall"),
    "Arista": ("switch", "router", "load_balancer"),
    "HPE": ("switch", "server"),
    "Aruba": ("switch", "wireless_controller", "access_point"),
    "Fortinet": ("firewall", "security_appliance"),
    "Palo Alto Networks": ("firewall", "security_appliance"),
    "Check Point": ("firewall", "security_appliance"),
    "Sophos": ("firewall", "security_appliance"),
    "SonicWall": ("firewall", "security_appliance"),
    "F5": ("load_balancer", "security_appliance"),
    "Extreme Networks": ("switch", "router"),
    "Dell Networking": ("switch", "storage"),
    "Huawei": ("router", "switch", "firewall"),
    "MikroTik": ("router", "switch", "access_point"),
    "Ubiquiti": ("switch", "access_point", "router"),
    "VMware": ("hypervisor", "vm"),
    "Microsoft": ("server", "vm"),
    "Amazon Web Services": ("cloud_resource", "cloud_load_balancer", "cloud_firewall"),
    "Microsoft Azure": ("cloud_resource", "cloud_load_balancer", "cloud_firewall"),
    "Google Cloud": ("cloud_resource", "cloud_load_balancer", "cloud_firewall"),
}

ALL_VENDORS: tuple[str, ...] = tuple(sorted(VENDOR_CATALOG.keys()))


def vendor_families(vendor: str | None) -> list[str]:
    """Device families a vendor is known to ship (for UI filter chips)."""
    if not vendor:
        return []
    return list(VENDOR_CATALOG.get(vendor.strip(), []))


# Map Section 14/15 device families to canonical device_type for taxonomy.
DEVICE_FAMILY_TO_TYPE: dict[str, str] = {
    # network
    "customer_edge": "router",
    "provider_edge": "router",
    "provider_router": "router",
    "core_router": "router",
    "router": "router",
    "switch": "switch",
    "lan_switch": "switch",
    "wireless_controller": "wireless_controller",
    "access_point": "access_point",
    "apt": "access_point",
    # security
    "firewall": "firewall",
    "next_generation_firewall": "firewall",
    "ngfw": "firewall",
    "ids_ips": "security_appliance",
    "intrusion_prevention": "security_appliance",
    "intrusion_detection": "security_appliance",
    "secure_web_gateway": "security_appliance",
    "web_application_firewall": "security_appliance",
    "waf": "security_appliance",
    "vpn_concentrator": "security_appliance",
    "email_security_gateway": "security_appliance",
    "nac": "security_appliance",
    "siem": "security_appliance",
    "load_balancer": "load_balancer",
    "wan_optimizer": "wan_optimizer",
    # compute
    "server": "server",
    "physical_server": "server",
    "vm": "vm",
    "virtual_machine": "vm",
    "hypervisor": "hypervisor",
    "kubernetes_node": "vm",
    # storage / backup
    "storage": "storage",
    "backup_appliance": "backup_appliance",
    "cloud": "cloud_resource",
    "cloud_resource": "cloud_resource",
}
