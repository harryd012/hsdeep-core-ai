"""
Canonical Infrastructure Overview — the single aggregation authority.

WHY THIS EXISTS
---------------
Dashboard, Infrastructure, Topology and Reports each computed their own
totals, so the same tenant could be told it has 9 devices on one page and a
different number on another. This service is the one place those numbers are
derived; every page should consume it rather than re-counting.

HONESTY RULES (enforced here, not in the UI)
--------------------------------------------
* No derived signal is invented. Interfaces and topology edges come straight
  from their repositories; when there are no rows the answer is 0 / `has_edges:
  false`, and the API additionally reports `not_collected` markers.
* Geography is never inferred. A Site is only placed on the map when it has
  real `latitude`/`longitude`. Private IPs (10/8, 172.16/12, 192.168/16) are
  never geocoded - sites without coordinates are returned separately as
  "location unavailable".
* Raw provider status vocabularies are returned alongside the bucketed counts
  (`devices.by_status`), so a consumer can always see what the buckets were
  built from instead of trusting an opaque total.

STATUS VOCABULARY NORMALIZATION
-------------------------------
The live database stores BOTH "up" and "online" for a healthy device (found by
inspecting the real dev DB: 131 `up`, 93 `online`). `schemas/site.py`'s
SiteSummary documented the stored values as only up|down|warning|unknown and
counted `status == "up"` as online - so every `online` device was invisible to
site rollups. The buckets below mirror `models/device_types.normalize_device_type`'s
philosophy: map what is unambiguous, and put anything unrecognised in
`unknown` rather than force-fitting it into a real category.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_types import normalize_device_type
from app.models.site import Site
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository

HEALTH_HEALTHY = "healthy"
HEALTH_WARNING = "warning"
HEALTH_CRITICAL = "critical"
HEALTH_UNKNOWN = "unknown"

#: Device status spellings that mean the device is serving traffic.
DEVICE_STATUS_HEALTHY: frozenset[str] = frozenset({
    "up", "online", "active", "ok", "enabled", "reachable", "succeeded",
})
#: Device status spellings that mean degraded-but-reachable.
DEVICE_STATUS_WARNING: frozenset[str] = frozenset({
    "warning", "degraded", "alerting", "maintenance", "warn", "partial",
})
#: Device status spellings that mean the device is not serving.
DEVICE_STATUS_CRITICAL: frozenset[str] = frozenset({
    "down", "offline", "critical", "failed", "error", "unreachable",
    "unhealthy", "disabled",
})

SENSOR_STATUS_HEALTHY: frozenset[str] = frozenset({"up", "online", "ok"})
SENSOR_STATUS_WARNING: frozenset[str] = frozenset({"warning", "degraded"})
SENSOR_STATUS_DOWN: frozenset[str] = frozenset({"down", "offline", "critical", "failed"})

#: Alert statuses that mean the alert is still actionable.
ACTIVE_ALERT_STATUSES: frozenset[str] = frozenset({"open", "acknowledged"})
#: Incident statuses that mean the incident is still open.
TERMINAL_INCIDENT_STATUSES: frozenset[str] = frozenset({
    "closed", "archived", "resolved", "recovered",
})

#: Emitted when a metric genuinely has no rows behind it.
NOT_COLLECTED = "not_collected"

#: Emitted when a relationship graph has no evidence rows.
NOT_DISCOVERED = "not_discovered"

#: Bucket key used when a stored status is NULL.
UNKNOWN_STATUS = "unknown"


def _bucket(
    status: Any,
    healthy: frozenset[str],
    warning: frozenset[str],
    critical: frozenset[str],
) -> str:
    """Map a stored status onto healthy/warning/critical/unknown.

    Unrecognised values (including None and "") are `unknown` - never counted
    as healthy, and never silently invented as critical.
    """
    if status is None:
        return HEALTH_UNKNOWN
    key = str(status).strip().lower()
    if key in healthy:
        return HEALTH_HEALTHY
    if key in warning:
        return HEALTH_WARNING
    if key in critical:
        return HEALTH_CRITICAL
    return HEALTH_UNKNOWN


def device_health_bucket(status: Any) -> str:
    return _bucket(status, DEVICE_STATUS_HEALTHY, DEVICE_STATUS_WARNING,
                   DEVICE_STATUS_CRITICAL)


def sensor_health_bucket(status: Any) -> str:
    return _bucket(status, SENSOR_STATUS_HEALTHY, SENSOR_STATUS_WARNING,
                   SENSOR_STATUS_DOWN)


@dataclass
class InfrastructureHealth:
    """The single health contract every page must agree on."""

    total_devices: int = 0
    healthy_devices: int = 0
    warning_devices: int = 0
    critical_devices: int = 0
    unknown_devices: int = 0

    total_sites: int = 0
    healthy_sites: int = 0
    warning_sites: int = 0
    critical_sites: int = 0
    unknown_sites: int = 0

    total_sensors: int = 0
    healthy_sensors: int = 0
    warning_sensors: int = 0
    down_sensors: int = 0
    unknown_sensors: int = 0

    total_alerts: int = 0
    active_alerts: int = 0
    critical_alerts: int = 0
    warning_alerts: int = 0
    info_alerts: int = 0

    total_incidents: int = 0
    open_incidents: int = 0

    total_collector_runs: int = 0
    successful_collector_runs: int = 0
    failed_collector_runs: int = 0

    total_relationships: int = 0
    total_interfaces: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class InfrastructureOverviewService:
    """Builds the canonical overview using existing repositories only.

    Per the codebase convention (`infrastructure_service.py`,
    `dashboard_service.py`) this class issues no SQL of its own - every number
    comes from a repository method that already existed, plus the additive
    `InterfaceRepository.count_for_tenant`.
    """

    #: Bounded device fetch - same convention/magnitude as
    #: InfrastructureService._DEVICE_FETCH_LIMIT, so a large tenant's devices
    #: are bucketed in Python without an unbounded query.
    _DEVICE_FETCH_LIMIT = 100_000

    def __init__(self, db: AsyncSession):
        self.db = db
        self.devices = DeviceRepository(db)
        self.sites = SiteRepository(db)
        self.alerts = AlertRepository(db)
        self.sensors = SensorRepository(db)
        self.incidents = IncidentRepository(db)
        self.collector_runs = CollectorRunRepository(db)
        self.relationships = ResourceRelationshipRepository(db)
        self.monitoring_sources = MonitoringSourceRepository(db)
        self.interfaces = InterfaceRepository(db)

    async def build(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        sites = await self.sites.list(tenant_id)
        devices = await self.devices.list(tenant_id, limit=self._DEVICE_FETCH_LIMIT)

        health = InfrastructureHealth()
        device_rows = self._device_block(devices, health)
        site_rows, geography = self._site_block(sites, devices, health)
        sensor_block = await self._sensor_block(tenant_id, health)
        alert_block = await self._alert_block(tenant_id, health)
        incident_block = await self._incident_block(tenant_id, health)
        collector_block = await self._collector_block(tenant_id, health)
        topology = await self._topology_block(tenant_id, devices, sites)
        source_block = await self._monitoring_source_block(tenant_id)

        health.total_interfaces = await self.interfaces.count_for_tenant(tenant_id)

        return {
            "tenant_id": str(tenant_id),
            "health": health.as_dict(),
            "sites": site_rows,
            "devices": device_rows,
            "sensors": sensor_block,
            "alerts": alert_block,
            "incidents": incident_block,
            "collector_runs": collector_block,
            "monitoring_sources": source_block,
            "topology": topology,
            "geography": geography,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    # -- devices ---------------------------------------------------------------

    def _device_block(
        self, devices: list[Device], health: InfrastructureHealth
    ) -> dict[str, Any]:
        """Bucket every device, and expose the raw stored vocabulary.

        Synchronous like `_site_block`: it performs no I/O, so callers are not
        forced to await a pure computation.
        """
        by_status: dict[str, int] = {}
        by_type_raw: dict[str, int] = {}
        by_type_canonical: dict[str, int] = {}
        unassigned = 0

        for device in devices:
            status = device.status
            key = str(status).strip().lower() if status else UNKNOWN_STATUS
            by_status[key] = by_status.get(key, 0) + 1

            raw_type = device.device_type
            raw_key = str(raw_type).strip().lower() if raw_type else UNKNOWN_STATUS
            by_type_raw[raw_key] = by_type_raw.get(raw_key, 0) + 1

            canonical = normalize_device_type(raw_type)
            by_type_canonical[canonical] = by_type_canonical.get(canonical, 0) + 1

            if device.site_id is None:
                unassigned += 1

            bucket = device_health_bucket(status)
            if bucket == HEALTH_HEALTHY:
                health.healthy_devices += 1
            elif bucket == HEALTH_WARNING:
                health.warning_devices += 1
            elif bucket == HEALTH_CRITICAL:
                health.critical_devices += 1
            else:
                health.unknown_devices += 1

        health.total_devices = len(devices)
        return {
            "total": len(devices),
            # Raw vocabulary as stored, so the bucketed totals are auditable.
            "by_status": dict(sorted(by_status.items())),
            # Canonical types power the UI filter chips (1:1 with
            # CANONICAL_DEVICE_TYPES); raw types are kept for provenance.
            "by_type": dict(sorted(by_type_canonical.items())),
            "by_type_raw": dict(sorted(by_type_raw.items())),
            "unassigned_to_site": unassigned,
        }

    # -- sites -----------------------------------------------------------------

    def _site_block(
        self,
        sites: list[Site],
        devices: list[Device],
        health: InfrastructureHealth,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Per-site rollup + map data. Coordinates are never derived."""
        site_devices: dict[uuid.UUID, list[Device]] = {}
        for device in devices:
            if device.site_id is not None:
                site_devices.setdefault(device.site_id, []).append(device)

        rows: list[dict[str, Any]] = []
        mapped: list[dict[str, Any]] = []
        unmapped = 0

        for site in sites:
            members = site_devices.get(site.id, [])
            counts = {HEALTH_HEALTHY: 0, HEALTH_WARNING: 0, HEALTH_CRITICAL: 0,
                      HEALTH_UNKNOWN: 0}
            for device in members:
                counts[device_health_bucket(device.status)] += 1

            if not members:
                # A site with no devices has no evidence of health. Calling it
                # healthy would be inventing a signal.
                bucket = HEALTH_UNKNOWN
                health.unknown_sites += 1
            elif counts[HEALTH_CRITICAL]:
                bucket = HEALTH_CRITICAL
                health.critical_sites += 1
            elif counts[HEALTH_WARNING] or counts[HEALTH_UNKNOWN]:
                bucket = HEALTH_WARNING
                health.warning_sites += 1
            else:
                bucket = HEALTH_HEALTHY
                health.healthy_sites += 1

            has_coords = site.latitude is not None and site.longitude is not None
            if has_coords:
                mapped.append({
                    "site_id": str(site.id),
                    "name": site.name,
                    "code": site.code,
                    "latitude": site.latitude,
                    "longitude": site.longitude,
                    "health": bucket,
                    "devices": len(members),
                    "city": site.city,
                    "country": site.country,
                    "region": site.region,
                    "site_type": site.site_type,
                })
            else:
                unmapped += 1

            rows.append({
                "site_id": str(site.id),
                "name": site.name,
                "code": site.code,
                "address": site.address,
                "city": site.city,
                "country": site.country,
                "region": site.region,
                "site_type": site.site_type,
                "criticality": site.criticality,
                "health": bucket,
                "devices_total": len(members),
                "devices_healthy": counts[HEALTH_HEALTHY],
                "devices_warning": counts[HEALTH_WARNING],
                "devices_critical": counts[HEALTH_CRITICAL],
                "devices_unknown": counts[HEALTH_UNKNOWN],
                "latitude": site.latitude,
                "longitude": site.longitude,
                "has_coordinates": has_coords,
            })

        health.total_sites = len(sites)
        geography = {
            # Only real coordinates ever land in `mapped`.
            "mapped": mapped,
            "sites_with_coordinates": len(mapped),
            "sites_without_coordinates": unmapped,
            # Explicit: the map must render "Location unavailable" for these
            # rather than geocoding a private IP.
            "location_unavailable": unmapped > 0,
        }
        return rows, geography

    # -- sensors ---------------------------------------------------------------

    async def _sensor_block(
        self, tenant_id: uuid.UUID, health: InfrastructureHealth
    ) -> dict[str, Any]:
        sensors = await self.sensors.list_for_tenant(
            tenant_id, limit=self._DEVICE_FETCH_LIMIT
        )
        by_status: dict[str, int] = {}
        for sensor in sensors:
            key = str(sensor.status).strip().lower() if sensor.status else UNKNOWN_STATUS
            by_status[key] = by_status.get(key, 0) + 1

            bucket = sensor_health_bucket(sensor.status)
            if bucket == HEALTH_HEALTHY:
                health.healthy_sensors += 1
            elif bucket == HEALTH_WARNING:
                health.warning_sensors += 1
            elif bucket == HEALTH_CRITICAL:
                health.down_sensors += 1
            else:
                health.unknown_sensors += 1

        health.total_sensors = len(sensors)
        block: dict[str, Any] = {
            "total": len(sensors),
            "by_status": dict(sorted(by_status.items())),
            "healthy": health.healthy_sensors,
            "warning": health.warning_sensors,
            "down": health.down_sensors,
            # Not in the original spec's list, but 10 of the live tenant's 34
            # sensors are `unknown` - omitting the bucket would make the parts
            # not sum to the total and hide a real data gap.
            "unknown": health.unknown_sensors,
        }
        if not sensors:
            block["status"] = NOT_COLLECTED
        return block

    # -- alerts ----------------------------------------------------------------

    async def _alert_block(
        self, tenant_id: uuid.UUID, health: InfrastructureHealth
    ) -> dict[str, Any]:
        """All-time and active alert counts, from the existing summary().

        Both definitions are published deliberately: the live UI shows 314
        critical alerts (all-time severity) while operationally only 15 are
        still open. Publishing both, named, stops two pages from quoting
        different "critical" totals without either being wrong.
        """
        summary = await self.alerts.summary(tenant_id)
        by_severity = summary.get("by_severity", {})
        by_status = summary.get("by_status", {})

        active = sum(
            by_status.get(status, 0) for status in ACTIVE_ALERT_STATUSES
        )
        active_critical = 0
        for status in ACTIVE_ALERT_STATUSES:
            active_critical += await self.alerts.count(
                tenant_id, severity="critical", status=status
            )

        health.total_alerts = summary.get("total", 0)
        health.active_alerts = active
        health.critical_alerts = by_severity.get("critical", 0)
        health.warning_alerts = by_severity.get("warning", 0)
        health.info_alerts = by_severity.get("info", 0)

        return {
            "total": health.total_alerts,
            # "active" = open + acknowledged (the statuses that still need a human).
            "active": active,
            "active_critical": active_critical,
            # All-time severity mix, matching what the Alerts page lists.
            "critical": health.critical_alerts,
            "warning": health.warning_alerts,
            "info": health.info_alerts,
            "by_severity": by_severity,
            "by_status": by_status,
            #: Definition note so no consumer has to guess.
            "active_definition": "status in (open, acknowledged)",
        }

    # -- incidents -------------------------------------------------------------

    async def _incident_block(
        self, tenant_id: uuid.UUID, health: InfrastructureHealth
    ) -> dict[str, Any]:
        total = await self.incidents.count(tenant_id)
        open_count = await self.incidents.count(tenant_id, status="open")
        health.total_incidents = total
        health.open_incidents = open_count

        block: dict[str, Any] = {"total": total, "open": open_count}
        if total and open_count == total:
            # Surfaced as a data-quality signal: the live dev tenant has
            # 2.3M incidents all in status 'open', which no human triaged.
            block["note"] = (
                "every incident is in status 'open'; this usually means "
                "automated test data rather than real triage backlog"
            )
        return block

    # -- collector runs --------------------------------------------------------

    #: Declared windows. Every rate is published per window so a page can never
    #: quote a success rate without saying which time window it covers.
    _RUN_WINDOWS: tuple[tuple[str, timedelta], ...] = (
        ("last_24_hours", timedelta(hours=24)),
        ("last_7_days", timedelta(days=7)),
        ("last_30_days", timedelta(days=30)),
    )

    async def _window_counts(
        self, tenant_id: uuid.UUID, since: datetime
    ) -> dict[str, Any]:
        counts = {}
        for status in ("success", "partial", "error", "running", "skipped"):
            counts[status] = await self.collector_runs.count(
                tenant_id, status=status, since=since
            )
        counts["total"] = await self.collector_runs.count(tenant_id, since=since)
        # "attempted" deliberately excludes running (not finished) and skipped
        # (never started) so neither can be scored as a failure.
        attempted = counts["success"] + counts["partial"] + counts["error"]
        counts["attempted"] = attempted
        counts["success_rate"] = (
            round(counts["success"] / attempted * 100, 1) if attempted else None
        )
        return counts

    async def _collector_block(
        self, tenant_id: uuid.UUID, health: InfrastructureHealth
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        historical = await self._window_counts(tenant_id, now - timedelta(days=3650))
        health.total_collector_runs = historical["total"]
        health.successful_collector_runs = historical["success"]
        health.failed_collector_runs = historical["error"]

        windows = {}
        for label, delta in self._RUN_WINDOWS:
            windows[label] = await self._window_counts(tenant_id, now - delta)

        recent = await self.collector_runs.list(tenant_id, limit=10)
        source_names = {
            source.id: (source.name, source.source_type)
            for source in await self.monitoring_sources.list(tenant_id)
        }

        recent_rows = []
        for run in recent:
            name, source_type = source_names.get(
                run.monitoring_source_id, (None, None)
            )
            duration = None
            if run.finished_at is not None and run.started_at is not None:
                duration = round(
                    (run.finished_at - run.started_at).total_seconds(), 2
                )
            recent_rows.append({
                "run_id": str(run.id),
                "collector": name,
                "source_type": source_type,
                "monitoring_source_id": str(run.monitoring_source_id),
                "trigger": run.trigger,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "duration_seconds": duration,
                "devices": run.devices_upserted,
                "sensors": run.sensors_upserted,
                "interfaces": run.interfaces_upserted,
                "alerts": run.alerts_upserted,
                # Error text is provider-reported, not user HTML; it is returned
                # raw so an operator sees the real cause (e.g. a connection
                # refusal), and the UI renders it as text, never as markup.
                "error": run.error,
            })

        recent_failed = sum(
            1 for run in recent if run.status in ("error", "partial")
        )
        block: dict[str, Any] = {
            # Historical lifetime figures (what the "82% success" tile shows).
            "historical": historical,
            # Time-windowed figures, to be shown separately from historical.
            "windows": windows,
            "recent_runs": recent_rows,
            "recent_sample_size": len(recent),
            "recent_failed": recent_failed,
            "recent_all_failed": bool(recent) and recent_failed == len(recent),
            #: Explicit so the UI can render "Historical 82% vs last 10 runs: 10
            #: failed" as two different facts rather than a contradiction.
            "note": (
                "historical figures cover all recorded runs; recent_runs covers "
                "only the latest sample and is not comparable without its window"
            ),
        }
        if not recent:
            block["status"] = NOT_COLLECTED
        return block

    # -- topology --------------------------------------------------------------

    async def _topology_block(
        self,
        tenant_id: uuid.UUID,
        devices: list[Device],
        sites: list[Site],
    ) -> dict[str, Any]:
        """Node/edge counts from real rows only.

        `resource_relationships` is the existing edge model. When it has no
        rows the overview says so explicitly (`status: not_discovered`) instead
        of returning an empty graph the UI would render as a blank canvas with
        no explanation.
        """
        edge_summary = await self.relationships.summary(tenant_id)
        edges_total = await self.relationships.count(tenant_id)

        nodes_by_type: dict[str, int] = {}
        for device in devices:
            key = normalize_device_type(device.device_type)
            nodes_by_type[key] = nodes_by_type.get(key, 0) + 1

        sources = await self.monitoring_sources.list(tenant_id)

        block: dict[str, Any] = {
            "nodes_total": len(devices) + len(sites) + len(sources),
            "edges_total": edges_total,
            "has_edges": edges_total > 0,
            "nodes": {
                "devices": len(devices),
                "sites": len(sites),
                "monitoring_sources": len(sources),
            },
            "nodes_by_type": dict(sorted(nodes_by_type.items())),
            "edges_by_type": edge_summary.get("by_type", []),
            # Provenance of each edge class, so an inferred edge is never shown
            # as though it were directly observed.
            "discovered_by": edge_summary.get("by_discovered_by", {}),
            "is_inferred": edge_summary.get("is_inferred", 0),
        }
        if edges_total == 0:
            block["status"] = NOT_DISCOVERED
            block["note"] = (
                "no relationship evidence recorded for this tenant; topology "
                "renders nodes only until a collector observes an adjacency"
            )
        return block

    # -- monitoring sources ----------------------------------------------------

    async def _monitoring_source_block(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Per-source health, with the collector's own evidence taking precedence.

        Phase 9 rule: a source must never be shown healthy when its latest
        collector run failed or its consecutive-failure counter is non-zero.
        The stored `health_status` is still returned (`stored_health_status`)
        so the divergence is visible rather than silently corrected.
        """
        sources = await self.monitoring_sources.list(tenant_id)
        rows: list[dict[str, Any]] = []

        for source in sources:
            latest = await self.collector_runs.list(
                tenant_id, monitoring_source_id=source.id, limit=1
            )
            last_run = latest[0] if latest else None

            failures = source.consecutive_failures or 0
            last_failed = source.last_failed_run_at
            last_ok = source.last_successful_run_at
            failed_after_success = bool(
                last_failed and (last_ok is None or last_failed > last_ok)
            )
            latest_failed = bool(
                last_run and last_run.status in ("error", "partial")
            )

            if latest_failed or failed_after_success or failures > 0:
                effective = "failing"
            elif last_run is not None and last_run.status == "success":
                effective = "healthy"
            else:
                effective = UNKNOWN_STATUS

            duration = None
            if last_run is not None and last_run.finished_at and last_run.started_at:
                duration = round(
                    (last_run.finished_at - last_run.started_at).total_seconds(), 2
                )

            rows.append({
                "id": str(source.id),
                "name": source.name,
                "source_type": source.source_type,
                "vendor": source.vendor,
                "is_active": source.is_active,
                "site_id": str(source.site_id) if source.site_id else None,
                "site_assigned": source.site_id is not None,
                #: As stored on the row.
                "stored_health_status": source.health_status,
                "stored_status": source.status,
                #: Derived, with collector evidence taking precedence.
                "effective_health": effective,
                "last_sync_at": (
                    source.last_sync_at.isoformat() if source.last_sync_at else None
                ),
                "last_successful_run_at": (
                    source.last_successful_run_at.isoformat()
                    if source.last_successful_run_at else None
                ),
                "last_failed_run_at": (
                    source.last_failed_run_at.isoformat()
                    if source.last_failed_run_at else None
                ),
                "next_run_at": (
                    source.next_run_at.isoformat() if source.next_run_at else None
                ),
                "poll_interval_seconds": source.poll_interval_seconds,
                "consecutive_failures": failures,
                "consecutive_successes": source.consecutive_successes or 0,
                "last_error": source.last_error,
                "latest_run": None if last_run is None else {
                    "run_id": str(last_run.id),
                    "status": last_run.status,
                    "started_at": (
                        last_run.started_at.isoformat() if last_run.started_at else None
                    ),
                    "duration_seconds": duration,
                    "devices": last_run.devices_upserted,
                    "sensors": last_run.sensors_upserted,
                    "error": last_run.error,
                },
            })

        return {
            "total": len(rows),
            "active": sum(1 for row in rows if row["is_active"]),
            "failing": sum(1 for row in rows if row["effective_health"] == "failing"),
            "items": rows,
        }
