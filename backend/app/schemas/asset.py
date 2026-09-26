"""
CMDB / Asset Intelligence schemas.

These Pydantic models define the API contract for Sprint 15's Asset
Inventory, Asset Details, and Relationship endpoints. Frontend components
import these types from the shared types file so there is no inline
interface drift (the Sprint 14 bug where InfrastructureMap had its own
local interface that diverged from DashboardSummary).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# CMDB extensions on existing models
# ---------------------------------------------------------------------------

class DeviceCMDBOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None
    monitoring_source_id: uuid.UUID | None
    external_id: str | None
    name: str
    device_type: str
    serial_number: str | None
    status: str
    hostname: str | None
    ip_address: str | None
    mac_address: str | None
    vendor: str | None
    model: str | None
    os_version: str | None
    last_seen_at: datetime | None
    criticality: str | None
    business_service_id: uuid.UUID | None
    # CMDB fields
    environment: str | None
    business_owner: str | None
    support_team: str | None
    maintenance_window: str | None
    warranty_start: datetime | None
    warranty_end: datetime | None
    tags: list[str]
    custom_metadata: dict[str, Any]


class SiteCMDBOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    code: str
    address: str | None
    region: str | None
    criticality: str | None
    # CMDB fields
    building: str | None
    floor: str | None
    rack: str | None
    latitude: float | None
    longitude: float | None
    site_owner: str | None
    support_contact: str | None
    timezone: str | None


class AssetRelationshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    asset_id: uuid.UUID
    related_asset_id: uuid.UUID
    relationship_type: str
    label: str | None
    weight: float | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Search / filter params
# ---------------------------------------------------------------------------

class AssetSearchParams(BaseModel):
    query: str | None = Field(default=None, description="Free-text search across name, hostname, IP, serial")
    site_id: uuid.UUID | None = None
    region: str | None = None
    environment: str | None = None
    asset_type: str | None = Field(default=None, alias="device_type")
    status: str | None = None
    criticality: str | None = None
    tag: str | None = None
    limit: int = 25
    offset: int = 0


# ---------------------------------------------------------------------------
# Health summaries
# ---------------------------------------------------------------------------

class AssetHealthSummary(BaseModel):
    scope: str  # device | site | tenant | business_service
    scope_id: uuid.UUID | None
    health_score: int
    status: str
    details: dict[str, Any] | None = None


class TenantHealthSummary(BaseModel):
    tenant_id: uuid.UUID
    health_score: int
    sites_count: int
    devices_count: int
    sensors_count: int
    alerts_active: int