"""
Pydantic v2 schemas for the Interface API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/device.py, schemas/alert.py, and schemas/monitoring.py) so the API
contract can evolve independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InterfaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    monitoring_source_id: uuid.UUID | None
    external_id: str
    name: str
    mac_address: str | None
    admin_status: str
    oper_status: str
    speed_mbps: float | None
    in_octets: float | None
    out_octets: float | None
    in_errors: float | None
    out_errors: float | None
    last_status_change_at: datetime | None
    created_at: datetime
    updated_at: datetime
