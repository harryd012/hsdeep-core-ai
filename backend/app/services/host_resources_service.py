"""
Server fleet resources (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P5).

One row per server that reports host resources (CPU, memory, disk volumes,
load, uptime) through its sensors, so an operator sees the whole Windows /
Linux / VMware estate at once and which servers are running hot or filling up.

Built only from what collectors reported:

  * A sensor's resource comes from its metric type through the existing
    canonical vocabulary (``canonical_metric_name``): ``cpu_usage`` ->
    ``cpu_utilization_pct`` and so on. A sensor whose type is not mapped (a
    generic PRTG check, an SSL check) is not a host resource and is ignored.
  * The platform is the collector that reported the sensors (``linux_ssh`` ->
    linux, ``windows_winrm`` / ``wmi`` -> windows, ``vmware`` -> vmware), else the
    device's OS string, else "other". It is never guessed from the name.
  * Every value carries its sensor's own status (its thresholds decide it), and
    a percentage is only compared as a percentage when its unit says "%".
    VMware's MHz / MB readings are shown as reported, not turned into %.
  * PRTG's memory and disk sensors report what is free: a free percentage
    becomes used (100 - free, exact for a share of capacity) and keeps the
    reported free value; a free amount in GB ... is shown as free.
  * A server whose newest reading is older than ``STALE_AFTER`` is "stale":
    its values are the last ones received, not current.

Tenant-scoped reads only; nothing is written.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.models.sensor import Sensor
from app.services.infrastructure_intelligence.telemetry import canonical_metric_name

PLATFORMS = ("windows", "linux", "vmware", "other")
STALE_AFTER = timedelta(hours=1)
MAX_HOSTS = 2000

#: Canonical metric (or the collector's own type where the vocabulary has no
#: canonical name yet) -> host resource.
RESOURCE_OF_METRIC: dict[str, str] = {
    "cpu_utilization_pct": "cpu",
    "cpu_usage_mhz": "cpu",
    "memory_used_pct": "memory",
    "memory_usage": "memory",
    "memory_usage_mb": "memory",
    "disk_used_pct": "disk",
    "cpu_load_1m": "load",
    "load_avg_1m": "load",
    "uptime_seconds": "uptime",
    # PRTG host-resource sensors (app/collectors/prtg.py, from PRTG's own sensor type).
    "prtg_cpu_load": "cpu",
    "prtg_memory_free": "memory",
    "prtg_disk_free": "disk",
}

#: Metric types whose value is what is FREE, not used (PRTG's memory / disk
#: sensors). A free percentage is shown as used (100 - free); a free amount in
#: another unit (GB ...) stays as reported and is labelled free.
FREE_METRICS = frozenset({"prtg_memory_free", "prtg_disk_free"})

_PLATFORM_OF_SOURCE = {"linux_ssh": "linux", "windows_winrm": "windows", "wmi": "windows", "vmware": "vmware"}
#: Worst first, for picking the reading that speaks for a resource.
_STATUS_RANK = {"down": 0, "warning": 1, "unknown": 2, "up": 3, "paused": 4, "disabled": 5}


def resource_of(metric_type: str | None) -> str | None:
    """The host resource a sensor measures, or None when it is not one."""
    if not metric_type:
        return None
    return RESOURCE_OF_METRIC.get(canonical_metric_name(metric_type)) or RESOURCE_OF_METRIC.get(metric_type.lower())


def platform_of(source_types: set[str], os_version: str | None) -> str:
    for source_type in sorted(source_types):
        if source_type in _PLATFORM_OF_SOURCE:
            return _PLATFORM_OF_SOURCE[source_type]
    os_text = (os_version or "").lower()
    if "windows" in os_text:
        return "windows"
    if any(k in os_text for k in ("linux", "ubuntu", "debian", "centos", "red hat", "rhel", "suse", "alma", "rocky")):
        return "linux"
    if "esxi" in os_text or "vmware" in os_text:
        return "vmware"
    return "other"


def is_percent(unit: str | None) -> bool:
    return (unit or "").strip() == "%"


@dataclass
class Reading:
    sensor_id: uuid.UUID
    name: str
    value: float | None
    unit: str | None
    status: str
    recorded_at: datetime | None
    #: "used", or "free" for a free amount that could not be turned into used.
    measure: str = "used"
    #: The free percentage the sensor reported, when `value` was derived from it.
    reported_free: float | None = None


@dataclass
class ServerRow:
    device_id: uuid.UUID
    name: str
    ip_address: str | None
    platform: str
    os_version: str | None
    device_status: str
    cpu: Reading | None = None
    memory: Reading | None = None
    #: The fullest volume (highest %), with how many volumes the host reports.
    disk: Reading | None = None
    disks: int = 0
    load: Reading | None = None
    uptime: Reading | None = None
    last_reading_at: datetime | None = None
    stale: bool = False
    #: Sensors on this host in down / warning (any resource).
    critical: int = 0
    warning: int = 0
    #: Highest of the CPU / memory / disk percentages (sort key; None when no % is known).
    peak_pct: float | None = None


@dataclass
class FleetSummary:
    servers: int = 0
    by_platform: dict[str, int] = field(default_factory=lambda: {p: 0 for p in PLATFORMS})
    critical: int = 0
    warning: int = 0
    stale: int = 0
    #: Servers whose fullest volume is at or above 90 % / CPU at or above 90 %.
    disk_over_90: int = 0
    cpu_over_90: int = 0


def _reading(s: Sensor) -> Reading:
    reading = Reading(
        sensor_id=s.id, name=s.name, value=s.current_value, unit=s.unit, status=s.status, recorded_at=s.last_value_at,
    )
    if s.metric_type in FREE_METRICS:
        if is_percent(s.unit) and s.current_value is not None:
            reading.reported_free = s.current_value
            reading.value = round(100.0 - s.current_value, 2)
        elif not is_percent(s.unit):
            reading.measure = "free"
    return reading


def _pick(current: Reading | None, s: Sensor, *, prefer_high: bool) -> Reading:
    """The reading that speaks for a resource: the worst status, then (for
    percentages) the highest value."""
    candidate = _reading(s)
    if current is None:
        return candidate
    key = lambda r: (  # noqa: E731
        _STATUS_RANK.get(r.status, 2),
        -(r.value if (prefer_high and r.value is not None and is_percent(r.unit) and r.measure == "used") else float("-inf")),
    )
    return candidate if key(candidate) < key(current) else current


def build_rows(devices: list[Device], sensors: list[Sensor], source_types: dict[uuid.UUID, str], now: datetime) -> list[ServerRow]:
    """Pure assembly of the fleet rows (separated from I/O for testing)."""
    by_device: dict[uuid.UUID, list[Sensor]] = {}
    for s in sensors:
        if s.device_id is not None and resource_of(s.metric_type) is not None:
            by_device.setdefault(s.device_id, []).append(s)

    rows: list[ServerRow] = []
    for d in devices:
        host_sensors = by_device.get(d.id)
        if not host_sensors:
            continue
        row = ServerRow(
            device_id=d.id, name=d.name, ip_address=d.ip_address, os_version=d.os_version, device_status=d.status,
            platform=platform_of({source_types[s.monitoring_source_id] for s in host_sensors if s.monitoring_source_id in source_types}, d.os_version),
        )
        for s in host_sensors:
            kind = resource_of(s.metric_type)
            if kind == "disk":
                row.disks += 1
            # Per-core CPU, several volumes ...: the worst one speaks for the resource.
            setattr(row, kind, _pick(getattr(row, kind), s, prefer_high=kind in ("cpu", "memory", "disk")))
            if s.status == "down":
                row.critical += 1
            elif s.status == "warning":
                row.warning += 1
            if s.last_value_at is not None and (row.last_reading_at is None or s.last_value_at > row.last_reading_at):
                row.last_reading_at = s.last_value_at
        row.stale = row.last_reading_at is None or now - row.last_reading_at > STALE_AFTER
        pcts = [r.value for r in (row.cpu, row.memory, row.disk) if _used_pct(r) is not None]
        row.peak_pct = max(pcts) if pcts else None
        rows.append(row)

    # Worst first: hosts with critical sensors, then warnings, then fullest.
    rows.sort(key=lambda r: (-r.critical, -r.warning, -(r.peak_pct if r.peak_pct is not None else -1), r.name.lower()))
    return rows


def _used_pct(r: Reading | None) -> float | None:
    """The reading as a used percentage, or None when it is not one."""
    if r is None or r.value is None or r.measure != "used" or not is_percent(r.unit):
        return None
    return r.value


def summarize(rows: list[ServerRow]) -> FleetSummary:
    summary = FleetSummary(servers=len(rows))
    for r in rows:
        summary.by_platform[r.platform] = summary.by_platform.get(r.platform, 0) + 1
        summary.critical += r.critical > 0
        summary.warning += r.critical == 0 and r.warning > 0
        summary.stale += r.stale
        summary.disk_over_90 += (_used_pct(r.disk) or 0) >= 90
        summary.cpu_over_90 += (_used_pct(r.cpu) or 0) >= 90
    return summary


class HostResourcesService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def fleet(
        self, tenant_id: uuid.UUID, *, platform: str | None = None, now: datetime | None = None,
    ) -> tuple[FleetSummary, list[ServerRow]]:
        """The summary covers the whole fleet; `platform` filters the rows only."""
        now = now or datetime.now(timezone.utc)
        sensors = list(
            (
                await self.db.execute(
                    select(Sensor).where(
                        Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False), Sensor.device_id.is_not(None),
                    )
                )
            ).scalars()
        )
        sensors = [s for s in sensors if resource_of(s.metric_type) is not None]
        device_ids = {s.device_id for s in sensors}
        if not device_ids:
            return FleetSummary(), []
        devices = list(
            (
                await self.db.execute(
                    select(Device)
                    .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False), Device.id.in_(device_ids))
                    .limit(MAX_HOSTS)
                )
            ).scalars()
        )
        source_types = dict(
            (
                await self.db.execute(
                    select(MonitoringSource.id, MonitoringSource.source_type).where(
                        MonitoringSource.tenant_id == tenant_id,
                        MonitoringSource.id.in_({s.monitoring_source_id for s in sensors}),
                    )
                )
            ).all()
        )
        rows = build_rows(devices, sensors, source_types, now)
        summary = summarize(rows)
        if platform is not None:
            rows = [r for r in rows if r.platform == platform]
        return summary, rows


__all__ = [
    "FREE_METRICS", "PLATFORMS", "RESOURCE_OF_METRIC", "STALE_AFTER", "FleetSummary", "HostResourcesService", "Reading", "ServerRow",
    "build_rows", "is_percent", "platform_of", "resource_of", "summarize",
]
