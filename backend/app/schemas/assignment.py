"""Pydantic v2 schemas for the Enterprise Assignment Engine."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssignmentGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    strategy: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AssignmentGroupCreate(BaseModel):
    name: str
    description: str | None = None
    strategy: str = "round_robin"
    is_active: bool = True


class AssignmentGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    strategy: str | None = None
    is_active: bool | None = None


class AssignmentGroupMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    group_id: uuid.UUID
    user_id: uuid.UUID
    skills_json: list[str]
    capacity_weight: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AssignmentGroupMemberCreate(BaseModel):
    user_id: uuid.UUID
    skills_json: list[str] = []
    capacity_weight: int = 1
    is_active: bool = True


class AssignmentGroupMemberUpdate(BaseModel):
    skills_json: list[str] | None = None
    capacity_weight: int | None = None
    is_active: bool | None = None


class SmartAssignRequest(BaseModel):
    group_id: uuid.UUID | None = None
    strategy: str | None = None
    assigned_by: uuid.UUID | None = None
    reason: str | None = None


class AssignmentMetrics(BaseModel):
    group_id: uuid.UUID
    group_name: str
    total_members: int
    active_members: int
    open_tickets: int