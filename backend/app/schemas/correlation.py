"""
Correlation result schemas (Phase 5 — topology-aware event correlation).

The correlation engine's contract: ONE root cause candidate with explicit
evidence, impact and confidence — never a storm of unrelated incidents.
All values are derived from real stored events and topology edges; the
engine never fabricates evidence. When evidence is thin it reports
`insufficient` and says so instead of guessing.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CorrelatedEventRef(BaseModel):
    """A normalized event participating in a correlation group."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_category: str
    event_type: str
    severity: str
    title: str
    device_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    occurrence_count: int = 1
    is_suppressed: bool = False
    event_timestamp: datetime | None = None


class TopologyEvidenceEdge(BaseModel):
    """A topology edge that supports the correlation (real stored edge)."""

    source_device_id: str
    target_device_id: str
    relationship_type: str


class RootCauseCandidate(BaseModel):
    """The single most probable root-cause event, with why it was chosen."""

    event_id: uuid.UUID
    title: str
    event_category: str
    event_type: str
    severity: str
    device_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    selection_reason: str


class CorrelationResult(BaseModel):
    tenant_id: uuid.UUID
    correlation_id: uuid.UUID
    root_cause: RootCauseCandidate
    correlated_events: list[CorrelatedEventRef] = []
    suppressed_symptom_ids: list[uuid.UUID] = []
    topology_path: list[TopologyEvidenceEdge] = []
    affected_device_ids: list[uuid.UUID] = []
    confidence_score: float = 0.0
    evidence_sufficiency: str = "insufficient"  # sufficient | partial | insufficient
    noc_event_id: uuid.UUID | None = None
    created: bool = True  # False when correlation already existed (idempotent re-run)


class CorrelationRunRequest(BaseModel):
    time_window_minutes: int = Field(default=15, ge=1, le=120)
    max_related_events: int = Field(default=200, ge=1, le=1000)


class CorrelationGroupSummary(BaseModel):
    """Read-side summary of one correlated group (results listing)."""

    correlation_id: uuid.UUID
    root_cause_title: str
    root_cause_severity: str
    root_cause_category: str
    root_cause_device_id: uuid.UUID | None = None
    correlated_count: int
    suppressed_count: int
    noc_event_id: uuid.UUID | None = None
    latest_event_at: datetime | None = None