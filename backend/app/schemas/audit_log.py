"""Schemas for the tenant audit log (GET /api/audit/events)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    action: str
    actor_id: uuid.UUID | None = None
    actor_email: str | None = None
    actor_type: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    description: str | None = None
    old_values: dict = {}
    new_values: dict = {}
    ip_address: str | None = None


class AuditPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[AuditEventOut]


class AuditFacetsOut(BaseModel):
    actions: list[str]
    entity_types: list[str]
