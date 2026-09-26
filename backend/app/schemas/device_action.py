"""
Pydantic v2 schemas for the Device Action API.

The API NEVER carries credentials - action params are operator parameters
(counts, timeouts, interface names); secrets travel only through the
encrypted monitoring-source columns.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionCatalogEntry(BaseModel):
    action: str
    classification: str
    provider_kind: str
    required_capability: str
    description: str
    implemented: bool


class DeviceActionRequestIn(BaseModel):
    device_id: uuid.UUID
    action: str = Field(min_length=1, max_length=50)
    params: dict[str, Any] = Field(default_factory=dict)
    incident_id: uuid.UUID | None = None
    # WHY this action is being requested (AI recommendation id/evidence or
    # a human note) - stored on the request's motivation, surfaced in audit.
    motivation: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionIn(BaseModel):
    decision: str = Field(pattern="^(ALLOW|DENY|MODIFY)$")
    note: str | None = None
    modified_params: dict[str, Any] | None = None


class DeviceActionRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    action: str
    classification: str
    provider_kind: str | None
    params: dict[str, Any]
    status: str
    motivation: dict[str, Any]
    requested_by: uuid.UUID | None
    requested_at: datetime
    resolved_by: uuid.UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    incident_id: uuid.UUID | None
    approval_request_id: uuid.UUID | None
    execution_job_id: uuid.UUID | None
    correlation_id: str
    policy_decision: dict[str, Any]
    result: dict[str, Any]
    verification: dict[str, Any]
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    created_at: datetime
    updated_at: datetime


class DeviceCapabilitiesOut(BaseModel):
    device_id: uuid.UUID
    source: str
    capabilities: dict[str, bool | None]
    firmware_version: str | None
    management_address: str | None
