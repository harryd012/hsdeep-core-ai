"""
Pydantic v2 schemas for the SOC Analyst API.

SOC analysts are NOT a separate ORM model — they are Agent rows with
department="soc" and role="soc_analyst" (same Agent table as every other
role in the virtual company). These schemas wrap Agent read data with
NOC-specific assignment counts computed at query time by the SOC service.

This keeps the SOC roster in lock-step with the existing Agent hierarchy
(CTO → SOC Manager → SOC Analyst) without duplicating identity data.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SocAnalystOut(BaseModel):
    """One SOC analyst — wraps AgentRead with live NOC assignment counts."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    # -- Identity (from Agent) --
    id: uuid.UUID
    agent_id: str
    name: str
    role: str
    department: str
    level: str
    manager_id: uuid.UUID | None
    status: str
    availability: str
    is_active: bool
    current_workload: int
    max_workload: int
    skills: list[str] = []
    supported_task_types: list[str] = []
    success_rate: float = 0.0
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_resolution_minutes: float | None = None
    created_at: datetime
    updated_at: datetime

    # -- SOC-specific computed fields --
    open_noc_events: int = 0
    acknowledged_noc_events: int = 0
    total_assigned_events: int = 0
    resolved_today: int = 0
    last_assigned_at: datetime | None = None


class SocAnalystDetailOut(SocAnalystOut):
    """Analyst detail — includes the full list of assigned NOC events."""

    assigned_events: list[dict] = []


class SocAnalystSummary(BaseModel):
    """Aggregate counts for the SOC roster header."""

    total_analysts: int = 0
    available_analysts: int = 0
    busy_analysts: int = 0
    offline_analysts: int = 0
    total_open_events: int = 0
    unassigned_open_events: int = 0
    avg_success_rate: float = 0.0


class SocRosterQuery(BaseModel):
    """Query parameters for the SOC roster list endpoint."""

    availability: str | None = None
    level: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class SocAssignAnalyst(BaseModel):
    """Assign a NOC event to a SOC analyst."""

    noc_event_id: uuid.UUID
    analyst_id: uuid.UUID
