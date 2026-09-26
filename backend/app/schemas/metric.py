"""
Pydantic v2 schemas for the Metric API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py and schemas/monitoring.py) so the API contract can evolve
independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    monitoring_source_id: uuid.UUID | None
    device_id: uuid.UUID | None
    sensor_id: uuid.UUID | None
    metric_type: str
    value: float
    unit: str | None
    recorded_at: datetime
    created_at: datetime
