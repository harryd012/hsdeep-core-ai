"""Change Request schema for API responses."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ChangeRequestBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str = Field(..., max_length=500)
    description: str
    change_type: str = Field(..., pattern="^(config|software|hardware|security|network|cloud|database|application|other)$")
    risk_level: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    priority: str = Field(default="normal", pattern="^(low|normal|high|critical|emergency)$")
    business_justification: Optional[str] = None
    implementation_plan: Optional[str] = None
    backout_plan: Optional[str] = None
    test_plan: Optional[str] = None
    validation_plan: Optional[str] = None
    security_impact: Optional[str] = None
    affected_devices: list = Field(default_factory=list)
    affected_services: list = Field(default_factory=list)
    downtime_expected: bool = False
    downtime_duration_minutes: int = 0
    maintenance_window_start: Optional[datetime] = None
    maintenance_window_end: Optional[datetime] = None


class ChangeRequestCreate(ChangeRequestBase):
    tenant_id: uuid.UUID


class ChangeRequestUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    status: Optional[str] = None
    business_justification: Optional[str] = None
    implementation_plan: Optional[str] = None
    backout_plan: Optional[str] = None
    test_plan: Optional[str] = None
    validation_plan: Optional[str] = None
    security_impact: Optional[str] = None
    affected_devices: Optional[list] = None
    affected_services: Optional[list] = None
    downtime_expected: Optional[bool] = None
    downtime_duration_minutes: Optional[int] = None
    maintenance_window_start: Optional[datetime] = None
    maintenance_window_end: Optional[datetime] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    approval_notes: Optional[str] = None
    implemented_by: Optional[uuid.UUID] = None
    implemented_at: Optional[datetime] = None
    implementation_result: Optional[str] = None
    rollback_executed: Optional[bool] = None
    verified_by: Optional[uuid.UUID] = None
    verified_at: Optional[datetime] = None
    verification_result: Optional[str] = None
    closed_at: Optional[datetime] = None
    closure_notes: Optional[str] = None


class ChangeRequestRead(ChangeRequestBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    change_number: str
    status: str
    requested_by: Optional[uuid.UUID] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    implemented_by: Optional[uuid.UUID] = None
    implemented_at: Optional[datetime] = None
    verified_by: Optional[uuid.UUID] = None
    verified_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ChangeRequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_changes: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_risk_level: dict[str, int]
