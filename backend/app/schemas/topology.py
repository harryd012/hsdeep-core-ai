"""Schemas for topology aggregation API and resource relationships."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SensorNode(BaseModel):
    id: uuid.UUID
    name: str
    metric_type: str | None = None
    status: str | None = None
    last_value_at: datetime | None = None


class MonitoringSourceNode(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    status: str | None = None
    sensors: list[SensorNode] = []


class DeviceNode(BaseModel):
    id: uuid.UUID
    name: str
    device_type: str | None = None
    hostname: str | None = None
    ip_address: str | None = None
    vendor: str | None = None
    status: str | None = None


class SiteNode(BaseModel):
    id: uuid.UUID
    name: str
    code: str | None = None
    address: str | None = None
    region: str | None = None
    devices: list[DeviceNode] = []
    monitoring_sources: list[MonitoringSourceNode] = []


class TopologyOut(BaseModel):
    tenant_id: uuid.UUID
    generated_at: datetime
    sites: list[SiteNode] = []


# ---------------------------------------------------------------------------
# Resource relationships (generic topology edges)
# ---------------------------------------------------------------------------

VALID_RELATIONSHIP_TYPES = (
    "connected_to", "depends_on", "protected_by", "managed_by",
    "monitored_by", "logged_to", "routes_through", "peers_with",
    "hosted_on", "contains", "member_of", "backup_for",
    "failover_to", "serves",
)

VALID_RELATIONSHIP_STATUSES = ("active", "degraded", "failed", "unknown")


class ResourceRelationshipBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_resource_id: uuid.UUID
    target_resource_id: uuid.UUID
    relationship_type: str
    relationship_subtype: Optional[str] = None
    label: Optional[str] = None
    weight: Optional[float] = None
    confidence: float = 100.0
    discovered_by: str = "manual"
    status: str = "active"
    attributes: dict = Field(default_factory=dict)


class ResourceRelationshipCreate(ResourceRelationshipBase):
    pass


class ResourceRelationshipUpdate(BaseModel):
    relationship_subtype: Optional[str] = None
    label: Optional[str] = None
    weight: Optional[float] = None
    confidence: Optional[float] = None
    status: Optional[str] = None
    attributes: Optional[dict] = None


class ResourceRelationshipRead(ResourceRelationshipBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    is_inferred: bool = False
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ResourceRelationshipPagination(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ResourceRelationshipRead]


class RelationshipTypeCount(BaseModel):
    relationship_type: str
    count: int


class TopologySummary(BaseModel):
    tenant_id: uuid.UUID
    total_relationships: int
    by_type: list[RelationshipTypeCount] = []
    by_status: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Graph traversal (impact / dependency views)
# ---------------------------------------------------------------------------


class TopologyDependencyNode(BaseModel):
    device_id: uuid.UUID
    device_name: Optional[str] = None
    relationship_type: str
    depth: int


class TopologyImpactOut(BaseModel):
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    direction: str  # upstream | downstream
    max_depth: int
    affected: list[TopologyDependencyNode] = []
    edges: list[dict] = []


class TopologyNeighborsOut(BaseModel):
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    neighbors: list[TopologyDependencyNode] = []
