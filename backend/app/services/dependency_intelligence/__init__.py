"""
HSDEEP Dependency Intelligence Engine V1.

Deterministic evidence-driven dependency reasoning. No LLM, no graph database, no
persisted taxonomy, and no parallel RCA/topology system.

    taxonomy   - protocol/service definitions and their dependency edges
    graph      - in-memory dependency graph, bounded + cycle-protected traversal
    models     - Evidence / EvidenceState / Confidence / Finding / DependencyAnalysis
    analysis   - evidence normalisation, the six deterministic rules, analyzer

Integration boundary: the analyzer returns serialisable findings
(``DependencyAnalysis.to_dict()``) so existing consumers can take structured
evidence. ``app/services/rca_service.py`` already declares a ``RootCauseAnalyser``
Protocol which this module is shaped to satisfy; that subsystem was NOT modified.
"""
from app.services.dependency_intelligence.analysis import (
    DependencyIntelligenceAnalyzer,
    analyze,
    normalize_capability,
)
from app.services.dependency_intelligence.graph import DependencyGraph, Edge
from app.services.dependency_intelligence.models import (
    Confidence,
    DependencyAnalysis,
    Evidence,
    EvidenceState,
    Finding,
    coerce_state,
)
from app.services.dependency_intelligence.taxonomy import (
    CATEGORIES,
    PROTOCOLS,
    Protocol,
    all_protocols,
    get_protocol,
    protocols_in_category,
)

__all__ = [
    "DependencyIntelligenceAnalyzer",
    "analyze",
    "normalize_capability",
    "DependencyGraph",
    "Edge",
    "Confidence",
    "DependencyAnalysis",
    "Evidence",
    "EvidenceState",
    "Finding",
    "coerce_state",
    "CATEGORIES",
    "PROTOCOLS",
    "Protocol",
    "all_protocols",
    "get_protocol",
    "protocols_in_category",
]
