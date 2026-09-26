"""
Pydantic v2 schemas for the Device API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py and schemas/monitoring.py) so the API contract can evolve
independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID | None
    monitoring_source_id: uuid.UUID | None
    external_id: str | None
    name: str
    device_type: str
    vendor: str | None
    model: str | None
    os_version: str | None
    hostname: str | None
    ip_address: str | None
    mac_address: str | None
    status: str
    serial_number: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime
