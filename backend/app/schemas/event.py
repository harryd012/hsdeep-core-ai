"""
Pydantic v2 schemas for the Event API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py and schemas/monitoring.py) so the API contract can evolve
independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID | None
    monitoring_source_id: uuid.UUID | None
    external_id: str | None
    event_type: str
    severity: str
    message: str
    occurred_at: datetime
    raw_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime
