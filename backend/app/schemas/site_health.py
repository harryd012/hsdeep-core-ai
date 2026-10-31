"""
Site Health schema (ECC-1 Executive Command Center - Sites honeycomb).

One row per site: device health in the SAME buckets as
``CommandCenterBrief.infrastructure_health.devices`` plus the site's active
incident count. ``worst_health`` is derived, never stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.dashboard import DeviceHealthBuckets

#: Worst-first. A site with no devices is ``unknown`` - "nothing to measure",
#: not "healthy".
SiteWorstHealth = Literal["down", "critical", "warning", "healthy", "unknown"]


class SiteHealthItem(BaseModel):
    site_id: uuid.UUID
    code: str
    name: str
    region: str | None = None
    criticality: str | None = None
    devices: DeviceHealthBuckets
    #: active = open + acknowledged + investigating (matches ResponseKpis).
    open_incidents: int
    critical_incidents: int
    worst_health: SiteWorstHealth


class SiteHealth(BaseModel):
    generated_at: datetime
    #: Sorted worst_health first, then open_incidents desc, then code.
    sites: list[SiteHealthItem]
    #: Devices with no site attribution (shown, never silently dropped).
    unassigned_devices: int
