"""
Universal Infrastructure Intelligence - canonical, provider-neutral vocabulary.

Data-only contract imported by every layer of the universal infrastructure
model. Nothing here talks to a vendor, a database or a router.

Relationship to the existing vocabulary modules
-----------------------------------------------
This module EXTENDS, never replaces:

* ``app/models/device_types.py``            - the stored ``Device.device_type``
  vocabulary + alias normalisation.
* ``app/services/recognition/taxonomy.py``  - resource categories and the
  cloud/container types the network-centric list did not carry.

Both stay authoritative for what is stored on ``Device.device_type``.
``universal_resource_type()`` is a superset view that also understands the
names the universal spec requires (``cloud_subscription``, ``workstation``,
``vpn_gateway``, ...). A token none of the three recognises becomes
``unknown`` - it is never guessed into a real category.

Honesty rules encoded here (enforced downstream, never in the UI)
-----------------------------------------------------------------
1. ``SUPPORT_UNKNOWN`` ("we have not asked yet") is distinct from
   ``SUPPORT_UNSUPPORTED`` ("the provider cannot do this").
2. ``COLLECTION_*`` distinguishes *why* there is no number: the provider
   cannot supply it, the resource does not expose it, the poll failed, or the
   value went stale. Collapsing these is the failure this vocabulary prevents.
3. Every "no data" answer carries an explicit ``reason`` code.
"""
from __future__ import annotations

from app.models.device_types import normalize_device_type
from app.services.recognition.taxonomy import (
    CATEGORY_APPLICATION,
    CATEGORY_CLOUD,
    CATEGORY_COMPUTE,
    CATEGORY_CONTAINER,
    CATEGORY_DATABASE,
    CATEGORY_ENDPOINT,
    CATEGORY_NETWORK,
    CATEGORY_SECURITY,
    CATEGORY_STORAGE,
    CATEGORY_UNKNOWN,
    CATEGORY_VIRTUALIZATION,
    CATEGORY_WIRELESS,
    category_for,
    normalize_resource_type,
)

# ---------------------------------------------------------------------------
# Resource families (spec section 4). Deliberately the SAME strings as
# recognition.taxonomy's categories so the two can never drift.
# ---------------------------------------------------------------------------
FAMILY_NETWORK = CATEGORY_NETWORK
FAMILY_COMPUTE = CATEGORY_COMPUTE
FAMILY_CLOUD = CATEGORY_CLOUD
FAMILY_ENDPOINT = CATEGORY_ENDPOINT
FAMILY_APPLICATION = CATEGORY_APPLICATION
FAMILY_STORAGE = CATEGORY_STORAGE
FAMILY_SECURITY = CATEGORY_SECURITY
FAMILY_WIRELESS = CATEGORY_WIRELESS
FAMILY_VIRTUALIZATION = CATEGORY_VIRTUALIZATION
FAMILY_CONTAINER = CATEGORY_CONTAINER
FAMILY_DATABASE = CATEGORY_DATABASE
FAMILY_OTHER = CATEGORY_UNKNOWN

RESOURCE_FAMILIES: tuple[str, ...] = (
    FAMILY_NETWORK,
    FAMILY_SECURITY,
    FAMILY_WIRELESS,
    FAMILY_COMPUTE,
    FAMILY_VIRTUALIZATION,
    FAMILY_CONTAINER,
    FAMILY_ENDPOINT,
    FAMILY_STORAGE,
    FAMILY_DATABASE,
    FAMILY_APPLICATION,
    FAMILY_CLOUD,
    FAMILY_OTHER,
)


# ---------------------------------------------------------------------------
# Universal resource types (spec section 4): superset of the stored canonical
# device types plus the cloud/endpoint/application/storage names the spec
# requires.
# ---------------------------------------------------------------------------
RESOURCE_TYPE_FAMILY: dict[str, str] = {
    # NETWORK
    "firewall": FAMILY_SECURITY,
    "router": FAMILY_NETWORK,
    "switch": FAMILY_NETWORK,
    "access_point": FAMILY_WIRELESS,
    "wireless_controller": FAMILY_WIRELESS,
    "gateway": FAMILY_NETWORK,
    "load_balancer": FAMILY_NETWORK,
    "network_device": FAMILY_NETWORK,
    "network_appliance": FAMILY_NETWORK,
    "wan_optimizer": FAMILY_NETWORK,
    "sensor": FAMILY_NETWORK,
    # COMPUTE
    "server": FAMILY_COMPUTE,
    "virtual_machine": FAMILY_VIRTUALIZATION,
    "hypervisor": FAMILY_VIRTUALIZATION,
    "vm": FAMILY_VIRTUALIZATION,
    "container_host": FAMILY_CONTAINER,
    # CLOUD
    "cloud_platform": FAMILY_CLOUD,
    "cloud_account": FAMILY_CLOUD,
    "cloud_subscription": FAMILY_CLOUD,
    "cloud_resource": FAMILY_CLOUD,
    "cloud_service": FAMILY_CLOUD,
    "cloud_database": FAMILY_DATABASE,
    "cloud_storage": FAMILY_STORAGE,
    "cloud_network": FAMILY_NETWORK,
    "cloud_load_balancer": FAMILY_NETWORK,
    # ENDPOINT
    "workstation": FAMILY_ENDPOINT,
    "laptop": FAMILY_ENDPOINT,
    "endpoint": FAMILY_ENDPOINT,
    "mobile_device": FAMILY_ENDPOINT,
    "iot_device": FAMILY_ENDPOINT,
    # APPLICATION
    "application": FAMILY_APPLICATION,
    "service": FAMILY_APPLICATION,
    "api": FAMILY_APPLICATION,
    "database": FAMILY_DATABASE,
    "web_service": FAMILY_APPLICATION,
    # STORAGE
    "storage_system": FAMILY_STORAGE,
    "storage_volume": FAMILY_STORAGE,
    "object_storage": FAMILY_STORAGE,
    "file_storage": FAMILY_STORAGE,
    "backup_resource": FAMILY_STORAGE,
    "backup_appliance": FAMILY_STORAGE,
    # SECURITY
    "security_gateway": FAMILY_SECURITY,
    "vpn_gateway": FAMILY_SECURITY,
    "security_appliance": FAMILY_SECURITY,
    "security_service": FAMILY_SECURITY,
    # OTHER
    "custom_device": FAMILY_OTHER,
    "custom_resource": FAMILY_OTHER,
}

