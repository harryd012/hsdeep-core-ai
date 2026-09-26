"""
Response schema for Phase 9.6 relationship-evidence materialisation.

Mirrors ``RelationshipEvidenceService.materialize()``: a per-source breakdown plus
honest limitations, so a caller can see exactly what was converted and why a
source produced nothing.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RelationshipSourceReportOut(BaseModel):
    """What one recorded evidence source contributed."""

    source: str
    candidates: int = 0
    planned: int = 0
    written: int = 0
    refreshed: int = 0
    unresolved: int = 0
    skipped: int = 0
    note: str | None = None


class RelationshipReconciliationOut(BaseModel):
    """Phase 9.10 canonical view of the tenant's recorded relationships.

    Read-only statistics from ``RelationshipEvidenceService.reconcile_canonical``:
    nothing here is inferred, and an unresolved or conflicting piece of evidence
    is reported rather than smoothed over.
    """

    tenant_id: str
    generated_at: str
    dry_run: bool
    evidence_records_seen: int = 0
    resolved: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    canonical_relationships: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    relationships_unchanged: int = 0
    conflicts: int = 0
    stale: int = 0
    skipped: int = 0
    errors: int = 0
    conflict_samples: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RelationshipMaterializationOut(BaseModel):
    tenant_id: str
    generated_at: str
    dry_run: bool
    devices_total: int = 0
    candidates_total: int = 0
    planned_total: int = 0
    written_total: int = 0
    refreshed_total: int = 0
    unresolved_total: int = 0
    skipped_total: int = 0
    sources: list[RelationshipSourceReportOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    #: Phase 9.10 (additive): the canonical reconciliation of the tenant's
    #: recorded relationships, including the evidence this run just materialised.
    canonical: RelationshipReconciliationOut | None = None