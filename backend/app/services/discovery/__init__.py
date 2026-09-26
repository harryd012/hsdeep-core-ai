"""Universal discovery integration layer (recognition -> existing HSDEEP stack).

    collector evidence -> recognition core -> identity -> Device
        -> capabilities -> relationships

Nothing here re-implements recognition, the Device model, the capability store
or the topology model — it wires them together.
"""
from app.services.discovery.evidence import (
    DiscoveryEvidence,
    NeighborEvidence,
    TRANSPORT_AGENT,
    TRANSPORT_API,
    TRANSPORT_HTTP,
    TRANSPORT_ICMP,
    TRANSPORT_SNMP,
    TRANSPORT_SSH,
    TRANSPORT_TCP,
)
from app.services.discovery.profiles import (
    DEFAULT_PROFILE,
    PROFILES,
    PROFILE_NAMES,
    DiscoveryProfile,
    UnknownDiscoveryProfile,
    apply_profile,
    profile_catalog,
    resolve_profile,
)
from app.services.discovery.service import (
    DiscoveryResult,
    DiscoveryService,
    ResourceOutcome,
)

__all__ = [
    "DEFAULT_PROFILE",
    "DiscoveryEvidence",
    "DiscoveryProfile",
    "DiscoveryResult",
    "DiscoveryService",
    "NeighborEvidence",
    "PROFILES",
    "PROFILE_NAMES",
    "ResourceOutcome",
    "TRANSPORT_AGENT",
    "TRANSPORT_API",
    "TRANSPORT_HTTP",
    "TRANSPORT_ICMP",
    "TRANSPORT_SNMP",
    "TRANSPORT_SSH",
    "TRANSPORT_TCP",
    "UnknownDiscoveryProfile",
    "apply_profile",
    "profile_catalog",
    "resolve_profile",
]
