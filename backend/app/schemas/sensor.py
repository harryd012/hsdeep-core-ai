"""
Pydantic v2 schemas for the Sensor Details API.

Kept separate from schemas/monitoring.py's SensorOut (per Clean
Architecture, same convention as schemas/alert.py) - SensorDetailOut adds a
denormalized device_name field for the Sensor Details page, mirroring how
AlertOut denormalizes device_name/site_name/sensor_name from
AlertRepository.list()'s join.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SensorDetailOut(BaseModel):
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

    # Denormalized display field, populated by the route handler as a plain
    # instance attribute after fetching the sensor's Device (not a mapped
    # column) - see AlertRepository.list()'s device_name for the same
    # pattern.
    device_name: str | None = None