#: Every type the universal layer understands, sorted.
UNIVERSAL_RESOURCE_TYPES: tuple[str, ...] = tuple(sorted(RESOURCE_TYPE_FAMILY))

UNKNOWN_RESOURCE_TYPE = "unknown"

#: Unambiguous provider/legacy token -> universal resource type. Ambiguous or
#: unrecognised tokens are NOT mapped (see universal_resource_type step 5).
_TYPE_ALIASES: dict[str, str] = {
    "ngfw": "firewall",
    "next_generation_firewall": "firewall",
    "firewalls": "firewall",
    "secure_web_gateway": "security_gateway",
    "ids_ips": "security_appliance",
    "siem": "security_service",
    "waf": "security_service",
    "routers": "router",
    "switches": "switch",
    "core_switch": "switch",
    "lan_switch": "switch",
    "virtualswitch": "switch",
    "wireless_ap": "access_point",
    "wifi_ap": "access_point",
    "wlan_controller": "wireless_controller",
    "firewall_gateway": "gateway",
    "default_gateway": "gateway",
    "nat_gateway": "gateway",
    "internet_gateway": "gateway",
    "transit_gateway": "gateway",
    "vpc": "cloud_network",
    "vnet": "cloud_network",
    "subnet": "cloud_network",
    "expressroute_circuit": "cloud_network",
    "direct_connect": "cloud_network",
    "application_gateway": "cloud_load_balancer",
    "elb": "cloud_load_balancer",
    "alb": "cloud_load_balancer",
    "physical_server": "server",
    "esxi_host": "hypervisor",
    "hypervisor_host": "hypervisor",
    "k8s_node": "container_host",
    "kubernetes_node": "container_host",
    "docker_host": "container_host",
    "ec2_instance": "cloud_resource",
    "cloud_vm": "cloud_resource",
    "instance": "cloud_resource",
    "subscription": "cloud_subscription",
    "azure_subscription": "cloud_subscription",
    "aws_account": "cloud_account",
    "gcp_project": "cloud_account",
    "project": "cloud_account",
    "resource_group": "cloud_resource",
    "storage_account": "cloud_storage",
    "s3_bucket": "cloud_storage",
    "blob_container": "cloud_storage",
    "block_storage": "cloud_storage",
    "datastore": "storage_volume",
    "sql_database": "cloud_database",
    "nosql_database": "cloud_database",
    "cosmos_db": "cloud_database",
    "rds": "cloud_database",
    "k8s_service": "service",
    "app_service": "cloud_service",
    "managed_service": "cloud_service",
    "key_vault": "security_service",
    "security_group": "security_service",
    "network_acl": "security_service",
    "cloud_firewall": "security_gateway",
    "pc": "workstation",
    "desktop": "workstation",
    "managed_endpoint": "endpoint",
    "mobile": "mobile_device",
    "iot": "iot_device",
    "camera": "iot_device",
    "storage": "storage_system",
    "san": "storage_system",
    "nas": "storage_system",
    "backup": "backup_resource",
}


def universal_resource_type(value: str | None) -> str:
    """Normalise any provider/legacy type token to a universal resource type.

    Resolution order:
      1. the universal set declared here,
      2. this module's unambiguous alias table,
      3. ``recognition.taxonomy.normalize_resource_type``,
      4. ``models.device_types.normalize_device_type``,
      5. ``"unknown"``.

    Step 5 is deliberate: an unrecognised token stays unknown rather than being
    bucketed with something it is not.
    """
    if not value:
        return UNKNOWN_RESOURCE_TYPE
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    if token in RESOURCE_TYPE_FAMILY:
        return token
    if token in _TYPE_ALIASES:
        return _TYPE_ALIASES[token]
    normalised = normalize_resource_type(token)
    if normalised and normalised in RESOURCE_TYPE_FAMILY:
        return normalised
    legacy = normalize_device_type(token)
    if legacy and legacy in RESOURCE_TYPE_FAMILY:
        return legacy
    return UNKNOWN_RESOURCE_TYPE


def resource_family(resource_type: str | None) -> str:
    """Family (coarse bin) for a universal resource type. Never guesses."""
    canonical = universal_resource_type(resource_type)
    if canonical in RESOURCE_TYPE_FAMILY:
        return RESOURCE_TYPE_FAMILY[canonical]
    delegated = category_for(resource_type)
    return delegated if delegated else FAMILY_OTHER


