"""
Pydantic v2 schemas for the Normalized Event API.

NormalizedEvents are the vendor-neutral currency of the telemetry plane.
They are produced by integrations (PNETLab, FortiGate, Security Onion, etc)
and consumed by the CorrelationEngine to produce NocEvents.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NormalizedEventSeverity = Literal["critical", "warning", "info"]
NormalizedEventCategory = Literal[
    "health", "network", "security", "performance", "routing", "vpn",
    "sdwan", "configuration", "cloud", "system", "application", "topology",
]
NormalizedEventTelemetryType = Literal[
    "METRIC", "LOG", "EVENT", "ALERT", "SECURITY_EVENT", "HEALTH", "TOPOLOGY_CHANGE",
]


class NormalizedEventBase(BaseModel):
    title: str = Field(..., max_length=500)
    description: str | None = None
    severity: NormalizedEventSeverity
    event_category: NormalizedEventCategory
    event_type: str = Field(..., max_length=100)
    telemetry_type: NormalizedEventTelemetryType
    event_signature: str | None = Field(None, max_length=500)
    event_timestamp: datetime
    site_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    integration_id: uuid.UUID | None = None
    source_event_id: str | None = Field(None, max_length=255)
    attributes: dict = Field(default_factory=dict)
    confidence_score: float | None = Field(None, ge=0.0, le=100.0)
    raw_reference: str | None = Field(None, max_length=500)


class NormalizedEventCreate(NormalizedEventBase):
    tenant_id: uuid.UUID | None = None  # derived from auth if None


class NormalizedEventUpdate(BaseModel):
    title: str | None = Field(None, max_length=500)
    description: str | None = None
    severity: NormalizedEventSeverity | None = None
    event_category: NormalizedEventCategory | None = None
    event_type: str | None = Field(None, max_length=100)
    is_suppressed: bool | None = None
    attributes: dict | None = None


class NormalizedEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    integration_id: uuid.UUID | None
    site_id: uuid.UUID | None
    device_id: uuid.UUID | None
    source_event_id: str | None
    event_signature: str | None
    event_category: str
    event_type: str
    severity: str
    title: str
    description: str | None
    attributes: dict
    event_timestamp: datetime
    correlation_id: uuid.UUID | None
    noc_event_id: uuid.UUID | None
    is_suppressed: bool
    telemetry_type: str
    confidence_score: float | None
    raw_reference: str | None
    occurrence_count: int
    last_occurred_at: datetime
    created_at: datetime
    updated_at: datetime

    # Denormalized display fields
    site_name: str | None = None
    device_name: str | None = None
    integration_name: str | None = None


class NormalizedEventListItem(BaseModel):
    """Lightweight projection for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    severity: str
    event_category: str
    event_type: str
    telemetry_type: str
    event_timestamp: datetime
    occurrence_count: int
    is_suppressed: bool
    site_name: str | None = None
    device_name: str | None = None
    integration_name: str | None = None


class NormalizedEventPagination(BaseModel):
    items: list[NormalizedEventListItem]
    total: int
    limit: int
    offset: int


class NormalizedEventSummary(BaseModel):
    total: int
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_telemetry_type: dict[str, int] = {}
    suppressed_count: int = 0
