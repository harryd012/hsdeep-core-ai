"""
Pydantic v2 schemas for the Site / Infrastructure API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/device.py and schemas/alert.py) so the API contract can evolve
independently of the persistence layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    code: str
    address: str | None
    created_at: datetime
    updated_at: datetime


class SiteSummary(BaseModel):
    """Aggregated view of a Site for the Infrastructure page's Site cards.
    Unlike SiteOut, this isn't mapped 1:1 from the ORM model - it's built by
    InfrastructureService from a Site plus a device-status breakdown, same
    convention as schemas/dashboard.py's plain BaseModel aggregates."""

    id: uuid.UUID
    name: str
    code: str
    address: str | None
    # Best-effort: the most common Device.vendor at this site, or None if
    # this site has no devices or there's no single most-common vendor
    # (computed in InfrastructureService, not here).
    vendor: str | None = None
    devices_total: int
    #: Canonical healthy bucket - `status in (up, online, active, ok, ...)`,
    #: NOT just `status == "up"`. The live database stores "online" for a
    #: healthy device as well as "up" (93 rows currently), so the old
    #: `status == "up"` reading silently hid those devices. Delegates to
    #: `infrastructure_overview_service.device_health_bucket`.
    devices_online: int
    #: Canonical critical bucket - `status in (down, offline, critical,
    #: failed, error)`, not only "down".
    devices_offline: int
    #: Canonical warning bucket - `status in (warning, degraded, alerting,
    #: maintenance)`.
    devices_warning: int
    #: Intentionally always 0. The critical signal lives in `devices_offline`;
    #: keeping this at 0 guarantees the four device fields never double-count
    #: the same device under two labels.
    devices_critical: int = 0
    #: Devices whose stored status is unrecognised/NULL. Makes
    #: online + warning + offline + critical + unknown == devices_total, so the
    #: UI can always reconcile the parts against the total.
    devices_unknown: int = 0