# ---------------------------------------------------------------------------
# Capabilities (spec sections 5, 12, 18-21, 30). A capability is a QUESTION
# ("can this resource report memory?"), never an assertion that data exists -
# assertions live in ResourceCapability rows / capability reports.
# ---------------------------------------------------------------------------
CAP_INVENTORY = "inventory"
CAP_AVAILABILITY = "availability"
CAP_HEALTH = "health"
CAP_CPU = "cpu"
CAP_MEMORY = "memory"
CAP_TEMPERATURE = "temperature"
CAP_UPTIME = "uptime"
CAP_INTERFACES = "interfaces"
CAP_BANDWIDTH = "bandwidth"
CAP_PACKET_STATISTICS = "packet_statistics"
CAP_ERRORS = "errors"
CAP_DISCARDS = "discards"
CAP_WAN = "wan"
CAP_SDWAN = "sdwan"
CAP_VPN = "vpn"
CAP_ROUTING = "routing"
CAP_BGP = "bgp"
CAP_OSPF = "ospf"
CAP_HIGH_AVAILABILITY = "high_availability"
CAP_SESSIONS = "sessions"
CAP_POLICIES = "policies"
CAP_NAT = "nat"
CAP_VLAN = "vlan"
CAP_POE = "poe"
CAP_CLIENTS = "clients"
CAP_WIRELESS = "wireless"
CAP_RF = "rf"
CAP_DISK = "disk"
CAP_PROCESSES = "processes"
CAP_SERVICES = "services"
CAP_APPLICATIONS = "applications"
CAP_STORAGE = "storage"
CAP_STORAGE_CAPACITY = "storage_capacity"
CAP_REPLICATION = "replication"
CAP_BACKUP = "backup"
CAP_CLOUD_METRICS = "cloud_metrics"
CAP_CLOUD_EVENTS = "cloud_events"
CAP_CLOUD_RESOURCES = "cloud_resources"
CAP_CLOUD_SERVICES = "cloud_services"
CAP_CLOUD_ACTIVITY = "cloud_activity"
CAP_TOPOLOGY = "topology"
CAP_DEPENDENCIES = "dependencies"
CAP_CONFIGURATION = "configuration"
CAP_ALERTS = "alerts"
CAP_LOGS = "logs"
CAP_ACTIONS = "actions"
CAP_REMEDIATION = "remediation"

ALL_CAPABILITIES: tuple[str, ...] = tuple(sorted({
    CAP_INVENTORY, CAP_AVAILABILITY, CAP_HEALTH, CAP_CPU, CAP_MEMORY,
    CAP_TEMPERATURE, CAP_UPTIME, CAP_INTERFACES, CAP_BANDWIDTH,
    CAP_PACKET_STATISTICS, CAP_ERRORS, CAP_DISCARDS, CAP_WAN, CAP_SDWAN,
    CAP_VPN, CAP_ROUTING, CAP_BGP, CAP_OSPF, CAP_HIGH_AVAILABILITY,
    CAP_SESSIONS, CAP_POLICIES, CAP_NAT, CAP_VLAN, CAP_POE, CAP_CLIENTS,
    CAP_WIRELESS, CAP_RF, CAP_DISK, CAP_PROCESSES, CAP_SERVICES,
    CAP_APPLICATIONS, CAP_STORAGE, CAP_STORAGE_CAPACITY, CAP_REPLICATION,
    CAP_BACKUP, CAP_CLOUD_METRICS, CAP_CLOUD_EVENTS, CAP_CLOUD_RESOURCES,
    CAP_CLOUD_SERVICES, CAP_CLOUD_ACTIVITY, CAP_TOPOLOGY, CAP_DEPENDENCIES,
    CAP_CONFIGURATION, CAP_ALERTS, CAP_LOGS, CAP_ACTIONS, CAP_REMEDIATION,
}))

#: Capabilities every collected resource answers regardless of type: they are
#: products of the collection pipeline itself, not of the device.
UNIVERSAL_CAPABILITIES: tuple[str, ...] = (
    CAP_INVENTORY, CAP_AVAILABILITY, CAP_HEALTH, CAP_ALERTS, CAP_LOGS,
    CAP_TOPOLOGY, CAP_DEPENDENCIES,
)


# ---------------------------------------------------------------------------
# Support / collection state vocabulary.
# ---------------------------------------------------------------------------
SUPPORT_SUPPORTED = "supported"
SUPPORT_UNSUPPORTED = "unsupported"
SUPPORT_UNKNOWN = "unknown"

SUPPORT_STATES: tuple[str, ...] = (
    SUPPORT_SUPPORTED, SUPPORT_UNSUPPORTED, SUPPORT_UNKNOWN,
)

COLLECTION_COLLECTED = "collected"
COLLECTION_EMPTY = "empty"
COLLECTION_UNSUPPORTED = "unsupported"
COLLECTION_UNAVAILABLE = "unavailable"
COLLECTION_FAILED = "failed"
COLLECTION_STALE = "stale"
COLLECTION_NOT_COLLECTED = "not_collected"
#: The spec's canonical answer for an unsupported capability (section 38):
#: ``supported = false``, ``status = not_available``, explicit ``reason``.
COLLECTION_NOT_AVAILABLE = "not_available"

COLLECTION_STATES: tuple[str, ...] = (
    COLLECTION_COLLECTED, COLLECTION_EMPTY, COLLECTION_UNSUPPORTED,
    COLLECTION_UNAVAILABLE, COLLECTION_FAILED, COLLECTION_STALE,
    COLLECTION_NOT_COLLECTED, COLLECTION_NOT_AVAILABLE,
)

# Explicit reason codes. These are what the API returns and what the UI shows;
# a bare "no data" is never returned.
REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED = "provider_capability_not_supported"
REASON_RESOURCE_TYPE_DOES_NOT_EXPOSE = "resource_type_does_not_expose_capability"
REASON_NOT_YET_COLLECTED = "capability_not_yet_collected"
REASON_COLLECTOR_FAILED = "collector_failed"
REASON_PROVIDER_AUTH_FAILED = "provider_authentication_failed"
REASON_NO_TELEMETRY_EVER = "no_telemetry_ever_received"
REASON_TELEMETRY_STALE = "telemetry_stale"
REASON_NO_MONITORING_SOURCE = "resource_has_no_monitoring_source"
REASON_PRIMARY_DATA_MISSING = "primary_inventory_not_collected"

REASONS: tuple[str, ...] = (
    REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED,
    REASON_RESOURCE_TYPE_DOES_NOT_EXPOSE,
    REASON_NOT_YET_COLLECTED,
    REASON_COLLECTOR_FAILED,
    REASON_PROVIDER_AUTH_FAILED,
    REASON_NO_TELEMETRY_EVER,
    REASON_TELEMETRY_STALE,
    REASON_NO_MONITORING_SOURCE,
    REASON_PRIMARY_DATA_MISSING,
)


