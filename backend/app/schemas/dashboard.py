"""
Pydantic v2 schemas for the Dashboard API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py, schemas/device.py, and schemas/collector_run.py) so the API
contract can evolve independently of the persistence layer. Unlike those
schemas, these are plain BaseModel subclasses (no `from_attributes` config)
because dashboard responses are aggregated from multiple queries by service
code rather than mapped 1:1 from a single ORM instance.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CloudVendorOperationalStatus = Literal["operational", "degraded", "error"]
AIInsightKind = Literal["sustained_critical", "flapping_sensor", "collector_failing", "no_data"]
AIInsightSeverity = Literal["critical", "warning", "info"]


class DashboardSummary(BaseModel):
    sites: int
    devices: int
    monitoring_sources: int  # "Collectors" card
    sensors: int
    cloud_resources: int


class CloudVendorStatus(BaseModel):
    vendor: str
    resource_count: int
    status: CloudVendorOperationalStatus  # derived from that vendor's MonitoringSource.status values


class CloudStatus(BaseModel):
    vendors: list[CloudVendorStatus]
    total_resources: int


class NetworkStatus(BaseModel):
    sites_total: int
    sites_online: int  # sites with >=1 device status='up' and 0 devices status='down'
    devices_total: int
    devices_up: int
    devices_down: int


class SourceRunHealth(BaseModel):
    source_id: uuid.UUID
    source_name: str
    status: str  # mirrors MonitoringSource.status
    last_run_at: datetime | None
    last_run_status: str | None
    next_run_at: datetime | None


class AutomationStatus(BaseModel):
    sources_total: int
    sources_connected: int
    sources_error: int
    runs_last_24h: int
    runs_succeeded_last_24h: int
    success_rate_pct: float
    sources: list[SourceRunHealth]


class AIInsight(BaseModel):
    kind: AIInsightKind
    severity: AIInsightSeverity
    message: str
    device_id: uuid.UUID | None = None
    monitoring_source_id: uuid.UUID | None = None


class AIInsights(BaseModel):
    generated_at: datetime
    insights: list[AIInsight]
