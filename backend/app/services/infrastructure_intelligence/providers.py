"""
Provider capability registry (spec sections 29, 30).

Maps a PROVIDER token to the canonical capabilities it can collect, and reports
which providers are actually WIRED (a collector class is registered for them)
versus merely DECLARED in the vocabulary.

Honesty rules
-------------
* ``wired`` is derived from the live ``CollectorRegistry`` at call time - a
  provider is never reported as implemented because it is in a list in this
  file.
* A provider that is not wired returns ``capabilities = None`` (i.e. "not
  evaluated"), which the capability resolver turns into ``SUPPORT_UNKNOWN``.
  It is never turned into a false ``supported`` or a false ``unsupported``.
* The declared capability sets below describe what each WIRED collector is
  implemented to emit through ``NormalizedBundle``. They are an upper bound,
  not a promise for a specific device: the collection pipeline records what was
  ACTUALLY collected, and that always wins over this table.

Adding a provider means: write the collector (existing Collector Framework),
then add one row here. No core module is edited.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.infrastructure_intelligence import vocabulary as V

#: Provider adapter kind.
KIND_NETWORK = "network"
KIND_CLOUD = "cloud"
KIND_CONTROLLER = "controller"
KIND_PLATFORM = "platform"
KIND_AGENT = "agent"
KIND_MANUAL = "manual"

# Capability groups only (kept terse so a new provider row is one line).

_ALL_COLLECTOR = frozenset({
    V.CAP_INVENTORY, V.CAP_AVAILABILITY, V.CAP_ALERTS, V.CAP_LOGS,
})
_FORTIGATE = _ALL_COLLECTOR | {
    V.CAP_HEALTH, V.CAP_CPU, V.CAP_MEMORY, V.CAP_UPTIME, V.CAP_SESSIONS,
    V.CAP_INTERFACES, V.CAP_BANDWIDTH, V.CAP_ERRORS, V.CAP_DISCARDS,
    V.CAP_WAN, V.CAP_SDWAN, V.CAP_VPN, V.CAP_ROUTING, V.CAP_BGP,
    V.CAP_OSPF, V.CAP_HIGH_AVAILABILITY,
}
_SNMP = _ALL_COLLECTOR | {
    V.CAP_HEALTH, V.CAP_CPU, V.CAP_MEMORY, V.CAP_TEMPERATURE, V.CAP_UPTIME,
    V.CAP_INTERFACES, V.CAP_BANDWIDTH, V.CAP_PACKET_STATISTICS, V.CAP_ERRORS,
    V.CAP_DISCARDS, V.CAP_VLAN, V.CAP_POE, V.CAP_TOPOLOGY,
}
_SERVER_AGENT = _ALL_COLLECTOR | {
    V.CAP_HEALTH, V.CAP_CPU, V.CAP_MEMORY, V.CAP_DISK, V.CAP_UPTIME,
    V.CAP_INTERFACES, V.CAP_BANDWIDTH, V.CAP_PROCESSES, V.CAP_SERVICES,
    V.CAP_APPLICATIONS,
}
_CLOUD = _ALL_COLLECTOR | {
    V.CAP_HEALTH, V.CAP_CLOUD_RESOURCES, V.CAP_CLOUD_SERVICES,
    V.CAP_CLOUD_METRICS, V.CAP_CLOUD_EVENTS, V.CAP_CLOUD_ACTIVITY,
    V.CAP_STORAGE, V.CAP_STORAGE_CAPACITY, V.CAP_TOPOLOGY, V.CAP_DEPENDENCIES,
}
_MONITORING_PLATFORM = _ALL_COLLECTOR | {
    V.CAP_HEALTH, V.CAP_CPU, V.CAP_MEMORY, V.CAP_DISK, V.CAP_UPTIME,
    V.CAP_INTERFACES, V.CAP_BANDWIDTH, V.CAP_SESSIONS, V.CAP_WIRELESS,
    V.CAP_CLIENTS, V.CAP_STORAGE,
}
#: A lab / topology controller: authoritative for its OWN inventory and wiring
#: (PNETLab knows exactly which interface is patched to which). It does not
#: measure device performance, so only the capabilities it really emits appear.
_TOPOLOGY_CONTROLLER = _ALL_COLLECTOR | {
    V.CAP_INTERFACES, V.CAP_TOPOLOGY, V.CAP_DEPENDENCIES,
}

#: provider token -> capability set the WIRED collector is implemented to emit.
PROVIDER_CAPABILITIES: dict[str, frozenset[str]] = {
    V.PROVIDER_PRTG: frozenset(_MONITORING_PLATFORM),
    V.PROVIDER_ZABBIX: frozenset(_MONITORING_PLATFORM),
    V.PROVIDER_FORTIGATE: frozenset(_FORTIGATE),
    V.PROVIDER_FORTIMANAGER: frozenset(
        _ALL_COLLECTOR | {V.CAP_HEALTH, V.CAP_CONFIGURATION}
    ),
    V.PROVIDER_MERAKI: frozenset(
        _ALL_COLLECTOR | {
            V.CAP_HEALTH, V.CAP_INTERFACES, V.CAP_BANDWIDTH, V.CAP_WIRELESS,
            V.CAP_CLIENTS, V.CAP_TOPOLOGY,
        }
    ),
    V.PROVIDER_SNMP: frozenset(_SNMP),
    V.PROVIDER_PNETLAB: frozenset(_TOPOLOGY_CONTROLLER),
    V.PROVIDER_ICMP: frozenset(_ALL_COLLECTOR | {V.CAP_HEALTH}),
    V.PROVIDER_WINRM: frozenset(_SERVER_AGENT),
    V.PROVIDER_WMI: frozenset(_SERVER_AGENT),
    V.PROVIDER_SSH: frozenset(_SERVER_AGENT),
    V.PROVIDER_AZURE: frozenset(_CLOUD),
    V.PROVIDER_AWS: frozenset(_CLOUD),
    V.PROVIDER_GCP: frozenset(_CLOUD),
    V.PROVIDER_VMWARE: frozenset(
        _ALL_COLLECTOR | {
            V.CAP_HEALTH, V.CAP_CPU, V.CAP_MEMORY, V.CAP_DISK, V.CAP_STORAGE,
            V.CAP_STORAGE_CAPACITY, V.CAP_UPTIME, V.CAP_TOPOLOGY,
        }
    ),
    V.PROVIDER_KUBERNETES: frozenset(
        _ALL_COLLECTOR | {
            V.CAP_HEALTH, V.CAP_CPU, V.CAP_MEMORY, V.CAP_APPLICATIONS,
            V.CAP_DEPENDENCIES,
        }
    ),
}

#: collector ``source_type`` -> canonical provider token. Every wired collector
#: must appear here, otherwise ``provider_from_source_type`` cannot name it and
#: the resource's provider stays None (honest, but less useful).
SOURCE_TYPE_TO_PROVIDER: dict[str, str] = {
    "prtg": V.PROVIDER_PRTG,
    "zabbix": V.PROVIDER_ZABBIX,
    "fortigate": V.PROVIDER_FORTIGATE,
    "fortimanager": V.PROVIDER_FORTIMANAGER,
    "meraki": V.PROVIDER_MERAKI,
    "pnetlab": V.PROVIDER_PNETLAB,
    "snmp": V.PROVIDER_SNMP,
    "switching": V.PROVIDER_SNMP,
    "connectivity": V.PROVIDER_ICMP,
    "windows_winrm": V.PROVIDER_WINRM,
    "wmi": V.PROVIDER_WMI,
    "linux_ssh": V.PROVIDER_SSH,
    "azure": V.PROVIDER_AZURE,
    "aws": V.PROVIDER_AWS,
    "gcp": V.PROVIDER_GCP,
    "vmware": V.PROVIDER_VMWARE,
    "kubernetes": V.PROVIDER_KUBERNETES,
}

PROVIDER_KINDS: dict[str, str] = {
    V.PROVIDER_PRTG: KIND_PLATFORM,
    V.PROVIDER_ZABBIX: KIND_PLATFORM,
    V.PROVIDER_FORTIGATE: KIND_NETWORK,
    V.PROVIDER_FORTIMANAGER: KIND_CONTROLLER,
    V.PROVIDER_MERAKI: KIND_CONTROLLER,
    V.PROVIDER_PNETLAB: KIND_CONTROLLER,
    V.PROVIDER_SNMP: KIND_NETWORK,
    V.PROVIDER_ICMP: KIND_NETWORK,
    V.PROVIDER_WINRM: KIND_AGENT,
    V.PROVIDER_WMI: KIND_AGENT,
    V.PROVIDER_SSH: KIND_AGENT,
    V.PROVIDER_AZURE: KIND_CLOUD,
    V.PROVIDER_AWS: KIND_CLOUD,
    V.PROVIDER_GCP: KIND_CLOUD,
    V.PROVIDER_VMWARE: KIND_CONTROLLER,
    V.PROVIDER_KUBERNETES: KIND_CONTROLLER,
    V.PROVIDER_MANUAL: KIND_MANUAL,
}

#: Resource types a provider is known to produce. Used for the provider catalog
#: and for UI hints; it never overrides the type a collector actually reported.
PROVIDER_RESOURCE_TYPES: dict[str, tuple[str, ...]] = {
    V.PROVIDER_PRTG: (
        "router", "switch", "access_point", "firewall", "server", "network_device",
    ),
    V.PROVIDER_ZABBIX: (
        "router", "switch", "firewall", "server", "endpoint", "network_device",
    ),
    V.PROVIDER_FORTIGATE: ("firewall", "security_gateway"),
    V.PROVIDER_FORTIMANAGER: ("firewall", "security_gateway", "network_device"),
    V.PROVIDER_MERAKI: (
        "access_point", "switch", "security_gateway", "wireless_controller",
    ),
    V.PROVIDER_PNETLAB: (
        "router", "switch", "firewall", "host", "network_device",
    ),
    V.PROVIDER_SNMP: (
        "router", "switch", "firewall", "access_point", "server",
        "storage_system", "network_device",
    ),
    V.PROVIDER_ICMP: ("network_device", "endpoint"),
    V.PROVIDER_WINRM: ("server", "workstation", "virtual_machine"),
    V.PROVIDER_WMI: ("server", "workstation", "virtual_machine"),
    V.PROVIDER_SSH: ("server", "virtual_machine", "container_host"),
    V.PROVIDER_AZURE: (
        "cloud_subscription", "cloud_resource", "cloud_service",
        "cloud_database", "cloud_storage", "cloud_network",
        "cloud_load_balancer",
    ),
    V.PROVIDER_AWS: (
        "cloud_account", "cloud_resource", "cloud_service", "cloud_database",
        "cloud_storage", "cloud_network", "cloud_load_balancer",
    ),
    V.PROVIDER_GCP: (
        "cloud_account", "cloud_resource", "cloud_service", "cloud_database",
        "cloud_storage", "cloud_network", "cloud_load_balancer",
    ),
    V.PROVIDER_VMWARE: (
        "hypervisor", "virtual_machine", "storage_volume", "container_host",
    ),
    V.PROVIDER_KUBERNETES: ("container_host", "service", "application"),
    V.PROVIDER_MANUAL: (),
}


@dataclass(frozen=True)
class ProviderDescriptor:
    """What HSDEEP knows about ONE provider, including whether it is real yet."""

    provider: str
    kind: str
    wired: bool
    source_types: tuple[str, ...] = ()
    capabilities: frozenset[str] = field(default_factory=frozenset)
    resource_types: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        """True when this token is part of the declared provider vocabulary."""
        return self.provider in V.KNOWN_PROVIDERS

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "kind": self.kind,
            "wired": self.wired,
            "declared": self.declared,
            "source_types": list(self.source_types),
            "capabilities": sorted(self.capabilities),
            "capability_count": len(self.capabilities),
            "resource_types": list(self.resource_types),
        }


def wired_source_types() -> dict[str, str]:
    """``source_type`` -> collector class name, read from the live registry.

    Imported lazily so this module never depends on collector import order, and
    so the registry is consulted at CALL time (a collector registered later is
    still reported as wired).
    """
    import app.services.monitoring  # noqa: F401  (registers every collector)

    from app.collectors.base import CollectorRegistry

    return {
        collector_cls.source_type: collector_cls.__name__
        for collector_cls in CollectorRegistry.list_types()
    }


def provider_from_source_type(source_type: str | None) -> str | None:
    """Canonical provider token for a monitoring source type.

    An unmapped source type returns None rather than being coerced into a
    provider whose capabilities would then be misreported.
    """
    if not source_type:
        return None
    return SOURCE_TYPE_TO_PROVIDER.get(source_type.strip().lower())


def implemented_providers() -> frozenset[str]:
    """Providers with a collector registered RIGHT NOW."""
    wired = set(wired_source_types())
    return frozenset(
        provider
        for source_type, provider in SOURCE_TYPE_TO_PROVIDER.items()
        if source_type in wired
    )


def capabilities_for_provider(provider: str | None) -> frozenset[str] | None:
    """Capabilities the WIRED collector for ``provider`` can collect.

    Returns ``None`` when the provider is not wired/evaluated - the capability
    resolver turns that into ``SUPPORT_UNKNOWN`` instead of guessing.
    """
    if not provider or provider not in PROVIDER_CAPABILITIES:
        return None
    if provider not in implemented_providers():
        return None
    return PROVIDER_CAPABILITIES[provider]


#: Evidence classification (Phase 9.4) for each provider a collector is wired
#: for, answering "what kind of interaction produced this capability evidence?".
#:
#: Verified PER PROVIDER, never derived from ``PROVIDER_KINDS``: that grouping
#: puts FortiGate (a REST integration) beside SNMP and ICMP (direct probes), so
#: the kind is not a safe proxy for the collection method. Same reasoning for
#: KIND_AGENT, which covers WinRM/WMI/SSH - all initiated by HSDEEP against the
#: target, therefore ACTIVE.
#:
#: ``V.PROVIDER_MANUAL`` is deliberately ABSENT: operator-asserted facts were not
#: collected by any mechanism, so they have no classification. Absence resolves
#: to None and is never guessed.
PROVIDER_EVIDENCE_CLASSIFICATION: dict[str, str] = {
    # API integrations: HSDEEP asks a provider/controller/cloud API.
    V.PROVIDER_PRTG: V.EVIDENCE_API,
    V.PROVIDER_ZABBIX: V.EVIDENCE_API,
    V.PROVIDER_FORTIGATE: V.EVIDENCE_API,
    V.PROVIDER_FORTIMANAGER: V.EVIDENCE_API,
    V.PROVIDER_MERAKI: V.EVIDENCE_API,
    # PNETLab answers over its own controller API; the lab is the authority for
    # its topology, so a reported link is source-observed evidence.
    V.PROVIDER_PNETLAB: V.EVIDENCE_API,
    V.PROVIDER_VMWARE: V.EVIDENCE_API,
    V.PROVIDER_KUBERNETES: V.EVIDENCE_API,
    V.PROVIDER_AZURE: V.EVIDENCE_API,
    V.PROVIDER_AWS: V.EVIDENCE_API,
    V.PROVIDER_GCP: V.EVIDENCE_API,
    # Direct interrogation of the target itself.
    V.PROVIDER_SNMP: V.EVIDENCE_ACTIVE,
    V.PROVIDER_ICMP: V.EVIDENCE_ACTIVE,
    V.PROVIDER_SSH: V.EVIDENCE_ACTIVE,
    V.PROVIDER_WMI: V.EVIDENCE_ACTIVE,
    V.PROVIDER_WINRM: V.EVIDENCE_ACTIVE,
}


def evidence_classification_for_provider(provider: str | None) -> str | None:
    """Classification of the evidence ``provider``'s collector produces.

    ``None`` means "not stated for this provider" (manual entry, or a provider
    with no declared collection method). It is never coerced into ACTIVE: a
    capability row with no classification is honest, a mislabelled one is not.
    """
    if not provider:
        return None
    return PROVIDER_EVIDENCE_CLASSIFICATION.get(provider)


def _descriptor(provider: str, wired: dict[str, str]) -> ProviderDescriptor:
    source_types = tuple(
        sorted(st for st, token in SOURCE_TYPE_TO_PROVIDER.items() if token == provider)
    )
    return ProviderDescriptor(
        provider=provider,
        kind=PROVIDER_KINDS.get(provider, KIND_NETWORK),
        wired=any(st in wired for st in source_types),
        source_types=source_types,
        capabilities=PROVIDER_CAPABILITIES.get(provider, frozenset()),
        resource_types=PROVIDER_RESOURCE_TYPES.get(provider, ()),
    )


def provider_descriptor(provider: str) -> ProviderDescriptor | None:
    """Describe one provider, or None when the token is unknown AND unwired."""
    if not provider:
        return None
    wired = wired_source_types()
    descriptor = _descriptor(provider, wired)
    if not descriptor.wired and provider not in V.KNOWN_PROVIDERS:
        return None
    return descriptor


def provider_catalog() -> list[dict]:
    """Every declared provider with its real wired state.

    ``wired=false`` is a useful answer: the UI shows the provider as "not
    implemented yet" instead of offering an integration that does nothing.
    """
    wired = wired_source_types()
    return [
        _descriptor(provider, wired).to_dict()
        for provider in sorted(V.KNOWN_PROVIDERS)
    ]
