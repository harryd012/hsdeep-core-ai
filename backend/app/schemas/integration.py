"""
Pydantic v2 schemas for the Integration API.

Integrations are provider-neutral connections to external systems
(PNETLab, FortiGate, Security Onion, AWS, Azure, GCP, etc).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IntegrationProviderType = Literal[
    "network", "security", "cloud", "datacenter", "monitoring", "itsm",
]
IntegrationStatus = Literal[
    "connected", "degraded", "disconnected", "authentication_failed",
    "syncing", "discovery_failed", "disabled",
]


class IntegrationBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = Field(None, max_length=500)
    provider_type: IntegrationProviderType
    provider: str = Field(..., max_length=100)
    capabilities: list[str] = Field(default_factory=list)
    configuration: dict = Field(default_factory=dict)


class IntegrationCreate(IntegrationBase):
    tenant_id: uuid.UUID | None = None  # derived from auth if None
    credential_reference: str | None = Field(None, max_length=500)


class IntegrationUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=500)
    status: IntegrationStatus | None = None
    capabilities: list[str] | None = None
    configuration: dict | None = None
    credential_reference: str | None = Field(None, max_length=500)


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    provider_type: str
    provider: str
    status: str
    health_score: int
    capabilities: list[str]
    configuration: dict
    discovery_state: str
    synchronization_state: str
    last_successful_collection: datetime | None
    last_discovery: datetime | None
    last_synchronization: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    telemetry_rate_per_minute: int
    event_rate_per_minute: int
    total_events_ingested: int
    created_at: datetime
    updated_at: datetime


class IntegrationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_type: str
    provider: str
    status: str
    health_score: int
    last_successful_collection: datetime | None
    total_events_ingested: int


class IntegrationPagination(BaseModel):
    items: list[IntegrationListItem]
    total: int
    limit: int
    offset: int


class IntegrationSummary(BaseModel):
    total: int
    connected: int
    degraded: int
    disconnected: int
    disabled: int
    total_events_ingested: int
