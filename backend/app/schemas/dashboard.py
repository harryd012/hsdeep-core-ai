"""
Pydantic v2 schemas for the Dashboard API.

Kept separate from the ORM model (per Clean Architecture, same convention as
schemas/alert.py, schemas/device.py, and schemas/collector_run.py) so the API
contract can evolve independently of the persistence layer. Unlike those
schemas, these are plain BaseModel subclasses (no `from_attributes` config)
because dashboard responses are aggregated from multiple queries by service
code rather than mapped 1:1 from a single ORM instance.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CloudVendorOperationalStatus = Literal["operational", "degraded", "error"]
AIInsightKind = Literal["sustained_critical", "flapping_sensor", "collector_failing", "no_data"]
AIInsightSeverity = Literal["critical", "warning", "info"]


class DashboardSummary(BaseModel):
    sites: int
    devices: int
    monitoring_sources: int  # "Collectors" card
    sensors: int
    cloud_resources: int


class CloudVendorStatus(BaseModel):
    vendor: str
    resource_count: int
    status: CloudVendorOperationalStatus  # derived from that vendor's MonitoringSource.status values


class CloudStatus(BaseModel):
    vendors: list[CloudVendorStatus]
    total_resources: int


class NetworkStatus(BaseModel):
    sites_total: int
    sites_online: int  # sites with >=1 device status='up' and 0 devices status='down'
    devices_total: int
    devices_up: int
    devices_down: int


class SourceRunHealth(BaseModel):
    source_id: uuid.UUID
    source_name: str
    status: str  # mirrors MonitoringSource.status
    last_run_at: datetime | None
    last_run_status: str | None
    next_run_at: datetime | None


class AutomationStatus(BaseModel):
    sources_total: int
    sources_connected: int
    sources_error: int
    runs_last_24h: int
    runs_succeeded_last_24h: int
    success_rate_pct: float
    sources: list[SourceRunHealth]


class AIInsight(BaseModel):
    kind: AIInsightKind
    severity: AIInsightSeverity
    message: str
    device_id: uuid.UUID | None = None
    monitoring_source_id: uuid.UUID | None = None


class AIInsights(BaseModel):
    generated_at: datetime
    insights: list[AIInsight]


# ---------------------------------------------------------------------------
# PHASE 6A - Operational Intelligence
# ---------------------------------------------------------------------------
# The typed contract for ONE tenant-scoped aggregation read
# (GET /api/dashboard/operational-intelligence).
#
# TRUTH RULES (the same ones the dashboard already follows - see
# AttentionRequired / commandKpis.ts):
#   * A MEASURED zero is a real answer and is serialized as 0.
#   * A RATE with no denominator is NOT 0% and NOT 100%: it is `None`, which
#     the client renders as "NO DATA" / "NOT CONFIGURED" / "NO COLLECTION".
#     Returning 0.0 there would report a total collection outage, and
#     returning 100.0 would report perfect health - both would be lies.
#   * Counts are never clamped, extrapolated or back-filled.
#
# Every field below is derived from a persisted column on an existing canonical
# entity (MonitoringSource / CollectorRun / Sensor / Device / Alert / Task).
# There is no separate KPI store and no denormalised counter to drift.


class SourceIntelligence(BaseModel):
    """MonitoringSource inventory: the "MONITORING SOURCES active / total" KPI.

    `active` is measured from `MonitoringSource.is_active` (the platform's own
    enabled/disabled switch), NOT from `status` - a source that is enabled but
    currently erroring is still active inventory and must not vanish from the
    coverage denominator.
    """

    active: int  # is_active = True
    total: int  # every non-deleted source for the tenant


class CollectionIntelligence(BaseModel):
    """CollectorRun activity for the trailing window (KPI B / C / K / L).

    `status` vocabulary is the CollectorRun model's own and nothing else:
    running | success | partial | error | skipped. `partial` is counted as a
    SUCCESS for `health_percent`, matching the documented CollectorHealthService
    contract ("failed = run status error"); it is also reported separately so
    the distinction stays visible rather than being flattened.
    """

    window_hours: int
    runs_24h: int
    succeeded_24h: int
    partial_24h: int
    failed_24h: int
    skipped_24h: int
    running: int  # status == "running" among runs that STARTED inside the window
    #: None when runs_24h == 0 -> "NO COLLECTION". Never 0.0 (that reads as a
    #: total outage) and never 100.0 (that reads as perfect health).
    health_percent: float | None
    #: Coverage denominator: active sources that can be collected from.
    coverage_configured: int
    #: Active sources with at least one successful run inside the window.
    coverage_collected: int
    #: None when coverage_configured == 0 -> "NOT CONFIGURED". Never 100.0.
    coverage_percent: float | None


class FreshnessIntelligence(BaseModel):
    """Sensor data freshness (KPI D).

    The ONLY timestamp used is `Sensor.last_value_at`, which
    `SensorRepository.upsert()` sets to `now` on every persisted reading - the
    last time a real value reached the platform for that sensor. It is
    explicitly NOT a collector-run completion time and NOT `updated_at` (which
    also moves for non-value reasons such as a rename).

    `stale_after_seconds` is returned so the client states the definition the
    data is judged against instead of showing an unexplained percentage.
    """

    stale_after_seconds: int
    fresh: int
    stale: int
    never_collected: int  # last_value_at IS NULL
    total: int
    #: None when total == 0 -> "NO COLLECTION". Never 100.0.
    fresh_percent: float | None


class SensorIntelligence(BaseModel):
    """Canonical `Sensor.status` distribution (KPI E / F / G).

    Uses the four-value vocabulary PHASE 5C locked in on the persisted column
    (up | down | warning | unknown). No vendor status mapping is repeated here:
    PRTG's own codes were already normalized before the row was written, and
    PHASE 5C deliberately maps unrecognised/paused vendor states to "unknown"
    rather than promoting them to an alarm - so `unknown` is a real, meaningful
    bucket and not a dumping ground.
    """

    healthy: int  # status == "up"
    down: int
    warning: int
    unknown: int
    total: int


class DeviceIntelligence(BaseModel):
    """Canonical `Device.status` distribution (KPI H).

    Buckets deliberately SPLIT what the frontend's commandKpis.ts
    `DEGRADED_DEVICE_STATUS` lumps together: that set counts
    degraded+warning+unreachable+critical+error AND down+offline as a single
    "DEGRADED SYSTEMS" figure. Splitting them matters: "reachable but impaired"
    and "not reachable" are different incidents, and one combined number hides an
    outage behind a cosmetic warning. Consequence, so it is not mistaken for a
    bug: `degraded + offline` here will NOT equal the existing DEGRADED SYSTEMS
    card; reconciling them means changing that card's definition, a deliberate
    follow-up rather than something changed here.

    Buckets:
      online   status == "up"
      degraded status in {degraded, warning, unreachable, critical, error}
      offline  status in {down, offline}
      unknown  anything else, INCLUDING the model default "unknown"
    An unrecognised status is never counted as a problem.
    """

    online: int
    degraded: int
    offline: int
    unknown: int
    total: int


class OperationalAlertIntelligence(BaseModel):
    """Alert activity (KPI I / J + unacknowledged).

    Windowed counts use `Alert.triggered_at`; `auto_resolved_24h` uses
    `Alert.resolved_at` restricted to rows the platform itself resolved
    (`Alert.auto_resolved` is an authoritative persisted boolean, written by
    `AlertRepository.resolve(..., auto=True)`) - it is not inferred from a
    status string and not estimated.
    """

    last_24h: int
    unacknowledged: int  # status == "open" AND acknowledged_at IS NULL
    auto_resolved_24h: int


class AiTaskIntelligence(BaseModel):
    """AI Task lifecycle distribution (KPI M).

    Point-in-time counts over the existing `Task.status` lifecycle - NOT a 24h
    window, because the specification asks for the queued/running/completed
    state and `TaskRepository.count_by_status` already exposes exactly that
    grouping. The bucket mapping is documented on the service, and every status
    the lifecycle can produce is accounted for, so the buckets always sum to
    `total`.
    """

    queued: int  # created | assigned | reassigned
    running: int  # accepted | investigating | executing | verifying | retrying | escalated
    completed: int  # verified | recorded
    failed: int  # failed
    cancelled: int  # cancelled - terminal, but neither a success nor a failure
    total: int


class OperationalIntelligence(BaseModel):
    """The complete Operational Intelligence KPI payload for one tenant.

    KPI N (AI automation rate) is deliberately ABSENT: no authoritative
    definition of an "automated eligible task" exists in this codebase, and the
    specification forbids inventing one. This is recorded as a deferred item in
    docs/architecture/phase-6a-operational-intelligence.md rather than being
    filled with a fabricated ratio.
    """

    generated_at: datetime
    window_hours: int
    sources: SourceIntelligence
    collection: CollectionIntelligence
    freshness: FreshnessIntelligence
    sensors: SensorIntelligence
    devices: DeviceIntelligence
    alerts: OperationalAlertIntelligence
    ai: AiTaskIntelligence


# ---------------------------------------------------------------------------
# SPRINT P1 — Operational Intelligence Command Center brief.
#
# One additive payload (GET /dashboard/command-center-brief) answering
# "what needs my attention right now, why, and what should I do next" from the
# SAME persisted columns the rest of the platform reads. Every nullable field
# is nullable for a reason: a missing value renders as UNKNOWN /
# INSUFFICIENT_DATA / NOT_CONFIGURED in the UI, never as a fabricated number.
# ---------------------------------------------------------------------------

AttentionSeverity = Literal["critical", "high", "medium", "low", "unknown"]
#: `type` vocabulary — one value per supported backend condition.
AttentionType = Literal[
    "critical_incident",
    "high_incident",
    "critical_alert",
    "high_alert",
    "down_device",
    "stale_telemetry",
    "collection_failure",
    "discovery_failure",
    "security_event",
    "sla_breach",
    "failed_automation",
    "pending_approval",
]
CollectionStatus = Literal[
    "HEALTHY", "DEGRADED", "FAILED", "STALE", "UNKNOWN", "NOT_CONFIGURED",
]


class AttentionItem(BaseModel):
    """One real condition needing an operator, with the evidence available.

    `impact` / `recommended_next_step` are None when the backend has no
    authoritative source (the client renders INSUFFICIENT_DATA) — this service
    never invents impact, site, service, cause or advice.
    """

    id: str
    severity: AttentionSeverity
    type: AttentionType
    resource: str | None = None
    site: str | None = None
    reason: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    impact: str | None = None
    recommended_next_step: str | None = None
    #: Existing view this item drills into (always a real route).
    href: str | None = None


class AttentionGap(BaseModel):
    """A condition class the brief could not measure, and why (never faked)."""

    type: str
    reason: str


class AttentionBrief(BaseModel):
    items: list[AttentionItem]
    counts_by_severity: dict[str, int]
    total: int
    #: True when per-item lists hit their caps — counts stay exact (aggregates).
    truncated: bool = False
    gaps: list[AttentionGap] = []


class DeviceHealthBuckets(BaseModel):
    """Spec vocabulary for devices: HEALTHY / WARNING / CRITICAL / DOWN / UNKNOWN."""

    healthy: int
    warning: int
    critical: int
    down: int
    unknown: int
    total: int


class SensorHealthBuckets(BaseModel):
    """Spec vocabulary for sensors: adds STALE (freshness-derived, P0 rules)."""

    healthy: int
    warning: int
    critical: int
    down: int
    stale: int
    unknown: int
    total: int


class SourceHealthBuckets(BaseModel):
    """Monitoring sources: HEALTHY / DEGRADED / FAILED / UNKNOWN."""

    healthy: int
    degraded: int
    failed: int
    unknown: int
    total: int


class CollectorHealthBuckets(BaseModel):
    """Per-source collectors: HEALTHY / DEGRADED / FAILED / STALE / UNKNOWN (run-based).

    `degraded` is its own bucket rather than being folded into `unknown`: a
    source that delivered *some* runs in the window is a different operational
    state from one this platform cannot classify, and merging them would hide
    partially-broken collection behind an unexplained count.
    """

    healthy: int
    degraded: int
    failed: int
    stale: int
    unknown: int
    total: int


class InfrastructureHealthBrief(BaseModel):
    devices: DeviceHealthBuckets
    sensors: SensorHealthBuckets
    sources: SourceHealthBuckets
    collectors: CollectorHealthBuckets
    #: Returned so the UI can state the STALE definition it judged against.
    sensor_stale_after_seconds: int


class SourceCollectionStatus(BaseModel):
    """One source's COLLECTION state — distinct from infrastructure health.

    FAILED here means the pipeline did not deliver data; it does NOT claim
    the infrastructure behind the source is down.
    """

    source_id: str
    name: str
    provider: str
    collection_status: CollectionStatus
    last_collection: datetime | None = None
    #: None when no run happened in the window (never 0.0 / 100.0).
    success_rate_pct: float | None = None
    #: Average run duration in the window, ms. None when never measured.
    latency_ms: int | None = None
    error: str | None = None
    #: Devices attributed to this source. None when not attributable.
    affected_resources: int | None = None
    is_active: bool = True


class CollectionHealthBrief(BaseModel):
    collection_failure_count: int
    infrastructure_failure_count: int
    #: One sentence the UI prints verbatim so the distinction cannot be lost.
    distinction: str
    sources: list[SourceCollectionStatus]


class ResponseKpis(BaseModel):
    """KPIs the Operational Intelligence payload does not already carry.

    Every rate is None when its denominator/sample set does not exist —
    N/A, never a synthetic 0 or 100.
    """

    availability_pct: float | None
    mttr_minutes: float | None
    mttd_minutes: float | None
    incident_volume_24h: int
    open_incidents: int  # active = open + acknowledged + investigating
    resolved_incidents_24h: int
    critical_open_incidents: int


class TrendBucket(BaseModel):
    ts: datetime
    count: int


class TrendSeries(BaseModel):
    available: bool
    unit: str = "hour"
    reason: str | None = None
    buckets: list[TrendBucket] = []


class TrendsBrief(BaseModel):
    alerts: TrendSeries
    incidents: TrendSeries
    collection_failures: TrendSeries
    devices_down: TrendSeries
    stale_telemetry: TrendSeries


class CommandCenterBrief(BaseModel):
    """P1 command-center brief: attention, health, collection, KPIs, trends."""

    generated_at: datetime
    window_hours: int
    attention: AttentionBrief
    infrastructure_health: InfrastructureHealthBrief
    collection_health: CollectionHealthBrief
    kpis: ResponseKpis
    trends: TrendsBrief
