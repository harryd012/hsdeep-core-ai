"""Server fleet resources API (observability plan P5; app/services/host_resources_service.py)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ResourceReadingOut(BaseModel):
    sensor_id: uuid.UUID
    #: The sensor's name (a volume's name for disks, a core's for CPU).
    name: str
    value: float | None
    unit: str | None
    #: The sensor's own status (its thresholds decide it).
    status: str
    recorded_at: datetime | None
    #: "used", or "free" for a free amount (GB ...) that could not be turned into used.
    measure: str = "used"
    #: The free percentage the sensor reported, when `value` (used) was derived from it.
    reported_free: float | None = None


class ServerResourcesOut(BaseModel):
    device_id: uuid.UUID
    name: str
    ip_address: str | None
    #: windows | linux | vmware | other (from the reporting collector, else the OS string)
    platform: str
    os_version: str | None
    device_status: str
    cpu: ResourceReadingOut | None
    memory: ResourceReadingOut | None
    #: The fullest volume; `disks` is how many volumes the host reports.
    disk: ResourceReadingOut | None
    disks: int
    load: ResourceReadingOut | None
    uptime: ResourceReadingOut | None
    last_reading_at: datetime | None
    #: No reading for over an hour: the values are the last ones received.
    stale: bool
    critical: int
    warning: int
    peak_pct: float | None


class FleetSummaryOut(BaseModel):
    servers: int
    by_platform: dict[str, int]
    #: Servers with at least one down sensor / with warnings but none down.
    critical: int
    warning: int
    stale: int
    disk_over_90: int
    cpu_over_90: int


class ServerFleetOut(BaseModel):
    summary: FleetSummaryOut
    servers: list[ServerResourcesOut]
