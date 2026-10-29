"""
Pydantic v2 schemas for the Runbook/RunbookExecution API.

Kept separate from the ORM model, same Clean Architecture convention as
schemas/sop.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ExecutionMode = Literal["manual", "semi_automated", "automated"]
ExecutionStatus = Literal[
    "pending_approval", "approved", "rejected", "running", "succeeded", "failed", "rolled_back"
]


class RunbookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    sop_id: uuid.UUID
    sop_version_id: uuid.UUID
    name: str
    description: str | None
    execution_mode: ExecutionMode
    steps: list[dict]
    automation_ref: str | None
    requires_approval: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RunbookCreate(BaseModel):
    sop_id: uuid.UUID
    sop_version_id: uuid.UUID
    name: str
    description: str | None = None
    execution_mode: ExecutionMode = "manual"
    steps: list[dict] = []
    automation_ref: str | None = None
    requires_approval: bool = True


class RunbookExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    runbook_id: uuid.UUID
    incident_id: uuid.UUID | None
    status: ExecutionStatus
    operator_id: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    result_notes: str | None
    rollback_required: bool
    rollback_performed: bool
    created_at: datetime
    updated_at: datetime


class RunbookExecutionRequest(BaseModel):
    incident_id: uuid.UUID | None = None
    operator_id: uuid.UUID | None = None


class RunbookExecutionApproveRequest(BaseModel):
    approved_by: uuid.UUID | None = None  # ignored: attributed to the authenticated user


class RunbookExecutionRejectRequest(BaseModel):
    rejected_by: uuid.UUID | None = None  # ignored: attributed to the authenticated user
    reason: str


class RunbookExecutionStartRequest(BaseModel):
    operator_id: uuid.UUID | None = None


class RunbookExecutionCompleteRequest(BaseModel):
    success: bool
    result_notes: str | None = None
    rollback_required: bool = False


class RunbookExecutionRollbackRequest(BaseModel):
    result_notes: str | None = None
