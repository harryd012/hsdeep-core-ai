"""
Pydantic v2 schemas for the Incident Analytics read
(GET /api/dashboard/incident-analytics).

One tenant-scoped aggregation that feeds the Executive Command Center's
incident charts: opened-vs-resolved trend, severity mix, open-incident aging,
a weekday x hour heatmap, resolved-duration distribution with percentiles, and
top affected sites.

TRUTH RULES (same as the rest of the dashboard payloads):
  * A measured zero is serialized as 0.
  * Percentiles with no resolved incidents in the window are ``None`` - never
    0 minutes (which would read as "instant recovery").
  * Nothing is estimated, extrapolated or back-filled. Days with no incidents
    appear with explicit zero counts so the chart axis is continuous.
  * Severity strings are passed through exactly as persisted; the client owns
    display ordering and colouring.
"""
from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel


class IncidentTrendPoint(BaseModel):
    #: UTC calendar day.
    day: dt.date
    opened: int
    resolved: int


class SeverityCount(BaseModel):
    severity: str
    count: int


class AgingBucket(BaseModel):
    key: str
    label: str
    total: int
    #: Open-incident count per persisted severity string in this age bucket.
    by_severity: dict[str, int]


class DurationBucket(BaseModel):
    key: str
    label: str
    count: int


class DurationStats(BaseModel):
    #: Resolved incidents (resolved_at inside the window) the stats cover.
    resolved_count: int
    #: None when ``resolved_count == 0``.
    median_minutes: float | None
    p90_minutes: float | None
    p95_minutes: float | None
    buckets: list[DurationBucket]


class SiteIncidentCount(BaseModel):
    #: None for incidents with no site attribution (rendered "Unassigned").
    site_id: uuid.UUID | None
    name: str
    count: int


class IncidentAnalytics(BaseModel):
    generated_at: dt.datetime
    #: Trend / severity / duration / top-site window, in UTC calendar days.
    window_days: int
    #: Heatmap window (fixed, so the 7x24 grid has comparable density).
    heatmap_days: int
    trend: list[IncidentTrendPoint]
    #: Incidents opened inside the window, by severity (sorted by count desc).
    severity: list[SeverityCount]
    total_opened: int
    #: Currently-open incidents by age. Point-in-time, NOT window-scoped.
    aging: list[AgingBucket]
    #: 7 rows (Monday..Sunday, UTC) x 24 columns (hour 0..23) of opened counts.
    heatmap: list[list[int]]
    duration: DurationStats
    top_sites: list[SiteIncidentCount]
    #: Opened-in-window incidents not covered by ``top_sites`` (the "Other" bar).
    other_sites_count: int
