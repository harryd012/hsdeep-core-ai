"""DiscoveryProbeJob / DiscoveryProbeHost API schemas.

Observation-only by design: these expose what a probe SAW, never canonical
inventory or credential material. The materialize endpoint's request is a set of
host addresses that MUST already exist on the persisted job - arbitrary client
IPs are rejected, not treated as discovery evidence.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryProbeHostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    address: str
    reachable: bool
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    protocols: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    materialization_outcome: str | None = None
    device_id: uuid.UUID | None = None
    materialized_at: datetime | None = None


class DiscoveryProbeJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope_id: uuid.UUID
    tenant_id: uuid.UUID
    target: str
    scope_type: str
    status: str
    hosts_probed: int
    hosts_total: int
    reachable: int
    truncated: bool
    error: str | None = None
    materialization_state: str
    started_at: datetime
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DiscoveryProbeJobDetailOut(DiscoveryProbeJobOut):
    hosts: list[DiscoveryProbeHostOut] = Field(default_factory=list)


class MaterializeRequest(BaseModel):
    """Hosts to materialize. Every address must already be in the persisted job."""

    hosts: list[str] = Field(min_length=1)


class MaterializeHostOutcome(BaseModel):
    address: str
    outcome: str  # created | existing | rejected | failed
    device_id: uuid.UUID | None = None
    error: str | None = None


class MaterializeResultOut(BaseModel):
    job_id: uuid.UUID
    materialization_state: str
    created: int = 0
    existing: int = 0
    rejected: int = 0
    failed: int = 0
    hosts: list[MaterializeHostOutcome] = Field(default_factory=list)
