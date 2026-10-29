"""
Pydantic v2 schemas for the Workflow Definition/Execution API.

Kept separate from the ORM model, same Clean Architecture convention as
schemas/sop.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

WorkflowType = Literal[
    "incident", "alert", "runbook", "approval", "maintenance", "notification",
    "servicenow", "jira", "teams", "slack", "webhook", "email", "self_healing", "ai",
]
WorkflowStatus = Literal["draft", "approved", "deprecated"]
ExecutionStatus = Literal[
    "draft", "ready", "queued", "waiting_approval", "running", "paused",
    "retrying", "succeeded", "failed", "cancelled", "rolled_back",
]


class WorkflowVersionHistoryEntry(BaseModel):
    version_number: int
    definition: dict
    status: str
    change_summary: str | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    archived_at: datetime


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_number: str
    name: str
    description: str | None
    workflow_type: str
    trigger_type: str | None
    trigger_filter: dict
    tags: list[str]
    is_active: bool
    definition: dict
    version_number: int
    status: WorkflowStatus
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkflowDetailOut(WorkflowOut):
    version_history: list[WorkflowVersionHistoryEntry] = []


class WorkflowCreate(BaseModel):
    workflow_number: str
    name: str
    description: str | None = None
    workflow_type: WorkflowType
    trigger_type: str | None = None
    trigger_filter: dict = {}
    tags: list[str] = []
    definition: dict


class WorkflowNewVersionRequest(BaseModel):
    definition: dict
    change_summary: str | None = None


class WorkflowApproveRequest(BaseModel):
    approved_by: uuid.UUID | None = None  # ignored: attributed to the authenticated user


class WorkflowExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_version_number: int
    trigger_source: str
    incident_id: uuid.UUID | None
    alert_id: uuid.UUID | None
    status: ExecutionStatus
    variables: dict
    current_node_id: str | None
    step_history: list[dict]
    audit_log: list[dict]
    retry_count: int
    failure_reason: str | None
    rollback_required: bool
    rollback_performed: bool
    requested_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    created_at: datetime
    updated_at: datetime


class WorkflowExecutionSummaryOut(BaseModel):
    """Lighter-weight shape for list endpoints - omits step_history/
    audit_log (can be large) the same way a "list" endpoint elsewhere in
    this codebase omits detail-only fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    trigger_source: str
    incident_id: uuid.UUID | None
    alert_id: uuid.UUID | None
    status: ExecutionStatus
    retry_count: int
    failure_reason: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None


class WorkflowExecutionRequest(BaseModel):
    incident_id: uuid.UUID | None = None
    alert_id: uuid.UUID | None = None
    requested_by: uuid.UUID | None = None
    initial_variables: dict = {}


class WorkflowExecutionApproveRequest(BaseModel):
    node_id: str
    approved_by: uuid.UUID | None = None  # ignored: attributed to the authenticated user
    comment: str | None = None


class WorkflowExecutionRejectRequest(BaseModel):
    node_id: str
    rejected_by: uuid.UUID | None = None  # ignored: attributed to the authenticated user
    reason: str


class WorkflowExecutionActionRequest(BaseModel):
    """Shared body for pause/resume/cancel/rollback."""

    actor_id: uuid.UUID | None = None
    reason: str | None = None


class WorkflowStatisticsOut(BaseModel):
    total_executions: int
    queued: int
    waiting_approval: int
    running: int
    succeeded: int
    failed: int
    cancelled: int
    rolled_back: int
    success_rate: float | None
    avg_duration_seconds: float | None
