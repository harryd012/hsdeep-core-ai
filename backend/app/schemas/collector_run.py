"""
Pydantic v2 schemas for the CollectorRun API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py, schemas/device.py, and schemas/monitoring.py) so the API
contract can evolve independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

CollectorRunTrigger = Literal["scheduler", "manual"]
CollectorRunStatus = Literal["running", "success", "partial", "error", "skipped"]


class CollectorRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    monitoring_source_id: uuid.UUID
    trigger: CollectorRunTrigger
    status: CollectorRunStatus
    error: str | None
    devices_upserted: int
    interfaces_upserted: int
    sensors_upserted: int
    metrics_recorded: int
    events_recorded: int
    alerts_upserted: int
    started_at: datetime
    finished_at: datetime | None
