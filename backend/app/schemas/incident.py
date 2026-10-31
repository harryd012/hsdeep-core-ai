"""
Pydantic v2 schemas for the Incident API (Correlation Engine sprint).

Kept separate from the ORM model, same Clean Architecture convention as
schemas/alert.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.alert import AlertOut

IncidentSeverity = Literal["critical", "warning", "info"]
IncidentStatus = Literal[
    "open", "acknowledged", "investigating", "monitoring",
    "recovered", "resolved", "closed", "archived"
]


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None
    device_id: uuid.UUID | None
    title: str
    description: str | None
    severity: IncidentSeverity
    status: str  # open|acknowledged|investigating|monitoring|recovered|resolved|closed|archived
    root_cause_alert_id: uuid.UUID | None
    alert_count: int
    opened_at: datetime
    last_alert_at: datetime
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    affected_assets: list[dict] = []

    # Correlation / priority engine fields (optional — set after engine runs)
    correlation_type: str | None = None
    root_cause_summary: str | None = None
    supporting_evidence: dict = {}
    automation_available: bool = False
    total_occurrences: int = 0
    assigned_team: str | None = None
    assigned_to: uuid.UUID | None = None

    # Priority engine scores
    priority: str | None = None
    operational_risk_score: float | None = None
    business_risk_score: float | None = None
    technical_risk_score: float | None = None
    ai_risk_score: float | None = None
    incident_confidence_score: float | None = None
    business_impact_score: float | None = None
    escalation_level: str | None = None
    recommended_team: str | None = None
    recommended_response_time_minutes: int | None = None
    recommended_next_action: str | None = None
    recommended_sop: str | None = None

    # Denormalized display fields set by the service layer
    device_name: str | None = None
    site_name: str | None = None


class IncidentDetailOut(IncidentOut):
    """Incident Details API - the incident plus every member Alert."""

    alerts: list[AlertOut] = []


class RecoveryCheckItemOut(BaseModel):
    kind: Literal["alert", "sensor", "device"]
    id: uuid.UUID
    name: str
    status: str
    observed_at: datetime | None
    result: Literal["pass", "fail", "inconclusive"]
    detail: str


class RecoveryCheckOut(BaseModel):
    """Evidence for or against recovery, from telemetry observed after `since`."""

    incident_id: uuid.UUID
    since: datetime
    evaluated_at: datetime
    verdict: Literal["recovered", "not_recovered", "inconclusive"]
    checks: list[RecoveryCheckItemOut]
