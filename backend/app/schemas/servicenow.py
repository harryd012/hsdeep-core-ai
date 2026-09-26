"""
Pydantic v2 schemas for ServiceNow models and API endpoints.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Connection schemas
# ---------------------------------------------------------------------------

class ServiceNowConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    instance_url: str
    auth_type: str  # basic | oauth2
    is_active: bool
    pull_enabled: bool
    status: str  # pending | connected | error | disabled
    rate_limit_requests_per_minute: int
    last_sync_at: datetime | None
    last_error: str | None


class ServiceNowConnectionCreate(BaseModel):
    name: str
    instance_url: str
    auth_type: str = "oauth2"
    username: str | None = None
    password_plaintext: str | None = None  # encrypted by service layer
    oauth_client_id: str | None = None
    oauth_client_secret_plaintext: str | None = None
    webhook_secret_plaintext: str | None = None


class ServiceNowConnectionUpdate(BaseModel):
    name: str | None = None
    instance_url: str | None = None
    username: str | None = None
    password_plaintext: str | None = None
    oauth_client_secret_plaintext: str | None = None
    webhook_secret_plaintext: str | None = None


class ServiceNowConnectionTestOut(BaseModel):
    connected: bool
    message: str
    token_expiry: datetime | None = None


# ---------------------------------------------------------------------------
# Incident Link schemas
# ---------------------------------------------------------------------------

class ServiceNowIncidentLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    incident_id: uuid.UUID
    sn_sys_id: str | None
    sn_number: str | None
    correlation_id: str
    sync_direction: str
    last_pushed_hash: str | None
    last_pulled_hash: str | None
    last_pushed_at: datetime | None
    last_pulled_at: datetime | None
    has_unresolved_conflict: bool


# ---------------------------------------------------------------------------
# Sync Job schemas
# ---------------------------------------------------------------------------

class ServiceNowSyncJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    job_type: str  # incident_push | incident_pull | webhook_inbound
    status: str
    retry_count: int
    max_retries: int
    incident_id: uuid.UUID | None
    payload: dict
    last_error: str | None
    next_attempt_at: datetime


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------

class ServiceNowIncidentPushRequest(BaseModel):
    incident_id: uuid.UUID
    connection_id: uuid.UUID
    priority_map: dict[str, str] | None = None  # our_prio -> SN_priority


class IncomingWebhookPayload(BaseModel):
    """Schema for validating the inbound webhook body. Only non-sensitive
    fields are validated here; HMAC signature is verified before this schema
    is consumed."""
    correlation_id: str
    incident_number: str | None = None
    state: str | None = None


class ServiceNowSyncJobRetryRequest(BaseModel):
    pass  # no payload needed - job ID from path


class ResolveConflictRequest(BaseModel):
    """Choose which side wins a bidirectional sync conflict (Req 8.6)."""

    resolution: Literal["keep_ours", "keep_servicenow"]
