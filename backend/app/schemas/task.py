"""
Pydantic schemas for Task model.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskBase(BaseModel):
    title: str = Field(..., max_length=500)
    description: str | None = None
    task_type: str = Field(..., max_length=50)
    priority: str = Field(default="P3", max_length=10)
    complexity: str = Field(default="medium", max_length=20)
    risk_level: str = Field(default="low", max_length=20)
    required_skills: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    owner_command: str | None = None
    command_source: str = Field(default="message", max_length=20)
    correlation_id: str | None = Field(None, max_length=100)
    customer_id: str | None = Field(None, max_length=100)
    project_id: str | None = Field(None, max_length=100)
    notes: str | None = None


class TaskCreate(TaskBase):
    incident_id: uuid.UUID | None = None
    ticket_id: uuid.UUID | None = None
    assigned_agent_id: uuid.UUID | None = None
    manager_agent_id: uuid.UUID | None = None
    execution_plan: dict = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    title: str | None = Field(None, max_length=500)
    description: str | None = None
    priority: str | None = Field(None, max_length=10)
    complexity: str | None = Field(None, max_length=20)
    risk_level: str | None = Field(None, max_length=20)
    required_skills: list[str] | None = None
    required_capabilities: list[str] | None = None
    notes: str | None = None


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    task_number: str
    status: str
    failure_reason: str | None
    retry_count: int
    max_retries: int
    assigned_agent_id: uuid.UUID | None
    assigned_by_agent_id: uuid.UUID | None
    manager_agent_id: uuid.UUID | None
    incident_id: uuid.UUID | None
    ticket_id: uuid.UUID | None
    execution_plan: dict
    execution_result: dict
    evidence: dict
    verification_result: dict
    verified: bool
    verified_at: datetime | None
    verified_by_agent_id: uuid.UUID | None
    recommended_sop_id: uuid.UUID | None
    sop_recommendation_score: float | None
    assigned_at: datetime | None
    accepted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    audit_log: list[dict]
    created_at: datetime
    updated_at: datetime


class TaskAssign(BaseModel):
    assigned_agent_id: uuid.UUID
    assigned_by_agent_id: uuid.UUID | None = None
    manager_agent_id: uuid.UUID | None = None


class TaskStatusChange(BaseModel):
    reason: str | None = None


class TaskExecutionSubmit(BaseModel):
    execution_result: dict
    evidence: dict | None = None


class TaskWorkerExecution(BaseModel):
    tool_name: str | None = None
    execution_input: dict | None = None
    allowed_tools: list[str] | None = None


class TaskVerification(BaseModel):
    verification_result: dict
    passed: bool = True


class TaskStatusCount(BaseModel):
    status: str
    count: int