# ---------------------------------------------------------------------------
# Status / health / lifecycle (spec sections 4, 6, 26, 27).
# ---------------------------------------------------------------------------
HEALTH_HEALTHY = "healthy"
HEALTH_WARNING = "warning"
HEALTH_CRITICAL = "critical"
HEALTH_UNKNOWN = "unknown"
HEALTH_MAINTENANCE = "maintenance"

HEALTH_STATES: tuple[str, ...] = (
    HEALTH_HEALTHY, HEALTH_WARNING, HEALTH_CRITICAL, HEALTH_UNKNOWN,
    HEALTH_MAINTENANCE,
)

LIFECYCLE_DISCOVERED = "discovered"
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_MAINTENANCE = "maintenance"
LIFECYCLE_RETIRED = "retired"
LIFECYCLE_DECOMMISSIONED = "decommissioned"

LIFECYCLE_STATES: tuple[str, ...] = (
    LIFECYCLE_DISCOVERED, LIFECYCLE_ACTIVE, LIFECYCLE_MAINTENANCE,
    LIFECYCLE_RETIRED, LIFECYCLE_DECOMMISSIONED,
)

#: Operational (availability) state. Kept separate from HEALTH: a router can be
#: UP (reachable) while its health is critical because a WAN member is down.
OPER_STATE_UNKNOWN = "unknown"
OPER_STATE_UP = "up"
OPER_STATE_DOWN = "down"
OPER_STATE_DEGRADED = "degraded"

OPER_STATES: tuple[str, ...] = (
    OPER_STATE_UP, OPER_STATE_DOWN, OPER_STATE_DEGRADED, OPER_STATE_UNKNOWN,
)

#: Raw stored status spellings -> operational state. Mirrors the bucket sets in
#: ``infrastructure_overview_service`` so the two aggregation paths can never
#: disagree about what "up"/"down" means.
_STATUS_UP: frozenset[str] = frozenset({
    "up", "online", "active", "ok", "enabled", "reachable", "succeeded", "healthy",
})
_STATUS_DEGRADED: frozenset[str] = frozenset({
    "warning", "warn", "degraded", "alerting", "maintenance", "partial",
})
_STATUS_DOWN: frozenset[str] = frozenset({
    "down", "offline", "critical", "failed", "error", "unreachable",
    "unhealthy", "disabled",
})


def operational_state(status: str | None) -> str:
    """Map a stored status spelling to the canonical operational state."""
    token = (status or "").strip().lower()
    if token in _STATUS_UP:
        return OPER_STATE_UP
    if token in _STATUS_DEGRADED:
        return OPER_STATE_DEGRADED
    if token in _STATUS_DOWN:
        return OPER_STATE_DOWN
    return OPER_STATE_UNKNOWN


def health_from_oper_state(state: str) -> str:
    """Operational state -> health bucket. ``unknown`` stays unknown."""
    if state == OPER_STATE_UP:
        return HEALTH_HEALTHY
    if state == OPER_STATE_DEGRADED:
        return HEALTH_WARNING
    if state == OPER_STATE_DOWN:
        return HEALTH_CRITICAL
    return HEALTH_UNKNOWN

#: 0-100 score per health bucket. Used by every aggregation path so the API and
#: the existing ``health_score`` service cannot drift.
HEALTH_SCORE: dict[str, int] = {
    HEALTH_HEALTHY: 100,
    HEALTH_WARNING: 60,
    HEALTH_CRITICAL: 0,
    HEALTH_UNKNOWN: 50,
    HEALTH_MAINTENANCE: 75,
}


# ---------------------------------------------------------------------------
# Relationship types (spec section 25). The canonical set is the union of the
# vocabulary already documented on ``ResourceRelationship`` and the additional
# names the spec requires; ``RELATIONSHIP_TYPES`` is what the API validates
# filters against.
# ---------------------------------------------------------------------------
REL_CONNECTED_TO = "connected_to"
REL_DEPENDS_ON = "depends_on"
REL_ROUTES_THROUGH = "routes_through"
REL_HOSTS = "hosts"
REL_RUNS_ON = "runs_on"
REL_COMMUNICATES_WITH = "communicates_with"
REL_PROTECTS = "protects"
REL_SERVES = "serves"
REL_ATTACHED_TO = "attached_to"
REL_MEMBER_OF = "member_of"
REL_LOCATED_AT = "located_at"
REL_BACKED_BY = "backed_by"
# Pre-existing vocabulary on ResourceRelationship, kept first-class:
REL_PROTECTED_BY = "protected_by"
REL_MANAGED_BY = "managed_by"
REL_MONITORED_BY = "monitored_by"
REL_LOGGED_TO = "logged_to"
REL_PEERS_WITH = "peers_with"
REL_HOSTED_ON = "hosted_on"
REL_CONTAINS = "contains"
REL_BACKUP_FOR = "backup_for"
REL_FAILOVER_TO = "failover_to"

RELATIONSHIP_TYPES: tuple[str, ...] = tuple(sorted({
    REL_CONNECTED_TO, REL_DEPENDS_ON, REL_ROUTES_THROUGH, REL_HOSTS, REL_RUNS_ON,
    REL_COMMUNICATES_WITH, REL_PROTECTS, REL_SERVES, REL_ATTACHED_TO,
    REL_MEMBER_OF, REL_LOCATED_AT, REL_BACKED_BY, REL_PROTECTED_BY,
    REL_MANAGED_BY, REL_MONITORED_BY, REL_LOGGED_TO, REL_PEERS_WITH,
    REL_HOSTED_ON, REL_CONTAINS, REL_BACKUP_FOR, REL_FAILOVER_TO,
}))

