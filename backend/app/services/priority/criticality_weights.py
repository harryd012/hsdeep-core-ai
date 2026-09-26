"""
Shared criticality/severity weight tables.

Extracted into their own tiny, dependency-free module (same rationale as
`app/services/alerting/severity_mapper.py`) so BusinessImpactEngine,
RiskEngine, and PriorityEngine all read from ONE weight table instead of
three drifting copies.
"""
from __future__ import annotations

# critical/high/medium/low -> 0-100 weight. "medium" is the neutral
# default for an unclassified Site/Device (nullable criticality column) -
# deliberately not "low", so an unclassified asset doesn't silently get
# treated as unimportant.
CRITICALITY_WEIGHTS: dict[str, float] = {
    "critical": 100.0,
    "high": 75.0,
    "medium": 50.0,
    "low": 20.0,
}
DEFAULT_CRITICALITY_WEIGHT = CRITICALITY_WEIGHTS["medium"]

# critical/warning/info -> 0-100 weight, same rank order as
# severity_mapper.severity_rank() but expressed as a weight rather than an
# ordinal, since RiskEngine needs to combine it arithmetically with other
# 0-100 weights (ordinal rank alone can't be averaged meaningfully).
SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 100.0,
    "warning": 60.0,
    "info": 20.0,
}


def criticality_weight(criticality: str | None) -> float:
    if criticality is None:
        return DEFAULT_CRITICALITY_WEIGHT
    return CRITICALITY_WEIGHTS.get(criticality, DEFAULT_CRITICALITY_WEIGHT)


def severity_weight(severity: str) -> float:
    return SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHTS["info"])
