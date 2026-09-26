"""Deterministic dependency-intelligence models.

Pure, serialisable data - no database, no LLM, no graph database - so existing
consumers (RCA, Resolution Recommendation, SOP) can take structured findings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EvidenceState(str, Enum):
    """The only four states the engine reasons about."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_TESTED = "NOT_TESTED"

    @property
    def confirmed(self) -> bool:
        return self is EvidenceState.PASS


class Confidence(str, Enum):
    """Transparent, evidence-derived confidence - deliberately NOT an AI score.

    HIGH   - direct evidence isolates the dependency.
    MEDIUM - multiple consistent signals, dependency not directly verifiable.
    LOW    - insufficient evidence.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


#: Accepted inbound spellings mapped onto the four canonical states, so the
#: engine tolerates existing monitoring vocabulary without inventing states.
_STATE_ALIASES: dict[str, EvidenceState] = {
    "pass": EvidenceState.PASS,
    "ok": EvidenceState.PASS,
    "up": EvidenceState.PASS,
    "healthy": EvidenceState.PASS,
    "reachable": EvidenceState.PASS,
    "fail": EvidenceState.FAIL,
    "failed": EvidenceState.FAIL,
    "down": EvidenceState.FAIL,
    "unreachable": EvidenceState.FAIL,
    "error": EvidenceState.FAIL,
    "unknown": EvidenceState.UNKNOWN,
    "not_tested": EvidenceState.NOT_TESTED,
    "not-tested": EvidenceState.NOT_TESTED,
}


def coerce_state(value: Any) -> EvidenceState:
    """Normalise inbound evidence into a canonical state.

    Anything unrecognised becomes UNKNOWN - never PASS. Fail-closed: the engine
    must not treat missing or ambiguous data as healthy.
    """
    if isinstance(value, EvidenceState):
        return value
    if isinstance(value, bool):
        return EvidenceState.PASS if value else EvidenceState.FAIL
    if value is None:
        return EvidenceState.UNKNOWN
    return _STATE_ALIASES.get(str(value).strip().lower(), EvidenceState.UNKNOWN)


@dataclass(frozen=True)
class Evidence:
    """One observed fact about one capability of one resource."""

    capability: str
    state: EvidenceState
    source: str = "unknown"
    resource_id: str | None = None
    observed_at: datetime | None = None
    details: str | None = None

    @classmethod
    def of(
        cls,
        capability: str,
        state: Any,
        *,
        source: str = "unknown",
        resource_id: str | None = None,
        details: str | None = None,
        observed_at: datetime | None = None,
    ) -> "Evidence":
        return cls(
            capability=str(capability).strip().lower(),
            state=coerce_state(state),
            source=source,
            resource_id=resource_id,
            details=details,
            observed_at=observed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "state": self.state.value,
            "source": self.source,
            "resource_id": self.resource_id,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "details": self.details,
        }


@dataclass
class Finding:
    """Structured, serialisable analysis result for ONE observed capability."""

    capability: str
    state: EvidenceState
    healthy_dependencies: list[str] = field(default_factory=list)
    failed_dependencies: list[str] = field(default_factory=list)
    unknown_dependencies: list[str] = field(default_factory=list)
    candidate_causes: list[str] = field(default_factory=list)
    suggested_checks: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    affected_capabilities: list[str] = field(default_factory=list)
    support: list[dict[str, Any]] = field(default_factory=list)
    #: Conclusions explicitly NOT drawn - makes "no false device-down" testable.
    excluded_conclusions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "state": self.state.value,
            "healthy_dependencies": list(self.healthy_dependencies),
            "failed_dependencies": list(self.failed_dependencies),
            "unknown_dependencies": list(self.unknown_dependencies),
            "candidate_causes": list(self.candidate_causes),
            "suggested_checks": list(self.suggested_checks),
            "confidence": self.confidence.value,
            "affected_capabilities": list(self.affected_capabilities),
            "support": list(self.support),
            "excluded_conclusions": list(self.excluded_conclusions),
            "notes": list(self.notes),
        }


@dataclass
class DependencyAnalysis:
    """The analyzer's deterministic result for one resource."""

    resource_id: str | None
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def has_failures(self) -> bool:
        return any(f.state is EvidenceState.FAIL for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "analyzed_at": self.analyzed_at.isoformat(),
            "has_failures": self.has_failures,
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
        }