#: How an edge came to exist (spec section 25).
PROVENANCE_MANUAL = "manual"
PROVENANCE_DISCOVERED = "discovered"
PROVENANCE_INFERRED = "inferred"


# ---------------------------------------------------------------------------
# Interface vocabulary (spec section 15).
# ---------------------------------------------------------------------------
IFACE_PHYSICAL = "physical"
IFACE_LOGICAL = "logical"
IFACE_VLAN = "vlan"
IFACE_LOOPBACK = "loopback"
IFACE_TUNNEL = "tunnel"
IFACE_VIRTUAL = "virtual"
IFACE_WAN = "wan"
IFACE_SDWAN_MEMBER = "sdwan_member"
IFACE_VPN = "vpn"
IFACE_UNKNOWN = "unknown"

INTERFACE_TYPES: tuple[str, ...] = (
    IFACE_PHYSICAL, IFACE_LOGICAL, IFACE_VLAN, IFACE_LOOPBACK, IFACE_TUNNEL,
    IFACE_VIRTUAL, IFACE_WAN, IFACE_SDWAN_MEMBER, IFACE_VPN, IFACE_UNKNOWN,
)


# ---------------------------------------------------------------------------
# WAN / SD-WAN / VPN / routing vocabulary (spec sections 18-20).
# ---------------------------------------------------------------------------
WAN_ROLE_ACTIVE = "active"
WAN_ROLE_STANDBY = "standby"
WAN_ROLE_BACKUP = "backup"
WAN_ROLE_DISABLED = "disabled"

WAN_ROLES: tuple[str, ...] = (
    WAN_ROLE_ACTIVE, WAN_ROLE_STANDBY, WAN_ROLE_BACKUP, WAN_ROLE_DISABLED,
)

VPN_TYPE_SITE_TO_SITE = "site_to_site"
VPN_TYPE_REMOTE_ACCESS = "remote_access"
VPN_TYPE_IPSEC = "ipsec"
VPN_TYPE_SSL = "ssl"
VPN_TYPE_TUNNEL = "tunnel"
VPN_TYPE_OTHER = "other"

VPN_TYPES: tuple[str, ...] = (
    VPN_TYPE_SITE_TO_SITE, VPN_TYPE_REMOTE_ACCESS, VPN_TYPE_IPSEC,
    VPN_TYPE_SSL, VPN_TYPE_TUNNEL, VPN_TYPE_OTHER,
)

ROUTE_PROTOCOL_CONNECTED = "connected"
ROUTE_PROTOCOL_STATIC = "static"
ROUTE_PROTOCOL_BGP = "bgp"
ROUTE_PROTOCOL_OSPF = "ospf"
ROUTE_PROTOCOL_OTHER = "other"

ROUTE_PROTOCOLS: tuple[str, ...] = (
    ROUTE_PROTOCOL_CONNECTED, ROUTE_PROTOCOL_STATIC, ROUTE_PROTOCOL_BGP,
    ROUTE_PROTOCOL_OSPF, ROUTE_PROTOCOL_OTHER,
)

BGP_STATE_IDLE = "idle"
BGP_STATE_CONNECT = "connect"
BGP_STATE_ACTIVE = "active"
BGP_STATE_OPEN_SENT = "opensent"
BGP_STATE_OPEN_CONFIRM = "openconfirm"
BGP_STATE_ESTABLISHED = "established"
BGP_STATE_UNKNOWN = "unknown"

BGP_STATES: tuple[str, ...] = (
    BGP_STATE_IDLE, BGP_STATE_CONNECT, BGP_STATE_ACTIVE, BGP_STATE_OPEN_SENT,
    BGP_STATE_OPEN_CONFIRM, BGP_STATE_ESTABLISHED, BGP_STATE_UNKNOWN,
)

_BGP_STATE_ALIASES: dict[str, str] = {
    "open_sent": BGP_STATE_OPEN_SENT,
    "opensent": BGP_STATE_OPEN_SENT,
    "open_confirm": BGP_STATE_OPEN_CONFIRM,
    "openconfirm": BGP_STATE_OPEN_CONFIRM,
    "estab": BGP_STATE_ESTABLISHED,
    "established": BGP_STATE_ESTABLISHED,
    "connect": BGP_STATE_CONNECT,
    "active": BGP_STATE_ACTIVE,
    "idle": BGP_STATE_IDLE,
}


def normalize_bgp_state(value: str | None) -> str:
    """BGP FSM state -> canonical spelling. Unknown stays unknown."""
    token = (value or "").strip().lower().replace(" ", "_")
    return _BGP_STATE_ALIASES.get(token, BGP_STATE_UNKNOWN)


# ---------------------------------------------------------------------------
# Provider tokens (spec section 29). Presence here means "declared vocabulary",
# NOT "implemented": the provider registry reports ``wired`` separately.
# ---------------------------------------------------------------------------
PROVIDER_PRTG = "prtg"
PROVIDER_FORTIGATE = "fortigate"
PROVIDER_FORTIMANAGER = "fortimanager"
PROVIDER_FORTIANALYZER = "fortianalyzer"
PROVIDER_MERAKI = "meraki"
PROVIDER_ARUBA_CENTRAL = "aruba_central"
PROVIDER_AZURE = "azure"
PROVIDER_AWS = "aws"
PROVIDER_GCP = "gcp"
PROVIDER_CISCO = "cisco"
PROVIDER_PALOALTO = "paloalto"
PROVIDER_VMWARE = "vmware"
PROVIDER_SNMP = "snmp"
PROVIDER_ICMP = "icmp"
PROVIDER_SSH = "ssh"
PROVIDER_WMI = "wmi"
PROVIDER_WINRM = "winrm"
PROVIDER_REST = "rest"
PROVIDER_SYSLOG = "syslog"
PROVIDER_WEBHOOK = "webhook"
PROVIDER_ZABBIX = "zabbix"
PROVIDER_KUBERNETES = "kubernetes"
PROVIDER_CUSTOM_REST = "custom_rest"
PROVIDER_MANUAL = "manual"

