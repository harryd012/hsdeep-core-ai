"""Pydantic v2 schemas for the Enterprise SLA Engine."""
from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict


class SLAPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    priority: str
    business_service_id: uuid.UUID | None
    response_time_minutes: int
    resolution_time_minutes: int
    warning_threshold_percent: int
    use_business_hours: bool
    business_hours_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SLAPolicyCreate(BaseModel):
    name: str
    description: str | None = None
    priority: str
    business_service_id: uuid.UUID | None = None
    response_time_minutes: int
    resolution_time_minutes: int
    warning_threshold_percent: int = 80
    use_business_hours: bool = False
    business_hours_id: uuid.UUID | None = None
    is_active: bool = True


class SLAPolicyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    priority: str | None = None
    business_service_id: uuid.UUID | None = None
    response_time_minutes: int | None = None
    resolution_time_minutes: int | None = None
    warning_threshold_percent: int | None = None
    use_business_hours: bool | None = None
    business_hours_id: uuid.UUID | None = None
    is_active: bool | None = None


class BusinessHoursOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    timezone: str
    monday_start: time | None
    monday_end: time | None
    tuesday_start: time | None
    tuesday_end: time | None
    wednesday_start: time | None
    wednesday_end: time | None
    thursday_start: time | None
    thursday_end: time | None
    friday_start: time | None
    friday_end: time | None
    saturday_start: time | None
    saturday_end: time | None
    sunday_start: time | None
    sunday_end: time | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BusinessHoursCreate(BaseModel):
    name: str
    timezone: str = "UTC"
    monday_start: time | None = None
    monday_end: time | None = None
    tuesday_start: time | None = None
    tuesday_end: time | None = None
    wednesday_start: time | None = None
    wednesday_end: time | None = None
    thursday_start: time | None = None
    thursday_end: time | None = None
    friday_start: time | None = None
    friday_end: time | None = None
    saturday_start: time | None = None
    saturday_end: time | None = None
    sunday_start: time | None = None
    sunday_end: time | None = None
    is_active: bool = True


class BusinessHoursUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
    monday_start: time | None = None
    monday_end: time | None = None
    tuesday_start: time | None = None
    tuesday_end: time | None = None
    wednesday_start: time | None = None
    wednesday_end: time | None = None
    thursday_start: time | None = None
    thursday_end: time | None = None
    friday_start: time | None = None
    friday_end: time | None = None
    saturday_start: time | None = None
    saturday_end: time | None = None
    sunday_start: time | None = None
    sunday_end: time | None = None
    is_active: bool | None = None


class HolidayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    holiday_date: datetime
    is_recurring: bool
    created_at: datetime
    updated_at: datetime


class HolidayCreate(BaseModel):
    name: str
    description: str | None = None
    holiday_date: datetime
    is_recurring: bool = False


class SLAStatusOut(BaseModel):
    policy_id: str | None
    response_due_at: str | None
    resolution_due_at: str | None
    is_breached: bool
    breached_at: str | None
    is_paused: bool
    paused_at: str | None
    paused_time_seconds: int
    warning_sent: bool
    response_remaining_seconds: int | None
    resolution_remaining_seconds: int | None
    response_percent_elapsed: int | None
    resolution_percent_elapsed: int | None


class SLADashboardStats(BaseModel):
    total_tickets_with_sla: int = 0
    breached_count: int = 0
    warning_count: int = 0
    paused_count: int = 0
    on_track_count: int = 0