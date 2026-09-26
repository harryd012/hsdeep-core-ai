"""
Pydantic v2 schemas for the NOC Event API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py) so the API contract can evolve independently of the
persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NocEventSeverity = Literal["critical", "warning", "info"]
NocEventStatus = Literal["open", "acknowledged", "resolved"]
NocEventCategory = Literal[
    "availability", "performance", "capacity", "connectivity",
    "configuration", "security", "other",
]


class NocEventBase(BaseModel):
    title: str = Field(..., max_length=255)
    description: str | None = None
    severity: NocEventSeverity
    category: NocEventCategory = "other"
    source: str = Field(..., max_length=100)
    event_signature: str | None = Field(None, max_length=255)
    triggered_at: datetime
    site_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    source_agent_id: uuid.UUID | None = None


class NocEventCreate(NocEventBase):
    tenant_id: uuid.UUID | None = None  # derived from auth if None
    assigned_analyst_id: uuid.UUID | None = None
    confidence_score: float | None = Field(None, ge=0.0, le=100.0)


class NocEventUpdate(BaseModel):
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    severity: NocEventSeverity | None = None
    category: NocEventCategory | None = None
    assigned_analyst_id: uuid.UUID | None = None
    status: NocEventStatus | None = None


class NocEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None
    device_id: uuid.UUID | None
    event_signature: str | None
    source: str
    category: str
    title: str
    description: str | None
    severity: NocEventSeverity
    status: NocEventStatus
    triggered_at: datetime
    last_occurred_at: datetime
    occurrence_count: int
    acknowledged_at: datetime | None
    acknowledged_by: uuid.UUID | None
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    assigned_analyst_id: uuid.UUID | None
    source_agent_id: uuid.UUID | None
    confidence_score: float | None
    created_at: datetime
    updated_at: datetime

    # Denormalized display fields (set by repository joins)
    site_name: str | None = None
    device_name: str | None = None
    assigned_analyst_name: str | None = None
    source_agent_name: str | None = None


class NocEventListItem(BaseModel):
    """Lightweight projection for list views — avoids loading full description."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    severity: NocEventSeverity
    status: NocEventStatus
    category: str
    source: str
    triggered_at: datetime
    occurrence_count: int
    site_name: str | None = None
    device_name: str | None = None
    assigned_analyst_name: str | None = None


class NocEventAcknowledge(BaseModel):
    acknowledged_by: uuid.UUID
    note: str | None = Field(default=None, max_length=1000)


class NocEventResolve(BaseModel):
    resolved_by: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


class NocEventAssign(BaseModel):
    analyst_id: uuid.UUID


class NocEventSeverityCounts(BaseModel):
    critical: int = 0
    warning: int = 0
    info: int = 0


class NocEventStatusCounts(BaseModel):
    open: int = 0
    acknowledged: int = 0
    resolved: int = 0


class NocEventCategoryCounts(BaseModel):
    availability: int = 0
    performance: int = 0
    capacity: int = 0
    connectivity: int = 0
    configuration: int = 0
    security: int = 0
    other: int = 0


class NocEventSummary(BaseModel):
    total: int
    by_severity: NocEventSeverityCounts
    by_status: NocEventStatusCounts
    by_category: NocEventCategoryCounts
    unassigned_open: int = 0
    avg_resolution_minutes: float | None = None


class NocEventStatistics(NocEventSummary):
    """Extended statistics for the NOC dashboard."""

    mttr_minutes: float | None = None  # mean time to resolve
    mttr_by_severity: dict[str, float | None] = {}
    open_over_30min: int = 0
    top_sources: list[dict[str, int | str]] = []


class NocEventPagination(BaseModel):
    items: list[NocEventListItem]
    total: int
    limit: int
    offset: int