KNOWN_PROVIDERS: tuple[str, ...] = (
    PROVIDER_PRTG, PROVIDER_FORTIGATE, PROVIDER_FORTIMANAGER,
    PROVIDER_FORTIANALYZER, PROVIDER_MERAKI, PROVIDER_ARUBA_CENTRAL,
    PROVIDER_AZURE, PROVIDER_AWS, PROVIDER_GCP, PROVIDER_CISCO,
    PROVIDER_PALOALTO, PROVIDER_VMWARE, PROVIDER_SNMP, PROVIDER_ICMP,
    PROVIDER_SSH, PROVIDER_WMI, PROVIDER_WINRM, PROVIDER_REST,
    PROVIDER_SYSLOG, PROVIDER_WEBHOOK, PROVIDER_ZABBIX, PROVIDER_KUBERNETES,
    PROVIDER_CUSTOM_REST, PROVIDER_MANUAL,
)


# ---------------------------------------------------------------------------
# Dependency-aware health aggregation constants (spec section 26). Single
# source of truth for the health service AND its tests.
# ---------------------------------------------------------------------------
#: A dependency in critical state drags its dependents' health down; warning
#: state drags it down less; unknown is a small, honest penalty.
DEPENDENCY_DOWN_PENALTY = 0.75
DEPENDENCY_WARNING_PENALTY = 0.30
DEPENDENCY_UNKNOWN_PENALTY = 0.10

#: How much of a parent's score comes from its own resources vs. its children.
PARENT_SELF_WEIGHT = 0.60
PARENT_CHILD_WEIGHT = 0.40

#: Telemetry older than this is STALE - never silently treated as current.
TELEMETRY_STALE_AFTER_SECONDS = 900

#: Time-to-live of a persisted capability assertion, in seconds. After this the
#: assertion is reported as stale rather than silently trusted.
CAPABILITY_ASSERTION_TTL_SECONDS = 86400


# ---------------------------------------------------------------------------
# Operational evidence + infrastructure graph hardening (Phase 7).
#
# The three vocabularies below answer three DIFFERENT questions about the same
# resource, and collapsing them is the mistake they exist to prevent:
#
#   identity stage  - how far have we got in *understanding* the resource?
#   binding         - is a collector actually responsible for its telemetry?
#   freshness       - is the telemetry we have still current?
#
# A resource can be fully IDENTIFIED and yet UNBOUND and NEVER_COLLECTED; or
# MONITORED with EXPIRED telemetry. Reporting either as "healthy" would be a lie,
# so each axis is reported separately.
# ---------------------------------------------------------------------------

# -- 1. Identity / readiness ladder -----------------------------------------
#: A probe saw something at this address. That is evidence, NOT a resource
#: identity - an unknown target legitimately stops here.
STAGE_DISCOVERED = "discovered"
#: A stable identity key exists (serial, provider resource id, address tuple).
STAGE_IDENTIFIED = "identified"
#: Type/vendor/model resolved by the recognition engine.
STAGE_CLASSIFIED = "classified"
#: Capability assertions resolved (what this resource can and cannot expose).
STAGE_CAPABILITY_RESOLVED = "capability_resolved"
#: At least one capability is actually collectable through a known provider.
STAGE_COLLECTABLE = "collectable"
#: Bound to a monitoring source AND carrying telemetry that is not expired.
STAGE_MONITORED = "monitored"
#: Was monitored, but its telemetry is stale/expired (or bindings withdrew).
STAGE_STALE = "stale"
#: Retired / decommissioned lifecycle - deliberately out of the collection path.
STAGE_RETIRED = "retired"

#: Ascending "how far along" order. ``stale``/``retired`` are terminal side
#: states rather than rungs, so they are reported but never used for ordering.
IDENTITY_STAGES: tuple[str, ...] = (
    STAGE_DISCOVERED, STAGE_IDENTIFIED, STAGE_CLASSIFIED,
    STAGE_CAPABILITY_RESOLVED, STAGE_COLLECTABLE, STAGE_MONITORED,
    STAGE_STALE, STAGE_RETIRED,
)

IDENTITY_STAGE_LADDER: tuple[str, ...] = (
    STAGE_DISCOVERED, STAGE_IDENTIFIED, STAGE_CLASSIFIED,
    STAGE_CAPABILITY_RESOLVED, STAGE_COLLECTABLE, STAGE_MONITORED,
)

#: Stages that describe something we cannot yet treat as an operational
#: resource. The UI uses this to keep "evidence" visually distinct from
#: "resource".
IDENTITY_STAGE_UNRESOLVED: frozenset[str] = frozenset(
    {STAGE_DISCOVERED, STAGE_STALE, STAGE_RETIRED}
)

# -- 2. Monitoring binding ---------------------------------------------------
#: A monitoring source is responsible for this resource AND its provider-native
#: identifier is preserved on the resource.
BINDING_BOUND = "bound"
#: No monitoring source is attached: discovery evidence only.
BINDING_UNBOUND = "unbound"
#: More than one active source claims this resource; telemetry ownership is not
#: provable, so it is reported rather than silently resolved to the first.
BINDING_AMBIGUOUS = "ambiguous"

BINDING_STATES: tuple[str, ...] = (BINDING_BOUND, BINDING_UNBOUND, BINDING_AMBIGUOUS)

