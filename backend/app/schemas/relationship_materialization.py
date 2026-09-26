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