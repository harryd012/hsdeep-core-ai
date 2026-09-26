"""
Pydantic v2 schemas for the Alert Engine API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/monitoring.py) so the API contract can evolve independently of the
persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.text import contains_markup, normalize_provider_text

AlertSeverity = Literal["critical", "warning", "info"]
AlertStatus = Literal["open", "acknowledged", "resolved"]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None
    device_id: uuid.UUID | None
    sensor_id: uuid.UUID | None
    interface_id: uuid.UUID | None = None
    monitoring_source_id: uuid.UUID | None
    title: str
    description: str | None
    #: Plain-text projection of `description`, derived automatically.
    #:
    #: `description` is the RAW provider value and may contain markup - verified
    #: against the live database: 177 of 1102 alerts store
    #: `<div class="logmessage">...</div>` from PRTG. The Alerts UI renders
    #: `message`; `description` is shown only under "Details -> Raw Event".
    message: str | None = None
    #: True when `description` contained markup that `message` had stripped.
    #: Lets the UI show a "Raw event" affordance only when it is meaningful.
    description_contains_markup: bool = False
    severity: AlertSeverity
    status: AlertStatus
    metric_type: str | None
    category: str | None = None
    vendor: str | None = None
    region: str | None = None
    source: str
    triggered_value: float | None
    threshold_value: float | None
    triggered_at: datetime
    last_occurred_at: datetime
    occurrence_count: int
    acknowledged_at: datetime | None
    acknowledged_by: uuid.UUID | None
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    auto_resolved: bool
    correlation_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    parent_alert_id: uuid.UUID | None = None
    is_root_cause_candidate: bool = False
    is_suppressed: bool = False
    created_at: datetime
    updated_at: datetime

    # Denormalized display fields populated by AlertRepository.list()'s join
    # (set as plain instance attributes, not mapped columns) - lets the
    # frontend render a human-readable alert list without N+1 requests.
    device_name: str | None = None
    site_name: str | None = None
    sensor_name: str | None = None

    @model_validator(mode="after")
    def _derive_plain_text_message(self) -> "AlertOut":
        """Populate `message` from the raw `description`.

        Runs for every construction path (`from_attributes=True` off the ORM
        object, or an explicit dict), so no caller can accidentally emit an
        alert whose only message is raw provider markup. An explicitly supplied
        `message` is respected - the derivation only fills a blank.
        """
        if self.message is None:
            self.message = normalize_provider_text(self.description).text
        self.description_contains_markup = contains_markup(self.description)
        return self


class AlertHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alert_id: uuid.UUID
    event_type: str
    message: str
    details: dict
    occurred_at: datetime


class AlertCategoryCounts(BaseModel):
    availability: int = 0
    performance: int = 0
    capacity: int = 0
    connectivity: int = 0
    configuration: int = 0
    security: int = 0
    other: int = 0


class AlertStatistics(BaseModel):
    """Alert Statistics API - broader than AlertSummary (severity/status
    only): adds category breakdown, suppression count, and correlation
    coverage (what fraction of active alerts are grouped into an incident)."""

    total: int
    by_severity: AlertSeverityCounts
    by_status: AlertStatusCounts
    by_category: AlertCategoryCounts
    suppressed_count: int
    correlated_count: int
    incident_count: int


class AlertAcknowledge(BaseModel):
    # TODO(auth): should come from the authenticated principal once the auth
    # module exists, not the request body. Same interim pattern as the rest
    # of the API - see NOTE(auth) in api/routes/monitoring.py.
    acknowledged_by: uuid.UUID
    note: str | None = Field(default=None, max_length=1000)


class AlertResolve(BaseModel):
    resolved_by: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


class AlertSeverityCounts(BaseModel):
    critical: int = 0
    warning: int = 0
    info: int = 0


class AlertStatusCounts(BaseModel):
    open: int = 0
    acknowledged: int = 0
    resolved: int = 0


class AlertSummary(BaseModel):
    total: int
    by_severity: AlertSeverityCounts
    by_status: AlertStatusCounts