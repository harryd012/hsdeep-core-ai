"""
Pydantic v2 schemas for the AI Priority Engine API.

Kept separate from the ORM model, same Clean Architecture convention as
schemas/incident.py/schemas/alert.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.incident import IncidentOut

Priority = Literal["P1", "P2", "P3", "P4"]


class PriorityOut(BaseModel):
    """GET /api/priorities/{incident_id} - the priority-specific slice of
    an Incident, plus its full reasoning breakdown."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: uuid.UUID
    priority: Priority | None
    operational_risk_score: float | None
    business_risk_score: float | None
    technical_risk_score: float | None
    ai_risk_score: float | None
    incident_confidence_score: float | None
    business_impact_score: float | None
    escalation_level: str | None
    recommended_team: str | None
    recommended_response_time_minutes: int | None
    recommended_next_action: str | None
    recommended_sop: str | None
    priority_reasoning: dict
    priority_calculated_at: datetime | None
    manual_priority_override: Priority | None
    priority_overridden_by: uuid.UUID | None
    priority_overridden_at: datetime | None


class PriorityDistribution(BaseModel):
    P1: int = 0
    P2: int = 0
    P3: int = 0
    P4: int = 0
    unscored: int = 0


class RiskHeatmapCell(BaseModel):
    """One incident's position on the risk heatmap - business risk vs.
    operational risk, sized/colored by ai_risk_score client-side."""

    incident_id: uuid.UUID
    title: str
    priority: Priority | None
    business_risk_score: float | None
    operational_risk_score: float | None
    technical_risk_score: float | None
    ai_risk_score: float | None


class RiskSummary(BaseModel):
    """GET /api/risk - overall risk posture across all open incidents."""

    generated_at: datetime
    priority_distribution: PriorityDistribution
    avg_ai_risk_score: float
    highest_risk_incidents: list[RiskHeatmapCell]
    heatmap: list[RiskHeatmapCell]


class BusinessImpactSummary(BaseModel):
    """GET /api/business-impact - aggregated business impact across all
    open incidents, plus the per-service breakdown for the Critical
    Services widget."""

    generated_at: datetime
    total_estimated_affected_users: int
    total_affected_devices: int
    critical_incident_count: int
    top_incidents_by_impact: list[RiskHeatmapCell]


class ManualOverrideRequest(BaseModel):
    priority: Priority | None = None  # None clears the override
    overridden_by: uuid.UUID | None = None


class PrioritizedIncidentOut(IncidentOut):
    """GET /api/incidents/prioritized - IncidentOut plus every Priority
    Engine field, so the dashboard's Highest Priority Incidents widget
    needs exactly one call."""

    priority: Priority | None = None
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
