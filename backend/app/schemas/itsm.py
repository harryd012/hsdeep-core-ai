"""
Pydantic v2 schemas for the Universal ITSM Framework.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# ITSM Provider schemas
# ---------------------------------------------------------------------------

class ITSMProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_type: str
    display_name: str
    supported_auth_modes: list[str]
    capabilities: dict[str, Any]
    default_rate_limit_rpm: int
    is_active: bool


# ---------------------------------------------------------------------------
# ITSM Connection schemas
# ---------------------------------------------------------------------------

class ITSMConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    provider_id: uuid.UUID | None
    provider_type: str
    name: str
    instance_url: str
    auth_type: str
    rate_limit_rpm: int
    pull_enabled: bool
    is_active: bool
    status: str
    last_sync_at: datetime | None
    last_error: str | None
    last_health_check_at: datetime | None
    last_health_check_ok: bool | None
    config: dict[str, Any]
    token_expires_at: datetime | None


class ITSMConnectionCreate(BaseModel):
    provider_type: str
    name: str
    instance_url: str
    auth_type: str = "oauth2"
    credentials: dict[str, Any]
    rate_limit_rpm: int | None = None
    pull_enabled: bool = True
    config: dict[str, Any] = {}
    webhook_secret: str | None = None


class ITSMConnectionUpdate(BaseModel):
    name: str | None = None
    instance_url: str | None = None
    auth_type: str | None = None
    credentials: dict[str, Any] | None = None
    rate_limit_rpm: int | None = None
    pull_enabled: bool | None = None
    config: dict[str, Any] | None = None
    webhook_secret: str | None = None
    is_active: bool | None = None


class ITSMConnectionTestOut(BaseModel):
    connected: bool
    message: str
    latency_ms: float | None = None
    provider_type: str | None = None


# ---------------------------------------------------------------------------
# ITSM Sync Job schemas
# ---------------------------------------------------------------------------

class ITSMSyncJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    job_type: str
    status: str
    ticket_id: uuid.UUID | None
    ticket_link_id: uuid.UUID | None
    payload: dict[str, Any]
    retry_count: int
    max_retries: int
    next_attempt_at: datetime
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None


# ---------------------------------------------------------------------------
# Webhook schemas
# ---------------------------------------------------------------------------

class ITSMWebhookResult(BaseModel):
    status: str
    correlation_id: str | None = None
    external_id: str | None = None
    external_number: str | None = None
    message: str | None = None