"""
Capability model (spec sections 5, 12, 30).

Answers exactly one question per (resource, capability) pair: *can this
resource report this capability, and if not, why not?* It never answers "what
is the value" - that is telemetry's job.

Three-way support state, never two
----------------------------------
``SUPPORT_SUPPORTED`` / ``SUPPORT_UNSUPPORTED`` / ``SUPPORT_UNKNOWN``.
"Unknown" means we have not asked, or the provider has not been evaluated.
Collapsing unknown into unsupported would let the UI hide a resource that
simply has not been polled yet, which is how platforms end up lying.

A capability that is not supported is ALWAYS returned as ``supported=False``
with an explicit ``reason`` code (spec section 38). Nothing here ever
fabricates a value.

Profiles are data, not logic
----------------------------
``RESOURCE_TYPE_CAPABILITIES`` - what a resource *type* exposes in principle.
``PROVIDER_CAPABILITIES``      - what a *provider* can collect, only ever
                                 asserted for providers that are actually
                                 wired (see ``providers.py``).
Adding a provider or a resource type means adding a row, never editing the
resolver.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.services.infrastructure_intelligence import vocabulary as V

#: How an assertion came to exist.
SOURCE_COLLECTOR = "collector"
SOURCE_OPERATOR = "operator"
SOURCE_RESOLVED = "resolved"
SOURCE_DERIVED = "derived"


@dataclass(frozen=True)
class CapabilityAssertion:
    """One capability answer for one resource.

    ``support`` is the three-way state; ``status`` is the collection state
    (``collected`` when data actually exists, ``not_collected`` when the
    provider can supply it but has not yet). ``reason`` is mandatory whenever
    ``support`` is not ``SUPPORT_SUPPORTED``.
    """

    capability: str
    support: str
    status: str
    reason: str | None = None
    provider: str | None = None
    source: str = SOURCE_RESOLVED
    asserted_at: datetime | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def supported(self) -> bool:
        """True only for an explicit ``SUPPORT_SUPPORTED``. Never for unknown."""
        return self.support == V.SUPPORT_SUPPORTED

    def to_dict(self) -> dict:
        # ``supported`` is serialised explicitly (spec section 38) so a client
        # never has to infer it from the support string.
        return {
            "capability": self.capability,
            "supported": self.supported,
            "support": self.support,
            "status": self.status,
            "reason": self.reason,
            "provider": self.provider,
            "source": self.source,
            "asserted_at": self.asserted_at.isoformat() if self.asserted_at else None,
            "evidence": dict(self.evidence or {}),
        }


def supported(
    capability: str,
    *,
    status: str = V.COLLECTION_NOT_COLLECTED,
    provider: str | None = None,
    source: str = SOURCE_RESOLVED,
    evidence: dict | None = None,
) -> CapabilityAssertion:
    """The resource CAN report this capability (data may not have arrived yet)."""
    return CapabilityAssertion(
        capability=capability,
        support=V.SUPPORT_SUPPORTED,
        status=status,
        provider=provider,
        source=source,
        evidence=evidence or {},
    )


def unsupported(
    capability: str,
    reason: str,
    *,
    provider: str | None = None,
    source: str = SOURCE_RESOLVED,
    evidence: dict | None = None,
) -> CapabilityAssertion:
    """The resource/provider CANNOT report this capability. Reason is required."""
    if not reason:
        raise ValueError("unsupported() requires an explicit reason code")
    return CapabilityAssertion(
        capability=capability,
        support=V.SUPPORT_UNSUPPORTED,
        status=V.COLLECTION_UNSUPPORTED,
        reason=reason,
        provider=provider,
        source=source,
        evidence=evidence or {},
    )


def unknown_capability(
    capability: str,
    reason: str,
    *,
    provider: str | None = None,
    source: str = SOURCE_RESOLVED,
    evidence: dict | None = None,
) -> CapabilityAssertion:
    """We do not know yet whether this capability is collectable."""
    return CapabilityAssertion(
        capability=capability,
        support=V.SUPPORT_UNKNOWN,
        status=V.COLLECTION_NOT_COLLECTED,
        reason=reason,
        provider=provider,
        source=source,
        evidence=evidence or {},
    )


@dataclass
class CapabilityReport:
    """The full capability picture for ONE resource."""

    resource_id: uuid.UUID | None
    resource_type: str
    family: str
    provider: str | None
    assertions: dict[str, CapabilityAssertion] = field(default_factory=dict)

    def assertion(self, capability: str) -> CapabilityAssertion:
        """Assertion for one capability, or an explicit unknown - never a raise."""
        existing = self.assertions.get(capability)
        if existing is not None:
            return existing
        return unknown_capability(
            capability, V.REASON_NOT_YET_COLLECTED, provider=self.provider
        )

    def supports(self, capability: str) -> bool:
        return self.assertion(capability).supported

    def capabilities_in(self, support_state: str) -> list[str]:
        return sorted(
            c for c, a in self.assertions.items() if a.support == support_state
        )

    def to_dict(self) -> dict:
        ordered = sorted(self.assertions.values(), key=lambda a: a.capability)
        return {
            "resource_id": str(self.resource_id) if self.resource_id else None,
            "resource_type": self.resource_type,
            "family": self.family,
            "provider": self.provider,
            "capabilities": sorted(self.assertions),
            "supported": self.capabilities_in(V.SUPPORT_SUPPORTED),
            "unsupported": [
                {"capability": a.capability, "reason": a.reason}
                for a in ordered if a.support == V.SUPPORT_UNSUPPORTED
            ],
            "unknown": [
                {"capability": a.capability, "reason": a.reason}
                for a in ordered if a.support == V.SUPPORT_UNKNOWN
            ],
            "assertions": {a.capability: a.to_dict() for a in ordered},
            "counts": {
                V.SUPPORT_SUPPORTED: len(self.capabilities_in(V.SUPPORT_SUPPORTED)),
                V.SUPPORT_UNSUPPORTED: len(self.capabilities_in(V.SUPPORT_UNSUPPORTED)),
                V.SUPPORT_UNKNOWN: len(self.capabilities_in(V.SUPPORT_UNKNOWN)),
            },
        }

# ---------------------------------------------------------------------------
# Resource-type capability profiles (data only).
#
# These describe what a resource TYPE exposes *in principle*. They are NOT a
# promise that a device answered: whether the wired provider can collect it is
# decided by providers.PROVIDER_CAPABILITIES, and whether data exists is
# decided by telemetry. A type absent from this table exposes ONLY
# UNIVERSAL_CAPABILITIES.
# ---------------------------------------------------------------------------
_C_INV = V.CAP_INVENTORY
_C_AVAIL = V.CAP_AVAILABILITY
_C_HEALTH = V.CAP_HEALTH
_C_CPU = V.CAP_CPU
_C_MEM = V.CAP_MEMORY
_C_TEMP = V.CAP_TEMPERATURE
_C_UPTIME = V.CAP_UPTIME
_C_IF = V.CAP_INTERFACES
_C_BW = V.CAP_BANDWIDTH
_C_PKTS = V.CAP_PACKET_STATISTICS
_C_ERR = V.CAP_ERRORS
_C_DISC = V.CAP_DISCARDS
_C_WAN = V.CAP_WAN
_C_SDWAN = V.CAP_SDWAN
_C_VPN = V.CAP_VPN
_C_ROUTE = V.CAP_ROUTING
_C_BGP = V.CAP_BGP
_C_OSPF = V.CAP_OSPF
_C_HA = V.CAP_HIGH_AVAILABILITY
_C_SESS = V.CAP_SESSIONS
_C_POL = V.CAP_POLICIES
_C_NAT = V.CAP_NAT
_C_VLAN = V.CAP_VLAN
_C_POE = V.CAP_POE
_C_CLIENTS = V.CAP_CLIENTS
_C_WIFI = V.CAP_WIRELESS
_C_RF = V.CAP_RF
_C_DISK = V.CAP_DISK
_C_PROC = V.CAP_PROCESSES
_C_SVC = V.CAP_SERVICES
_C_APP = V.CAP_APPLICATIONS
_C_STOR = V.CAP_STORAGE
_C_STORCAP = V.CAP_STORAGE_CAPACITY
_C_REPL = V.CAP_REPLICATION
_C_BACKUP = V.CAP_BACKUP
_C_CMETRIC = V.CAP_CLOUD_METRICS
_C_CEVENT = V.CAP_CLOUD_EVENTS
_C_CRES = V.CAP_CLOUD_RESOURCES
_C_CSVC = V.CAP_CLOUD_SERVICES
_C_CACT = V.CAP_CLOUD_ACTIVITY
_C_TOPO = V.CAP_TOPOLOGY
_C_DEP = V.CAP_DEPENDENCIES
_C_CONF = V.CAP_CONFIGURATION
_C_ALERT = V.CAP_ALERTS
_C_LOG = V.CAP_LOGS

#: Capability groups reused across many resource types.
_G_DEVICE_BASE = (_C_INV, _C_AVAIL, _C_HEALTH, _C_UPTIME, _C_ALERT, _C_LOG, _C_TOPO)
_G_NET_DEVICE = _G_DEVICE_BASE + (
    _C_CPU, _C_MEM, _C_IF, _C_BW, _C_PKTS, _C_ERR, _C_DISC, _C_CONF,
)
_G_FIREWALL = _G_NET_DEVICE + (
    _C_WAN, _C_SDWAN, _C_VPN, _C_ROUTE, _C_BGP, _C_OSPF, _C_HA,
    _C_SESS, _C_POL, _C_NAT,
)
_G_SWITCH = _G_DEVICE_BASE + (
    _C_CPU, _C_MEM, _C_TEMP, _C_IF, _C_BW, _C_PKTS, _C_ERR, _C_DISC,
    _C_VLAN, _C_POE, _C_CLIENTS, _C_CONF,
)
_G_COMPUTE = _G_DEVICE_BASE + (
    _C_CPU, _C_MEM, _C_DISK, _C_STOR, _C_STORCAP, _C_IF, _C_BW, _C_ERR,
    _C_PROC, _C_SVC, _C_APP, _C_CONF,
)
_G_CLOUD_TENANT = (
    _C_INV, _C_AVAIL, _C_HEALTH, _C_CRES, _C_CSVC, _C_CMETRIC, _C_CEVENT,
    _C_CACT, _C_TOPO, _C_DEP, _C_ALERT, _C_LOG,
)
_G_CLOUD_WORKLOAD = _G_DEVICE_BASE + (
    _C_CPU, _C_MEM, _C_DISK, _C_STOR, _C_CMETRIC, _C_CEVENT, _C_CACT, _C_CONF,
)
_G_ENDPOINT = _G_DEVICE_BASE + (_C_CPU, _C_MEM, _C_DISK, _C_IF, _C_APP)
_G_APPLICATION = (
    _C_INV, _C_AVAIL, _C_HEALTH, _C_APP, _C_SVC, _C_DEP, _C_TOPO,
    _C_ALERT, _C_LOG,
)
_G_STORAGE = _G_DEVICE_BASE + (
    _C_STOR, _C_STORCAP, _C_IF, _C_ERR, _C_DISC, _C_REPL, _C_BACKUP, _C_CONF,
)

RESOURCE_TYPE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    # NETWORK / SECURITY
    "firewall": _G_FIREWALL,
    "security_gateway": _G_FIREWALL,
    "security_appliance": _G_FIREWALL,
    "router": _G_NET_DEVICE + (
        _C_WAN, _C_SDWAN, _C_VPN, _C_ROUTE, _C_BGP, _C_OSPF, _C_HA, _C_CLIENTS,
    ),
    "gateway": _G_NET_DEVICE + (
        _C_WAN, _C_SDWAN, _C_VPN, _C_ROUTE, _C_BGP, _C_OSPF, _C_NAT,
    ),
    "vpn_gateway": _G_NET_DEVICE + (_C_VPN, _C_WAN, _C_ROUTE, _C_SESS, _C_HA),
    "switch": _G_SWITCH,
    "load_balancer": _G_DEVICE_BASE + (
        _C_IF, _C_BW, _C_SESS, _C_CPU, _C_MEM, _C_CONF,
    ),
    "cloud_load_balancer": (
        _C_AVAIL, _C_HEALTH, _C_SESS, _C_BW, _C_CMETRIC, _C_CEVENT, _C_TOPO,
    ),
    "network_device": _G_NET_DEVICE,
    "network_appliance": _G_NET_DEVICE,
    "wan_optimizer": _G_NET_DEVICE + (_C_WAN, _C_SDWAN, _C_VPN),
    "sensor": _G_DEVICE_BASE,
    # WIRELESS
    "access_point": _G_DEVICE_BASE + (
        _C_CPU, _C_MEM, _C_WIFI, _C_CLIENTS, _C_RF, _C_IF, _C_BW, _C_ERR,
        _C_CONF,
    ),
    "wireless_controller": _G_DEVICE_BASE + (
        _C_CPU, _C_MEM, _C_WIFI, _C_CLIENTS, _C_RF, _C_IF, _C_CONF,
    ),
    # COMPUTE / VIRTUALIZATION / CONTAINER
    "server": _G_COMPUTE + (_C_TEMP,),
    "virtual_machine": _G_COMPUTE,
    "vm": _G_COMPUTE,
    "hypervisor": _G_COMPUTE + (_C_TEMP,),
    "container_host": _G_COMPUTE,
    # CLOUD
    "cloud_platform": _G_CLOUD_TENANT,
    "cloud_account": _G_CLOUD_TENANT,
    "cloud_subscription": _G_CLOUD_TENANT,
    "cloud_resource": _G_CLOUD_WORKLOAD,
    "cloud_service": (
        _C_AVAIL, _C_HEALTH, _C_CMETRIC, _C_CEVENT, _C_CACT, _C_APP, _C_DEP,
    ),
    "cloud_network": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_ROUTE, _C_VPN, _C_WAN, _C_CMETRIC,
        _C_CEVENT, _C_TOPO, _C_DEP,
    ),
    "cloud_database": _G_DEVICE_BASE + (
        _C_CPU, _C_MEM, _C_STOR, _C_STORCAP, _C_REPL, _C_BACKUP, _C_SESS,
        _C_CMETRIC, _C_CEVENT, _C_DEP,
    ),
    "cloud_storage": _G_DEVICE_BASE + (
        _C_STOR, _C_STORCAP, _C_REPL, _C_BACKUP, _C_CMETRIC, _C_CEVENT,
    ),
    # ENDPOINT
    "workstation": _G_ENDPOINT,
    "laptop": _G_ENDPOINT,
    "endpoint": _G_ENDPOINT,
    "mobile_device": (_C_INV, _C_AVAIL, _C_HEALTH, _C_APP, _C_ALERT, _C_TOPO),
    "iot_device": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_UPTIME, _C_TEMP, _C_ALERT, _C_LOG,
    ),
    # APPLICATION / DATABASE
    "application": _G_APPLICATION,
    "service": _G_APPLICATION,
    "web_service": _G_APPLICATION,
    "api": (_C_INV, _C_AVAIL, _C_HEALTH, _C_DEP, _C_ALERT, _C_LOG),
    "database": _G_DEVICE_BASE + (
        _C_CPU, _C_MEM, _C_DISK, _C_STOR, _C_STORCAP, _C_REPL, _C_BACKUP,
        _C_SESS, _C_APP,
    ),
    # STORAGE
    "storage_system": _G_STORAGE,
    "storage_volume": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_STOR, _C_STORCAP, _C_REPL, _C_BACKUP,
        _C_ALERT,
    ),
    "object_storage": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_STOR, _C_STORCAP, _C_REPL, _C_BACKUP,
        _C_CMETRIC,
    ),
    "file_storage": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_STOR, _C_STORCAP, _C_REPL, _C_BACKUP,
    ),
    "backup_resource": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_STOR, _C_STORCAP, _C_BACKUP, _C_REPL,
    ),
    "backup_appliance": _G_STORAGE,
    # SECURITY SERVICES
    "security_service": (
        _C_INV, _C_AVAIL, _C_HEALTH, _C_CONF, _C_ALERT, _C_LOG,
        _C_CMETRIC, _C_CEVENT,
    ),
    # OTHER
    "custom_device": _G_DEVICE_BASE + (_C_IF, _C_CPU, _C_MEM),
    "custom_resource": _G_DEVICE_BASE + (_C_DEP,),
}


def type_exposes(resource_type: str | None) -> tuple[str, ...]:
    """Capabilities a resource TYPE exposes in principle.

    An unknown type exposes only ``UNIVERSAL_CAPABILITIES`` - it is never
    assumed to support more because someone hoped it would.
    """
    canonical = V.universal_resource_type(resource_type)
    return RESOURCE_TYPE_CAPABILITIES.get(canonical, V.UNIVERSAL_CAPABILITIES)


def resolve_capabilities(
    resource_type: str | None,
    provider: str | None,
    *,
    resource_id: uuid.UUID | None = None,
    provider_capabilities: frozenset[str] | None = None,
    collected: object = (),
    asserted: object = None,
) -> CapabilityReport:
    """Build the capability report for one resource.

    Precedence, highest first:
      1. an explicit assertion (operator/collector evidence),
      2. a capability we actually collected data for,
      3. the resource-type profile vs. the provider's declared capabilities.

    ``provider_capabilities`` is the set of capabilities the WIRED provider can
    collect. Passing ``None`` means "this provider has not been evaluated", in
    which case capabilities that are otherwise supported stay ``unknown``
    rather than being falsely reported as supported or unsupported.
    """
    canonical_type = V.universal_resource_type(resource_type)
    exposed = set(type_exposes(canonical_type))
    provider_caps = set(provider_capabilities) if provider_capabilities is not None else None
    collected_caps = {str(c) for c in collected}  # type: ignore[union-attr]
    asserted_map = dict(asserted or {})

    candidates: set[str] = set(exposed) | collected_caps | set(asserted_map)
    if provider_caps:
        candidates |= provider_caps

    report = CapabilityReport(
        resource_id=resource_id,
        resource_type=canonical_type,
        family=V.resource_family(canonical_type),
        provider=provider,
    )

    for capability in candidates:
        if provider is None and capability not in collected_caps:
            report.assertions[capability] = unknown_capability(
                capability, V.REASON_NO_MONITORING_SOURCE, provider=provider
            )
            continue
        if capability in collected_caps:
            report.assertions[capability] = supported(
                capability,
                status=V.COLLECTION_COLLECTED,
                provider=provider,
                source=SOURCE_COLLECTOR,
            )
            continue
        if capability not in exposed:
            report.assertions[capability] = unsupported(
                capability, V.REASON_RESOURCE_TYPE_DOES_NOT_EXPOSE, provider=provider
            )
            continue
        if provider_caps is None:
            # The provider exists on this resource but has NOT been evaluated
            # (no collector registered for it). "We do not know" is the honest
            # answer - reporting `supported` would claim a capability nobody
            # verified, and reporting `unsupported` would claim it is impossible.
            report.assertions[capability] = unknown_capability(
                capability, V.REASON_NOT_YET_COLLECTED, provider=provider
            )
            continue
        if capability not in provider_caps:
            report.assertions[capability] = unsupported(
                capability,
                V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED,
                provider=provider,
            )
            continue
        report.assertions[capability] = supported(
            capability, status=V.COLLECTION_NOT_COLLECTED, provider=provider
        )

    for capability, assertion in asserted_map.items():
        report.assertions[capability] = assertion

    return report
