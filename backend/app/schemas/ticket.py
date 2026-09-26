"""
Pydantic v2 schemas for the Enterprise Ticket Management Engine.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TicketCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    ticket_id: uuid.UUID
    author_id: uuid.UUID | None
    author_name: str | None
    body: str
    is_internal: bool
    is_synced: bool
    external_comment_id: str | None
    created_at: datetime
    updated_at: datetime


class TicketAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    ticket_id: uuid.UUID
    uploaded_by: uuid.UUID | None
    filename: str
    content_type: str | None
    size_bytes: int | None
    storage_key: str
    is_synced: bool
    external_attachment_id: str | None
    created_at: datetime
    updated_at: datetime


class TicketLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    ticket_id: uuid.UUID
    external_id: str | None
    external_number: str | None
    correlation_id: str
    sync_direction: str
    last_pushed_hash: str | None
    last_pulled_hash: str | None
    last_pushed_at: datetime | None
    last_pulled_at: datetime | None
    has_unresolved_conflict: bool
    conflict_details: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TicketHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    ticket_id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_name: str | None
    action_type: str
    field_name: str | None
    old_value: str | None
    new_value: str | None
    context: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    incident_id: uuid.UUID | None
    ticket_number: str
    title: str
    description: str | None
    source: str
    status: str
    priority: str | None
    category: str | None
    subcategory: str | None
    assigned_to: uuid.UUID | None
    assigned_team: str | None
    assignment_group: str | None
    assigned_group_id: uuid.UUID | None
    # Legacy SLA
    sla_due_at: datetime | None
    sla_breached: bool
    # Enterprise SLA Engine
    sla_policy_id: uuid.UUID | None
    sla_response_due_at: datetime | None
    sla_resolution_due_at: datetime | None
    sla_breached_at: datetime | None
    sla_warning_sent: bool
    sla_paused_at: datetime | None
    sla_paused_time_seconds: int
    # Lifecycle timestamps
    opened_at: datetime
    assigned_at: datetime | None
    in_progress_at: datetime | None
    pending_approval_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    reopened_at: datetime | None
    # Escalation
    escalation_level: int
    escalated_at: datetime | None
    escalated_by: uuid.UUID | None
    # AI fields
    ai_ticket_summary: str | None
    ai_executive_summary: str | None
    ai_engineer_recommendation: str | None
    ai_customer_update: str | None
    ai_playbook_selection: str | None
    ai_execution_confidence: float | None
    ai_automation_recommendation: str | None
    knowledge_article_id: uuid.UUID | None
    watchers: list[dict[str, Any]]
    tags: list[str]
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    is_deleted: bool


class TicketCreate(BaseModel):
    title: str
    description: str | None = None
    incident_id: uuid.UUID | None = None
    source: str = "manual"
    priority: str | None = None
    category: str | None = None
    subcategory: str | None = None
    assigned_to: uuid.UUID | None = None
    assigned_team: str | None = None
    assignment_group: str | None = None
    assigned_group_id: uuid.UUID | None = None
    sla_due_at: datetime | None = None
    custom_fields: dict[str, Any] = {}
    tags: list[str] = []
    created_by: uuid.UUID | None = None


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    category: str | None = None
    subcategory: str | None = None
    assigned_to: uuid.UUID | None = None
    assigned_team: str | None = None
    assignment_group: str | None = None
    assigned_group_id: uuid.UUID | None = None
    sla_due_at: datetime | None = None
    custom_fields: dict[str, Any] | None = None
    tags: list[str] | None = None
    updated_by: uuid.UUID | None = None


class TicketAssign(BaseModel):
    assigned_to: uuid.UUID
    assigned_by: uuid.UUID | None = None


class TicketCommentCreate(BaseModel):
    body: str
    author_id: uuid.UUID | None = None
    author_name: str | None = None
    is_internal: bool = True


class TicketListResponse(BaseModel):
    items: list[TicketOut]
    total: int
    limit: int
    offset: int


class TicketDashboardStats(BaseModel):
    open_count: int = 0
    assigned_count: int = 0
    in_progress_count: int = 0
    pending_approval_count: int = 0
    pending_customer_count: int = 0
    pending_vendor_count: int = 0
    resolved_today_count: int = 0
    closed_today_count: int = 0
    escalated_count: int = 0
    overdue_count: int = 0
    automation_pending_count: int = 0
    sla_breach_count: int = 0
    priority_distribution: dict[str, int] = {}
    recent_activity: list[TicketHistoryOut] = []


class TicketTransition(BaseModel):
    """Request to transition a ticket to a new state."""
    to_state: str
    reason: str | None = None
    context: dict[str, Any] = {}


class TicketTransitionResponse(BaseModel):
    """Response from a state transition."""
    ticket: TicketOut
    available_transitions: list[str]