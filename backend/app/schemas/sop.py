"""
Pydantic v2 schemas for the SOP Repository/Knowledge Base API.

Kept separate from the ORM model, same Clean Architecture convention as
schemas/incident.py/schemas/priority.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SOPCategory = Literal[
    "availability", "performance", "capacity", "configuration", "security", "connectivity", "other"
]
SOPVersionStatus = Literal["draft", "approved", "deprecated"]


class SOPVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sop_id: uuid.UUID
    version_number: int
    steps: list[dict]
    possible_causes: list[str]
    change_summary: str | None
    status: SOPVersionStatus
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime


class SOPOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    sop_number: str
    name: str
    description: str | None
    category: str
    tags: list[str]
    vendor: str | None
    platform: str | None
    device_type: str | None
    site_id: uuid.UUID | None
    business_service_id: uuid.UUID | None
    region: str | None
    min_priority: str | None
    required_skills: list[str]
    requires_approval: bool
    estimated_resolution_minutes: int | None
    rollback_procedure: str | None
    is_active: bool
    current_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class SOPDetailOut(SOPOut):
    """SOP details view - the catalog entry plus every version (audit
    trail deliverable)."""

    versions: list[SOPVersionOut] = []


class SOPCreate(BaseModel):
    sop_number: str
    name: str
    description: str | None = None
    category: SOPCategory
    tags: list[str] = []
    vendor: str | None = None
    platform: str | None = None
    device_type: str | None = None
    site_id: uuid.UUID | None = None
    business_service_id: uuid.UUID | None = None
    region: str | None = None
    min_priority: Literal["P1", "P2", "P3", "P4"] | None = None
    required_skills: list[str] = []
    requires_approval: bool = True
    estimated_resolution_minutes: int | None = None
    rollback_procedure: str | None = None


class SOPVersionCreate(BaseModel):
    steps: list[dict]
    possible_causes: list[str] = []
    change_summary: str | None = None


class SOPVersionApproveRequest(BaseModel):
    approved_by: uuid.UUID | None = None  # ignored: attributed to the authenticated user


class SOPRecommendationOut(BaseModel):
    """One scored SOP recommendation for an Incident - every mandatory
    field the sprint's AI Recommendation Engine requires: confidence
    score, matching criteria, business impact, estimated resolution time,
    required skills, required approvals, rollback procedure."""

    model_config = ConfigDict(from_attributes=True)

    sop_id: uuid.UUID
    sop_number: str
    name: str
    category: str
    confidence_score: float
    matched_criteria: dict
    reasoning: dict
    business_impact_score: float
    estimated_resolution_minutes: int | None
    required_skills: list[str]
    requires_approval: bool
    rollback_procedure: str | None
    current_version_id: uuid.UUID | None
