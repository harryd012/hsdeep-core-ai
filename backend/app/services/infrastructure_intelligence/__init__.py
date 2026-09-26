"""
Universal Infrastructure Intelligence (provider-neutral foundation).

Layering (each module imports only from the ones above it):

    vocabulary    canonical provider-neutral vocabulary (types, capabilities,
                  health/lifecycle states, reasons, relationships, providers)
    capabilities  capability profiles + three-way support resolution
    telemetry     canonical telemetry contract + metric-name catalog
    resources     universal resource projection
    network       interfaces / WAN / SD-WAN / VPN / routing / BGP projections
    providers     provider capability registry (wired vs. declared)
    pipeline      NormalizedBundle -> canonical telemetry + capability facts
    health        dependency- and maintenance-aware resource/site/tenant health
    hsignce       graph-based correlation context (no GNN is claimed)
    service       tenant/site/resource read models for the API

Nothing here is vendor-specific: a provider plugs in by registering a
collector (existing Collector Framework) and adding capability rows, never by
editing these modules.
"""
from app.services.infrastructure_intelligence.capabilities import (
    CapabilityAssertion,
    CapabilityReport,
    resolve_capabilities,
    supported,
    type_exposes,
    unknown_capability,
    unsupported,
)
from app.services.infrastructure_intelligence.health import (
    HealthRollup,
    HealthService,
    ResourceHealth,
)
from app.services.infrastructure_intelligence.hsignce import (
    CorrelationGroup,
    HsignceContextBuilder,
    connected_components,
    rank_root_causes,
)
from app.services.infrastructure_intelligence.network import (
    BgpNeighborState,
    RouteEntry,
    UniversalInterface,
    VpnTunnel,
    WanMember,
    evaluate_sdwan_members,
)
from app.services.infrastructure_intelligence.pipeline import (
    CanonicalTelemetrySaver,
    NormalizationResult,
    normalize_bundle,
)
from app.services.infrastructure_intelligence.providers import (
    ProviderDescriptor,
    capabilities_for_provider,
    provider_catalog,
    provider_from_source_type,
    wired_source_types,
)
from app.services.infrastructure_intelligence.resources import (
    UniversalResource,
    resource_type_catalog,
)
from app.services.infrastructure_intelligence.service import (
    UniversalInfrastructureService,
)
from app.services.infrastructure_intelligence.telemetry import (
    CanonicalTelemetry,
    not_available,
    quality_for,
    summarize_series,
)
from app.services.infrastructure_intelligence.vocabulary import (
    ALL_CAPABILITIES,
    HEALTH_STATES,
    RELATIONSHIP_TYPES,
    RESOURCE_FAMILIES,
    SUPPORT_STATES,
    UNIVERSAL_RESOURCE_TYPES,
    resource_family,
    universal_resource_type,
)

__all__ = [
    "ALL_CAPABILITIES",
    "BgpNeighborState",
    "CanonicalTelemetry",
    "CanonicalTelemetrySaver",
    "CapabilityAssertion",
    "CapabilityReport",
    "CorrelationGroup",
    "HEALTH_STATES",
    "HealthRollup",
    "HealthService",
    "HsignceContextBuilder",
    "NormalizationResult",
    "ProviderDescriptor",
    "RELATIONSHIP_TYPES",
    "RESOURCE_FAMILIES",
    "ResourceHealth",
    "RouteEntry",
    "SUPPORT_STATES",
    "UNIVERSAL_RESOURCE_TYPES",
    "UniversalInfrastructureService",
    "UniversalInterface",
    "UniversalResource",
    "VpnTunnel",
    "WanMember",
    "capabilities_for_provider",
    "connected_components",
    "evaluate_sdwan_members",
    "normalize_bundle",
    "not_available",
    "provider_catalog",
    "provider_from_source_type",
    "quality_for",
    "rank_root_causes",
    "resolve_capabilities",
    "resource_family",
    "resource_type_catalog",
    "summarize_series",
    "supported",
    "type_exposes",
    "unknown_capability",
    "universal_resource_type",
    "unsupported",
    "wired_source_types",
]
