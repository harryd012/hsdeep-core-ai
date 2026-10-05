"""
Pydantic v2 schemas for the Monitoring Engine API.

Kept separate from the ORM models (per Clean Architecture) so API contracts
can evolve independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# MonitoringSource
# ---------------------------------------------------------------------------
class MonitoringSourceCreate(BaseModel):
    # TODO(auth): tenant_id should come from the authenticated principal
    # once the auth module lands, not from the request body. Kept explicit
    # here because there is no auth dependency to derive it from yet.
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    # Connection details for vendor collectors (additive; mirrors the
    # MonitoringSource model columns). All optional since not every source
    # type needs every field (e.g. an SNMP source has no api_key).
    vendor: str | None = Field(default=None, max_length=100)
    hostname: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=45)  # IPv4/IPv6
    port: int | None = None
    protocol: str | None = Field(default=None, max_length=20)  # https | ssh | snmp | wmi | winrm
    verify_ssl: bool | None = False

    # Write-only plaintext credentials accepted from the client. These are
    # never stored as-is - the service layer (task 16.1) encrypts them into
    # api_key_encrypted/password_encrypted before persistence. They must
    # never be echoed back in MonitoringSourceOut.
    api_key: str | None = None
    snmp_version: str | None = Field(default=None, max_length=10)  # v1 | v2c | v3
    username: str | None = Field(default=None, max_length=150)
    password: str | None = None

    poll_interval_seconds: int = 60


class MonitoringSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    site_id: uuid.UUID | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None

    # Same new connection/credential fields as MonitoringSourceCreate, but
    # all default to None here since None means "leave unchanged" for a
    # partial update (consistent with the fields above).
    vendor: str | None = Field(default=None, max_length=100)
    hostname: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=45)
    port: int | None = None
    protocol: str | None = Field(default=None, max_length=20)
    verify_ssl: bool | None = None

    api_key: str | None = None
    snmp_version: str | None = Field(default=None, max_length=10)
    username: str | None = Field(default=None, max_length=150)
    password: str | None = None

    poll_interval_seconds: int | None = None


class MonitoringSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None
    name: str
    source_type: str
    config: dict[str, Any]
    status: str
    last_sync_at: datetime | None
    last_error: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    vendor: str | None
    hostname: str | None
    ip_address: str | None
    port: int | None
    protocol: str | None
    verify_ssl: bool | None
    snmp_version: str | None
    username: str | None
    poll_interval_seconds: int

    # INTENTIONAL OMISSION - do not add these fields: api_key, password,
    # api_key_encrypted, password_encrypted, or any other credential-derived
    # field. Requirement 9.2 / Correctness Property 6 require that secrets
    # are never returned by the API. "Is a credential configured" is exposed
    # ONLY as the boolean below, never as the value itself.
    credential_configured: bool = False


class ConnectionTestResult(BaseModel):
    success: bool
    message: str
    checked_at: datetime


class ReadinessResultOut(BaseModel):
    """READ-ONLY readiness diagnostic for one discovery source (Phase 3B §10).

    Carries no credentials: the service builds every field through
    `app/services/monitoring/readiness.build_result`, which redacts configured
    secrets and derives `target` from hostname/port only.
    """

    status: str
    stage: str
    message: str
    target: str
    protocol: str
    safe_details: dict[str, Any] = Field(default_factory=dict)


class ReadinessReportOut(BaseModel):
    sources_checked: int
    results: list[ReadinessResultOut] = Field(default_factory=list)
    #: Per-protocol coverage. Includes an explicit "not configured" entry for a
    #: protocol with no source, so an absent SNMP/WMI line is never ambiguous.
    coverage: list[ReadinessResultOut] = Field(default_factory=list)


class SyncResult(BaseModel):
    source_id: uuid.UUID
    status: str
    sensors_created: int
    sensors_updated: int
    sensors_total: int
    synced_at: datetime
    error: str | None = None


class SupportedSourceType(BaseModel):
    source_type: str
    display_name: str
    implemented: bool


# ---------------------------------------------------------------------------
# Sensor
# ---------------------------------------------------------------------------
class SensorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    monitoring_source_id: uuid.UUID
    device_id: uuid.UUID | None
    external_id: str
    name: str
    metric_type: str
    unit: str | None
    current_value: float | None
    warning_threshold: float | None
    critical_threshold: float | None
    status: str
    last_value_at: datetime | None
    created_at: datetime
    updated_at: datetime
