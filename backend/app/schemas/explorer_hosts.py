"""
Pydantic v2 schemas for the Hosts & Devices Explorer
(GET /api/explorer/hosts).

A Dynatrace-style entity explorer payload: a filtered, paginated host table
plus FACET counts for each filterable dimension, in one read.

TRUTH RULES:
  * ``health`` is DERIVED from the host's currently-open alerts (critical if it
    has any open critical alert, warning if it has any open warning alert,
    otherwise ``no_alerts``). It is NOT ``Device.health`` (a stored bucket that
    is NULL until a health pass has run) and never claims "healthy" for a host
    that merely has no alerts - ``no_alerts`` says exactly what is known.
  * Alert counts are open + acknowledged, non-suppressed alerts attached to the
    device (the same set the Active Alerts view shows).
  * Facet counts are computed with the facet's OWN filter removed (standard
    faceted-search behaviour), so selecting "Cisco" still shows how many hosts
    exist for the other vendors.
  * Nothing about availability percentages is returned: the platform stores no
    uptime history, so no availability figure is fabricated.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel

HostHealth = Literal["critical", "warning", "no_alerts"]


class HostAlertCounts(BaseModel):
    critical: int = 0
    warning: int = 0
    info: int = 0


class HostRow(BaseModel):
    id: uuid.UUID
    name: str
    hostname: str | None = None
    ip_address: str | None = None
    device_type: str
    vendor: str | None = None
    provider: str | None = None
    status: str
    criticality: str | None = None
    environment: str | None = None
    lifecycle_state: str
    site_id: uuid.UUID | None = None
    site_name: str | None = None
    health: HostHealth
    alerts: HostAlertCounts
    last_seen_at: dt.datetime | None = None
    last_telemetry_at: dt.datetime | None = None


class FacetValue(BaseModel):
    value: str
    count: int


class HostFacets(BaseModel):
    #: Always the three health buckets, in severity order, zero-filled.
    health: list[FacetValue]
    status: list[FacetValue]
    device_type: list[FacetValue]
    vendor: list[FacetValue]
    criticality: list[FacetValue]
    environment: list[FacetValue]


class HostsExplorer(BaseModel):
    generated_at: dt.datetime
    #: Hosts matching ALL active filters (drives pagination).
    total: int
    limit: int
    offset: int
    rows: list[HostRow]
    facets: HostFacets
