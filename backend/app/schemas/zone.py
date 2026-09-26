"""Zone schema for API responses."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ZoneBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=500)
    zone_type: str = Field(default="network", pattern="^(network|security|cloud|application)$")


class ZoneCreate(ZoneBase):
    site_id: uuid.UUID
    tenant_id: uuid.UUID


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    zone_type: Optional[str] = Field(None, pattern="^(network|security|cloud|application)$")


class ZoneRead(ZoneBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ZoneSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_zones: int
    by_type: dict[str, int]