# -- 3. Telemetry freshness --------------------------------------------------
#: Collected within the fresh window: safe to treat as the current state.
FRESHNESS_FRESH = "fresh"
#: Older than fresh, still inside the stale threshold: usable, visibly ageing.
FRESHNESS_AGING = "aging"
#: Older than the stale threshold: must not be presented as current.
FRESHNESS_STALE = "stale"
#: Older than the expiry threshold: treated as absent for operational decisions.
FRESHNESS_EXPIRED = "expired"
#: A collector is bound, but no sample has EVER arrived.
FRESHNESS_NEVER_COLLECTED = "never_collected"
#: No collection path exists at all (no binding, or provider cannot collect).
FRESHNESS_UNAVAILABLE = "unavailable"

FRESHNESS_STATES: tuple[str, ...] = (
    FRESHNESS_FRESH, FRESHNESS_AGING, FRESHNESS_STALE, FRESHNESS_EXPIRED,
    FRESHNESS_NEVER_COLLECTED, FRESHNESS_UNAVAILABLE,
)

#: Freshness states that permit "this is what the resource looks like now".
FRESHNESS_CURRENT: frozenset[str] = frozenset({FRESHNESS_FRESH, FRESHNESS_AGING})
#: Freshness states that forbid asserting a current operational conclusion.
FRESHNESS_NON_CURRENT: frozenset[str] = frozenset(
    {FRESHNESS_STALE, FRESHNESS_EXPIRED, FRESHNESS_NEVER_COLLECTED, FRESHNESS_UNAVAILABLE}
)

#: Freshness windows (seconds). Derived from the existing stale threshold so the
#: resource vocabulary and the health service can never disagree: a sample is
#: AGING from 60% of the stale window and STALE beyond it.
TELEMETRY_AGING_AFTER_SECONDS = int(TELEMETRY_STALE_AFTER_SECONDS * 0.6)
TELEMETRY_EXPIRED_AFTER_SECONDS = TELEMETRY_STALE_AFTER_SECONDS * 4

#: Reason codes for freshness (never a bare "no data").
REASON_FRESHNESS_NEVER_COLLECTED = "telemetry_never_collected"
REASON_FRESHNESS_EXPIRED = REASON_TELEMETRY_STALE
REASON_FRESHNESS_UNAVAILABLE = REASON_NO_MONITORING_SOURCE

# -- 4. Evidence provenance --------------------------------------------------
#: The collector read this value from the device/provider itself.
EVIDENCE_OBSERVED = "observed"
#: Computed by HSDEEP from observed values (scores, rollups, diffs).
EVIDENCE_DERIVED = "derived"
#: Real but joined from another table with a different collection cadence.
EVIDENCE_CONTEXT = "context"
#: Produced by a model/simulation - never mixed into the observed record.
EVIDENCE_SIMULATED = "simulated"

EVIDENCE_PROVENANCE_STATES: tuple[str, ...] = (
    EVIDENCE_OBSERVED, EVIDENCE_DERIVED, EVIDENCE_CONTEXT, EVIDENCE_SIMULATED,
)

#: Provenance that may be used as an operational fact on its own.
EVIDENCE_AUTHORITATIVE: frozenset[str] = frozenset({EVIDENCE_OBSERVED})

# -- 5. Relationship evidence state -----------------------------------------
#: The adjacency was observed recently enough to be current topology.
RELATIONSHIP_ACTIVE = "active"
#: Observed before, but not re-observed inside the staleness window.
RELATIONSHIP_STALE = "stale"
#: Explicitly withdrawn (e.g. an interface was decommissioned).
RELATIONSHIP_REMOVED = "removed"

#: The canonical answer when NO adjacency evidence exists. This is a state, not
#: an empty list pretending to be complete topology.
NO_RELATIONSHIP_EVIDENCE = "no_relationship_evidence"

#: How long an observed adjacency stays "current" without re-observation. Kept
#: generous because LLDP/ARP are polled far less often than metrics.
RELATIONSHIP_STALE_AFTER_SECONDS = 86400 * 7

#: Evidence sources (what produced a relationship). Only sources that can
#: actually observe an adjacency are listed - a relationship is never inferred
#: from a shared subnet or a similar name.
EVIDENCE_SOURCE_LLDP = "lldp"
EVIDENCE_SOURCE_CDP = "cdp"
EVIDENCE_SOURCE_ARP = "arp"
EVIDENCE_SOURCE_MAC = "mac_table"
EVIDENCE_SOURCE_SNMP = "snmp"
EVIDENCE_SOURCE_ROUTING = "routing"
EVIDENCE_SOURCE_BGP = "bgp"
EVIDENCE_SOURCE_SDWAN = "sdwan"
EVIDENCE_SOURCE_CLOUD = "cloud"
EVIDENCE_SOURCE_MANUAL = "manual"
#: Recorded structural binding (Phase 9.6): an edge materialised from a column or
#: row the platform already persisted (e.g. ``devices.parent_resource_id``), not
#: from a live observation. Deliberately NOT in the classification table below:
#: nothing recorded HOW the binding was obtained, so it stays unclassified rather
#: than being presented as something HSDEEP observed itself.
EVIDENCE_SOURCE_INVENTORY = "inventory"

RELATIONSHIP_EVIDENCE_SOURCES: tuple[str, ...] = (
    EVIDENCE_SOURCE_LLDP, EVIDENCE_SOURCE_CDP, EVIDENCE_SOURCE_ARP,
    EVIDENCE_SOURCE_MAC, EVIDENCE_SOURCE_SNMP, EVIDENCE_SOURCE_ROUTING,
    EVIDENCE_SOURCE_BGP, EVIDENCE_SOURCE_SDWAN, EVIDENCE_SOURCE_CLOUD,
    EVIDENCE_SOURCE_MANUAL, EVIDENCE_SOURCE_INVENTORY,
)

#: Key under which a relationship's per-source evidence ledger is stored in
#: ``ResourceRelationship.attributes``. A later LLDP-only poll must not erase
#: earlier ARP evidence, so each source keeps its own last-seen entry.
RELATIONSHIP_EVIDENCE_LEDGER_KEY = "evidence_sources"


