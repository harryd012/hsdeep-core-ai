"""Universal infrastructure recognition (provider-agnostic discovery core).

Layering:

    taxonomy   -> resource categories + canonical/extended resource types
    providers  -> ProviderAdapter contract + capability-driven registry
    identity   -> deterministic identity keys + evidence confidence
    engine     -> evidence -> normalized resource (+ provenance)

Adding a vendor/provider means registering an adapter with fingerprints and
capabilities; the core above is never edited.
"""
from app.services.recognition import adapters as _adapters  # noqa: F401
from app.services.recognition.engine import (
    Recognition,
    RecognizedResource,
    enrich_identity,
    recognize,
)
from app.services.recognition.identity import (
    IdentityEvidence,
    has_strong_identity,
    identity_confidence,
    identity_keys,
    identity_source,
    same_resource,
    strong_identity_keys,
)
from app.services.recognition.providers import (
    CAP_API,
    CAP_ICMP,
    CAP_SNMP,
    CAP_SSH,
    CAP_TELEMETRY,
    CAP_TOPOLOGY,
    ProviderAdapter,
    ProviderRegistry,
    RecognitionFingerprint,
    register,
)
from app.services.recognition.taxonomy import (
    ALL_RESOURCE_TYPES,
    RESOURCE_CATEGORIES,
    category_for,
    normalize_resource_type,
)

__all__ = [
    "ALL_RESOURCE_TYPES",
    "CAP_API",
    "CAP_ICMP",
    "CAP_SNMP",
    "CAP_SSH",
    "CAP_TELEMETRY",
    "CAP_TOPOLOGY",
    "IdentityEvidence",
    "ProviderAdapter",
    "ProviderRegistry",
    "RESOURCE_CATEGORIES",
    "Recognition",
    "RecognitionFingerprint",
    "RecognizedResource",
    "category_for",
    "enrich_identity",
    "has_strong_identity",
    "identity_confidence",
    "identity_keys",
    "identity_source",
    "normalize_resource_type",
    "recognize",
    "register",
    "same_resource",
    "strong_identity_keys",
]
