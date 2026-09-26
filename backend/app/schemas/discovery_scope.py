"""
DiscoveryScope API schemas (Phase 6.3 §11).

Responses deliberately expose NO credential material: the credential is reported
as a boolean ``credential_configured`` plus its opaque identifier, never its
contents.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryScopeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scope_type: str
    target: str
    enabled: bool = True
    credential_profile_id: uuid.UUID | None = None
    #: Phase 9.4: name a DiscoveryProfile to supply defaults for every field
    #: below that this request does not state itself. An explicit value always
    #: wins over the profile, and omitting the profile changes nothing (today's
    #: defaults still apply) - so this is additive for every existing caller.
    profile: str | None = Field(default=None, max_length=32)
    allowed_protocols: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=1, ge=0, le=10)
    max_hosts: int = Field(default=256, ge=1, le=65536)
    max_concurrency: int = Field(default=32, ge=1, le=256)
    timeout: float = Field(default=1.0, gt=0, le=60)
    excluded_targets: list[str] = Field(default_factory=list)
    schedule: str | None = Field(default=None, max_length=100)


class DiscoveryScopeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    scope_type: str | None = None
    target: str | None = None
    enabled: bool | None = None
    credential_profile_id: uuid.UUID | None = None
    #: Phase 9.4: re-profile this scope. Limits not stated in the same request
    #: are re-stated from the profile; stated ones always win.
    profile: str | None = Field(default=None, max_length=32)
    allowed_protocols: list[str] | None = None
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_hosts: int | None = Field(default=None, ge=1, le=65536)
    max_concurrency: int | None = Field(default=None, ge=1, le=256)
    timeout: float | None = Field(default=None, gt=0, le=60)
    excluded_targets: list[str] | None = None
    schedule: str | None = Field(default=None, max_length=100)


class DiscoveryScopeOut(BaseModel):
    """Safe scope view. Never carries a password, community or API token."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    scope_type: str
    target: str
    enabled: bool
    credential_profile_id: uuid.UUID | None = None
    credential_configured: bool = False
    allowed_protocols: list[str] = Field(default_factory=list)
    max_depth: int
    max_hosts: int
    max_concurrency: int
    timeout: float
    excluded_targets: list[str] = Field(default_factory=list)
    schedule: str | None = None
    status: str
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScopeProbeHostOut(BaseModel):
    address: str
    reachable: bool
    latency_ms: float | None = None
    protocols: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class ScopeProbeResultOut(BaseModel):
    """Observation-only probe result: no devices, no canonical resources."""

    scope_id: uuid.UUID
    target: str
    hosts_probed: int
    hosts_total: int
    truncated: bool
    reachable: int
    protocol_counts: dict[str, int] = Field(default_factory=dict)
    hosts: list[ScopeProbeHostOut] = Field(default_factory=list)