# -- 6. Recognition status (discovery) ---------------------------------------
#: The recogniser resolved a type/vendor/model from the observed evidence.
RECOGNITION_IDENTIFIED = "identified"
#: Something answered, but we could not say what it is. The probe result is
#: still valid EVIDENCE - it is simply not a classified resource.
RECOGNITION_UNKNOWN = "unknown"

RECOGNITION_STATES: tuple[str, ...] = (RECOGNITION_IDENTIFIED, RECOGNITION_UNKNOWN)


# ---------------------------------------------------------------------------
# 7. Evidence classification (Phase 9.4).
#
# ``transport`` answers HOW evidence was obtained (icmp/tcp/snmp/ssh/http/api/
# agent). It does NOT answer WHAT kind of interaction produced it, and the two
# are not interchangeable: an SNMP walk and a vendor REST call are different
# kinds of claim about a resource even when both cross the network.
#
# Three classifications exist, and they are the whole set. There is deliberately
# no fourth "other" bucket, because a classification nobody can name is a
# classification nobody can reason about later:
#
#   ACTIVE   HSDEEP initiated an interaction with the target itself.
#   PASSIVE  the evidence was observed without interacting with the target.
#   API      HSDEEP asked a provider/controller/cloud API about the target.
#
# Classification is PROVENANCE ONLY. It never identifies a resource, is never an
# identity key, and never feeds recognition - identity stays deterministic and
# evidence-based (recognition.identity / discovery.identity).
# ---------------------------------------------------------------------------
EVIDENCE_ACTIVE = "active"
EVIDENCE_PASSIVE = "passive"
EVIDENCE_API = "api"

EVIDENCE_CLASSIFICATIONS: tuple[str, ...] = (
    EVIDENCE_ACTIVE,
    EVIDENCE_PASSIVE,
    EVIDENCE_API,
)

#: Default classification for every transport this repository implements.
#:
#: This is a DATA table on purpose: adding a transport is one row, and reviewing
#: a classification is reading one line. A transport absent from this table
#: resolves to None (see ``evidence_classification``) and is NEVER guessed into
#: ACTIVE - the first passive transport to arrive would otherwise be silently
#: mislabelled as something HSDEEP did to the target.
TRANSPORT_EVIDENCE_CLASSIFICATION: dict[str, str] = {
    # HSDEEP interacts with the target itself.
    "icmp": EVIDENCE_ACTIVE,
    "tcp": EVIDENCE_ACTIVE,
    "udp": EVIDENCE_ACTIVE,
    "snmp": EVIDENCE_ACTIVE,
    "ssh": EVIDENCE_ACTIVE,
    "http": EVIDENCE_ACTIVE,
    "https": EVIDENCE_ACTIVE,
    "wmi": EVIDENCE_ACTIVE,
    "winrm": EVIDENCE_ACTIVE,
    # HSDEEP asks a provider/controller/cloud API about the target.
    "api": EVIDENCE_API,
    # An installed collector pushes observations it already made, so nothing in
    # HSDEEP interacted with the target to obtain them.
    "agent": EVIDENCE_PASSIVE,
}


def evidence_classification(transport: str | None) -> str | None:
    """Canonical classification for ``transport``, or None when it is unknown.

    None means "this transport is not one this platform can classify", and is
    returned instead of ACTIVE on purpose. A caller that knows better states the
    classification explicitly (``DiscoveryEvidence(classification=...)``), which
    is exactly what a future passive (e.g. flow/Zeek) provider must do.
    """
    if not transport:
        return None
    return TRANSPORT_EVIDENCE_CLASSIFICATION.get(str(transport).strip().lower())


#: Evidence classification for each RELATIONSHIP evidence source.
#:
#: ``ResourceRelationship.discovered_by`` records WHICH observation produced an
#: edge (the ``EVIDENCE_SOURCE_*`` tokens above, plus free text). The topology UI
#: must show an edge's provenance with the same three-way classification as
#: discovery evidence, so the mapping lives here instead of being re-derived in
#: the frontend.
#:
#: Rows follow the transport table's rule: a source absent from this table
#: resolves to None and is never guessed. ``EVIDENCE_SOURCE_MANUAL`` is
#: deliberately absent - an operator-asserted edge was not collected by any
#: mechanism, so it has no collection classification at all.
RELATIONSHIP_SOURCE_EVIDENCE_CLASSIFICATION: dict[str, str] = {
    # HSDEEP interrogated the device(s) and read the adjacency from it.
    EVIDENCE_SOURCE_LLDP: EVIDENCE_ACTIVE,
    EVIDENCE_SOURCE_CDP: EVIDENCE_ACTIVE,
    EVIDENCE_SOURCE_ARP: EVIDENCE_ACTIVE,
    EVIDENCE_SOURCE_MAC: EVIDENCE_ACTIVE,
    EVIDENCE_SOURCE_SNMP: EVIDENCE_ACTIVE,
    EVIDENCE_SOURCE_ROUTING: EVIDENCE_ACTIVE,
    EVIDENCE_SOURCE_BGP: EVIDENCE_ACTIVE,
    # Answered by a provider/controller/cloud API rather than by the device.
    EVIDENCE_SOURCE_SDWAN: EVIDENCE_API,
    EVIDENCE_SOURCE_CLOUD: EVIDENCE_API,
}


def relationship_evidence_classification(source: str | None) -> str | None:
    """Classification for a relationship's recorded evidence source, or None.

    None means "this source is not one we can classify" and is returned rather
    than ACTIVE, so an unrecognised ``discovered_by`` value is displayed as
    unclassified instead of being presented as something HSDEEP observed itself.
    """
    if not source:
        return None
    return RELATIONSHIP_SOURCE_EVIDENCE_CLASSIFICATION.get(str(source).strip().lower())
