"""
Pydantic schemas for Agent model.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentBase(BaseModel):
    agent_id: str = Field(..., max_length=50, examples=["CTO-001"])
    name: str = Field(..., max_length=200, examples=["Chief Technology Officer"])
    role: str = Field(..., max_length=100, examples=["cto"])
    department: str = Field(..., max_length=100, examples=["engineering"])
    level: str = Field(..., max_length=20, examples=["cto"])
    description: str | None = None
    avatar_url: str | None = None
    skills: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    supported_task_types: list[str] = Field(default_factory=list)
    permissions: dict = Field(default_factory=dict)
    max_workload: int = Field(default=5, ge=1, le=50)
    config: dict = Field(default_factory=dict)


class AgentCreate(AgentBase):
    manager_id: uuid.UUID | None = None
    status: str = Field(default="active", max_length=20)
    availability: str = Field(default="available", max_length=20)


class AgentUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    description: str | None = None
    avatar_url: str | None = None
    skills: list[str] | None = None
    capabilities: list[str] | None = None
    supported_task_types: list[str] | None = None
    permissions: dict | None = None
    max_workload: int | None = Field(None, ge=1, le=50)
    config: dict | None = None
    status: str | None = Field(None, max_length=20)
    availability: str | None = Field(None, max_length=20)
    manager_id: uuid.UUID | None = None


class AgentRead(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    manager_id: uuid.UUID | None
    status: str
    availability: str
    is_active: bool
    current_workload: int
    tasks_completed: int
    tasks_failed: int
    avg_resolution_minutes: float | None
    success_rate: float
    last_active_at: datetime | None
    created_at: datetime
    updated_at: datetime
    is_available_for_work: bool
    can_accept_task: bool


class AgentTreeNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: str
    name: str
    role: str
    department: str
    level: str
    status: str
    availability: str
    current_workload: int
    max_workload: int
    is_available: bool
    success_rate: float
    skills: list[str]
    children: list["AgentTreeNode"] = Field(default_factory=list)


AgentTreeNode.model_rebuild()