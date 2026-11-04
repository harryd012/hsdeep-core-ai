"""
Sensor library (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P3 slice 1).

A catalogue of sensor templates built from what the collectors really emit:
each template names the `metric_type` values it covers, the collectors that
produce them, the unit, which way is worse and - only where the platform
already defines one - default thresholds with where they come from. Nothing
here invents a measurement: a template no collector has reported for a tenant
simply shows zero sensors.

Three uses:
  * usage     - per template, the tenant's sensors by status and how many run
                on the user's own thresholds; metric types no template covers
                are listed as uncatalogued, never dropped.
  * apply     - set (or reset) user thresholds on every matching sensor, or on
                those of chosen hosts, through the P1 lifecycle
                (`SensorService.apply`), so status and alerts follow at once.
  * recommend - read-only coverage rules per host (no sensor at all, a server
                with no OS-level collector, a network device with no polling).
                Nothing is created: adding a check needs a source and, for
                most collectors, credentials.

PRTG sensors are one template: the connector stores PRTG's CPU load, memory
and disk free sensors as `prtg_cpu_load` / `prtg_memory_free` /
`prtg_disk_free` (from PRTG's own sensor type) and every other one as
`prtg_sensor`. Their thresholds stay PRTG's, so the template cannot apply
thresholds in bulk.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.health_thresholds import DEFAULT_THRESHOLDS
from app.collectors.connectivity import BOUND_DEVICE_KEY, bound_device_id
from app.core.config import get_settings
from app.models.device import Device
from app.models.interface import Interface
from app.models.monitoring_source import MonitoringSource
from app.models.sensor import Sensor
from app.schemas.monitoring import SensorThresholdsIn, SensorUpdate
from app.services.monitoring.sensor_service import SensorService

MAX_APPLY_SENSORS = 1000
MAX_APPLY_HOSTS = 200
MAX_RECOMMENDATION_HOSTS = 500
MAX_REACHABILITY_HOSTS = 200
REACHABILITY_CHECKS = ("icmp", "tcp")
MIN_POLL_SECONDS = 60
MAX_POLL_SECONDS = 86400

#: Collectors that measure a host's operating system.
OS_SOURCE_TYPES = frozenset({"linux_ssh", "windows_winrm", "wmi", "vmware", "zabbix", "datadog", "prtg"})
#: Collectors that poll a network device (sensors or interfaces).
NETWORK_SOURCE_TYPES = frozenset({"snmp", "prtg", "zabbix", "fortigate", "paloalto", "meraki", "fortimanager"})
SERVER_TYPES = frozenset({"server", "vm", "virtual_machine", "host"})
NETWORK_TYPES = frozenset({"switch", "router", "firewall", "access_point", "load_balancer"})


@dataclass(frozen=True)
class SensorTemplate:
    key: str
    name: str
    category: str
    description: str
    source_types: tuple[str, ...]
    metric_types: tuple[str, ...]
    unit: str | None
    #: above: higher is worse; below: lower is worse; None: a state sensor (1 / 0).
    direction: str | None

    @property
    def thresholds_applicable(self) -> bool:
        return self.direction is not None



def _t(key, name, category, description, source_types, metric_types, unit, direction) -> SensorTemplate:
    return SensorTemplate(key, name, category, description, tuple(source_types), tuple(metric_types), unit, direction)


TEMPLATES: tuple[SensorTemplate, ...] = (
    _t("latency", "Response time", "availability", "ICMP round-trip time to the host.",
       ["connectivity"], ["latency"], "ms", "above"),
    _t("packet_loss", "Packet loss", "availability", "Share of ICMP probes with no reply.",
       ["connectivity"], ["packet_loss"], "%", "above"),
    _t("reachability", "Reachability", "availability", "Host answers its ICMP / TCP / DNS checks (1) or not (0).",
       ["connectivity"], ["reachability"], None, None),
    _t("cpu_utilization", "CPU utilization", "system", "Processor busy time, per socket on Windows.",
       ["windows_winrm", "wmi", "zabbix"], ["cpu_usage", "cpu_utilization"], "%", "above"),
    _t("cpu_load", "CPU load average", "system", "Run-queue load average (1 minute).",
       ["linux_ssh", "zabbix"], ["load_avg_1m", "cpu_load"], None, "above"),
    _t("cpu_queue", "Processor queue length", "system", "Threads waiting for a processor (Windows).",
       ["windows_winrm", "wmi"], ["cpu_queue_length"], None, "above"),
    _t("memory_utilization", "Memory utilization", "system", "Share of physical memory in use.",
       ["linux_ssh", "windows_winrm", "wmi", "zabbix"], ["memory_usage", "memory_utilization"], "%", "above"),
    _t("disk_usage", "Disk usage", "system", "Share of a volume or mount in use, one sensor per volume.",
       ["linux_ssh", "windows_winrm", "wmi", "zabbix"], ["disk_usage", "disk_utilization"], "%", "above"),
    _t("uptime", "Uptime", "system", "Time since the last restart; a low value means a recent reboot.",
       ["windows_winrm", "wmi"], ["uptime_seconds"], "s", "below"),
    _t("process_cpu", "Process CPU", "system", "CPU of the ten busiest processes (Windows).",
       ["windows_winrm", "wmi"], ["process_cpu"], "%", "above"),
    _t("process_memory", "Process memory", "system", "Working set of the ten largest processes (Windows).",
       ["windows_winrm", "wmi"], ["process_memory_bytes"], "bytes", "above"),
    _t("service_status", "Windows service", "system", "Running state of each automatic-start service.",
       ["windows_winrm", "wmi"], ["service_status"], None, None),
    _t("network_throughput", "Network throughput", "network", "Inbound and outbound traffic per item.",
       ["zabbix"], ["network_in", "network_out"], None, "above"),
    _t("vm_cpu", "VM CPU", "virtualization", "CPU used by a virtual machine.",
       ["vmware"], ["cpu_usage_mhz"], "MHz", "above"),
    _t("vm_memory", "VM memory", "virtualization", "Guest memory used by a virtual machine.",
       ["vmware"], ["memory_usage_mb"], "MB", "above"),
    _t("temperature", "Temperature", "environment", "Environmental sensor temperature.",
       ["meraki", "zabbix"], ["environmental_temperature"], "C", "above"),
    _t("humidity", "Humidity", "environment", "Environmental sensor relative humidity.",
       ["meraki", "zabbix"], ["environmental_humidity"], "%", "above"),
    _t("firewall_sessions", "Firewall session table", "firewall", "Sessions in use against the device's limit.",
       ["fortigate", "paloalto", "snmp"], ["firewall_session_pct"], "%", "above"),
    _t("firewall_cpu", "Firewall CPU", "firewall", "Management or data-plane CPU the firewall reports.",
       ["fortigate", "paloalto", "snmp"], ["firewall_cpu_pct"], "%", "above"),
    _t("firewall_memory", "Firewall memory", "firewall", "Memory the firewall reports in use.",
       ["fortigate", "paloalto", "snmp"], ["firewall_memory_pct"], "%", "above"),
    _t("firewall_licence", "Firewall licence", "firewall", "Days until a licensed feature expires.",
       ["fortigate", "paloalto"], ["firewall_licence_days"], "days", "below"),
    _t("firewall_ha", "Firewall HA sync", "firewall", "High-availability peers in sync (1) or not (0).",
       ["fortigate", "paloalto", "snmp"], ["firewall_ha_sync"], None, None),
    _t("vpn_tunnel", "VPN tunnel", "firewall", "Tunnel up (1), partly up (0) or down.",
       ["fortigate", "paloalto"], ["firewall_vpn_tunnel"], None, None),
    _t("backup_age", "Backup age", "backup", "Hours since the item's last successful backup.",
       ["azure_backup", "veeam_backup"], ["backup_age_hours"], "h", "above"),
    _t("backup_free_space", "Backup storage free", "backup", "Free share of a fixed-size backup repository.",
       ["azure_backup", "veeam_backup"], ["backup_free_space_pct"], "%", "below"),
    _t("backup_result", "Last backup result", "backup", "Outcome of the item's most recent backup.",
       ["azure_backup", "veeam_backup"], ["backup_last_result"], None, None),
    _t("backup_protection", "Backup protection", "backup", "Whether the item is still protected.",
       ["azure_backup", "veeam_backup"], ["backup_protection"], None, None),
    _t("backup_soft_delete", "Vault soft delete", "backup", "Soft delete enabled on the vault.",
       ["azure_backup"], ["backup_soft_delete"], None, None),
    _t("replication_health", "Replication health", "backup", "Site Recovery replication health of a protected item.",
       ["azure_backup"], ["replication_health"], None, None),
    _t("prtg_sensor", "PRTG sensor", "vendor",
       "Any PRTG sensor. Its kind, unit and thresholds are defined in PRTG; channels are kept as history.",
       ["prtg"], ["prtg_sensor", "prtg_cpu_load", "prtg_memory_free", "prtg_disk_free"], None, None),
)
TEMPLATES_BY_KEY = {t.key: t for t in TEMPLATES}
TEMPLATE_BY_METRIC = {m: t for t in TEMPLATES for m in t.metric_types}


def default_thresholds(template: SensorTemplate) -> tuple[float | None, float | None, str | None]:
    """(warning, critical, origin) where the platform already defines a default; else Nones."""
    cfg = get_settings()
    match template.key:
        case "cpu_utilization":
            w, c = DEFAULT_THRESHOLDS["cpu"]
            return w, c, "Platform CPU health default"
        case "memory_utilization":
            w, c = DEFAULT_THRESHOLDS["memory"]
            return w, c, "Platform memory health default"
        case "firewall_sessions":
            return cfg.firewall_session_warning_pct, cfg.firewall_session_critical_pct, "Firewall settings"
        case "firewall_cpu" | "firewall_memory":
            return cfg.firewall_resource_warning_pct, cfg.firewall_resource_critical_pct, "Firewall settings"
        case "firewall_licence":
            return float(cfg.firewall_licence_warning_days), None, "Firewall settings"
        case "backup_age":
            return None, float(cfg.backup_rpo_hours), "Backup recovery-point objective"
        case "backup_free_space":
            return 15.0, 5.0, "Backup collector default"
    return None, None, None


def check_thresholds(t: SensorThresholdsIn) -> None:
    """ValueError unless at least one limit is set and they are in the direction's order."""
    if t.warning is None and t.critical is None:
        raise ValueError("Set a warning or a critical threshold")
    if t.warning is not None and t.critical is not None:
        if t.direction == "above" and t.warning > t.critical:
            raise ValueError("When higher is worse, warning must not be above critical")
        if t.direction == "below" and t.warning < t.critical:
            raise ValueError("When lower is worse, warning must not be below critical")


# ---------------------------------------------------------------------------
# Recommendations (pure)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    description: str
    #: What resolves it: always a configuration step outside this page.
    action: str


RULES: dict[str, Rule] = {
    r.key: r
    for r in (
        Rule("unmonitored", "Not monitored",
             "No sensor and no polled interface reports for this host.",
             "Add a reachability check (ICMP / TCP) for its address here, or cover it from a monitoring source."),
        Rule("server_without_os", "Server without OS metrics",
             "A server with no sensor from an operating-system collector (SSH, WinRM / WMI, VMware, Zabbix, Datadog, PRTG).",
             "Add a Linux (SSH) or Windows (WinRM) source with credentials for this host."),
        Rule("network_without_polling", "Network device not polled",
             "A switch, router or firewall with no interfaces and no sensor from a network collector.",
             "Add SNMP polling (or the vendor's API source) for this device."),
        Rule("unclassified", "Device type unknown",
             "Discovery could not tell what the host is, so no type-specific check can be suggested.",
             "Set its type, or let a credentialed probe (SNMP / SSH / WinRM) identify it."),
    )
}


def rules_for(device_type: str | None, sensor_sources: set[str], sensor_count: int, interface_count: int) -> list[str]:
    """Coverage rules that hold for one host. Pure."""
    kind = (device_type or "unknown").lower()
    out: list[str] = []
    if sensor_count == 0 and interface_count == 0:
        out.append("unmonitored")
    if kind in SERVER_TYPES and not (sensor_sources & OS_SOURCE_TYPES):
        out.append("server_without_os")
    if kind in NETWORK_TYPES and interface_count == 0 and not (sensor_sources & NETWORK_SOURCE_TYPES):
        out.append("network_without_polling")
    if kind in ("unknown", "other", ""):
        out.append("unclassified")
    return out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TemplateNotFound(Exception):
    pass


@dataclass
class ReachabilityPlan:
    """What a bulk reachability request would do, host by host."""

    to_create: list[Device]
    already_checked: list[uuid.UUID]
    no_address: list[uuid.UUID]
    not_found: list[uuid.UUID]


def check_reachability_request(check: str, port: int | None, poll_interval_seconds: int) -> None:
    """ValueError unless the check, port and interval are usable (pure)."""
    if check not in REACHABILITY_CHECKS:
        raise ValueError(f"check must be one of {', '.join(REACHABILITY_CHECKS)}")
    if check == "tcp" and not (port and 1 <= port <= 65535):
        raise ValueError("A TCP check needs a port between 1 and 65535")
    if check == "icmp" and port is not None:
        raise ValueError("An ICMP check takes no port")
    if not MIN_POLL_SECONDS <= poll_interval_seconds <= MAX_POLL_SECONDS:
        raise ValueError(f"poll interval must be between {MIN_POLL_SECONDS} and {MAX_POLL_SECONDS} seconds")


def check_source_name(device: Device, taken: set[str]) -> str:
    """A tenant-unique source name for the host's check (names are unique per tenant, deleted rows included)."""
    base = f"Reachability: {device.name}"[:200]
    name = f"{base} [{device.id.hex[:8]}]"
    n = 2
    while name in taken:
        name = f"{base} [{device.id.hex[:8]}-{n}]"
        n += 1
    taken.add(name)
    return name


class TooManySensors(Exception):
    pass


class SensorLibraryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def usage(self, tenant_id: uuid.UUID) -> tuple[dict[str, dict], list[dict]]:
        """Per template key: {total, by_status, user_thresholds, source_types}; plus uncatalogued metric types."""
        rows = (
            await self.db.execute(
                select(Sensor.metric_type, MonitoringSource.source_type, Sensor.status, Sensor.threshold_source, func.count())
                .join(MonitoringSource, MonitoringSource.id == Sensor.monitoring_source_id)
                .where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False))
                .group_by(Sensor.metric_type, MonitoringSource.source_type, Sensor.status, Sensor.threshold_source)
            )
        ).all()
        usage: dict[str, dict] = {}
        other: dict[str, dict] = {}
        for metric_type, source_type, status, threshold_source, n in rows:
            template = TEMPLATE_BY_METRIC.get(metric_type)
            if template is None:
                bucket = other.setdefault(metric_type, {"metric_type": metric_type, "total": 0, "source_types": set()})
            else:
                bucket = usage.setdefault(
                    template.key, {"total": 0, "by_status": defaultdict(int), "user_thresholds": 0, "source_types": set()}
                )
                bucket["by_status"][status] += n
                if threshold_source == "user":
                    bucket["user_thresholds"] += n
            bucket["total"] += n
            bucket["source_types"].add(source_type)
        uncatalogued = sorted(
            ({**o, "source_types": sorted(o["source_types"])} for o in other.values()), key=lambda o: -o["total"]
        )
        return usage, uncatalogued

    async def apply(
        self, tenant_id: uuid.UUID, key: str, thresholds: SensorThresholdsIn | None, device_ids: list[uuid.UUID] | None,
        *, dry_run: bool = False,
    ) -> tuple[list[tuple[Sensor, dict, dict]], int, list[uuid.UUID]]:
        """Set (`thresholds`) or reset (None) user thresholds on the template's sensors.

        Returns ([(sensor, old, new) per changed sensor], matched, device ids not found in the tenant).
        """
        template = TEMPLATES_BY_KEY.get(key)
        if template is None:
            raise TemplateNotFound()
        if not template.thresholds_applicable:
            raise ValueError(f"{template.name} sensors are state sensors or vendor-defined; thresholds cannot be applied")
        if thresholds is not None:
            check_thresholds(thresholds)

        not_found: list[uuid.UUID] = []
        query = select(Sensor).where(
            Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False), Sensor.metric_type.in_(template.metric_types)
        )
        if device_ids is not None:
            found = set(
                (
                    await self.db.execute(
                        select(Device.id).where(Device.tenant_id == tenant_id, Device.id.in_(device_ids), Device.is_deleted.is_(False))
                    )
                ).scalars()
            )
            not_found = [d for d in dict.fromkeys(device_ids) if d not in found]
            query = query.where(Sensor.device_id.in_(found or [uuid.UUID(int=0)]))
        count = (await self.db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        if count > MAX_APPLY_SENSORS:
            raise TooManySensors(count)
        if dry_run:
            return [], count, not_found

        sensors = (await self.db.execute(query.order_by(Sensor.id).with_for_update())).scalars().all()
        change = SensorUpdate(thresholds=thresholds) if thresholds is not None else SensorUpdate(reset_thresholds=True)
        service = SensorService(self.db)
        changed = []
        for sensor in sensors:
            old, new = await service.apply(sensor, change)
            if new:
                changed.append((sensor, old, new))
        return changed, len(sensors), not_found

    async def plan_reachability(self, tenant_id: uuid.UUID, device_ids: list[uuid.UUID]) -> ReachabilityPlan:
        """Sort the hosts: to check, already checked by a tied connectivity source, no address, not in the tenant."""
        wanted = list(dict.fromkeys(device_ids))
        devices = {
            d.id: d
            for d in (
                await self.db.execute(
                    select(Device).where(Device.tenant_id == tenant_id, Device.id.in_(wanted), Device.is_deleted.is_(False))
                )
            ).scalars()
        }
        checked = {
            bound_device_id(source_type, config)
            for source_type, config in (
                await self.db.execute(
                    select(MonitoringSource.source_type, MonitoringSource.config).where(
                        MonitoringSource.tenant_id == tenant_id,
                        MonitoringSource.source_type == "connectivity",
                        MonitoringSource.is_deleted.is_(False),
                    )
                )
            ).all()
        }
        plan = ReachabilityPlan([], [], [], [])
        for device_id in wanted:
            device = devices.get(device_id)
            if device is None:
                plan.not_found.append(device_id)
            elif device_id in checked:
                plan.already_checked.append(device_id)
            elif not (device.ip_address or device.hostname):
                plan.no_address.append(device_id)
            else:
                plan.to_create.append(device)
        return plan

    async def create_reachability(
        self, tenant_id: uuid.UUID, devices: list[Device], *, check: str, port: int | None, poll_interval_seconds: int,
    ) -> list[MonitoringSource]:
        """One connectivity source per host, tied to it; collected from the next scheduler pass."""
        from app.services.monitoring.monitoring_service import MonitoringService
        from app.repositories.monitoring_source_repository import MonitoringSourceRepository
        from app.repositories.sensor_repository import SensorRepository

        service = MonitoringService(source_repo=MonitoringSourceRepository(self.db), sensor_repo=SensorRepository(self.db))
        taken = set(
            (await self.db.execute(select(MonitoringSource.name).where(MonitoringSource.tenant_id == tenant_id))).scalars()
        )
        created = []
        for device in devices:
            created.append(await service.create_source(
                tenant_id=tenant_id,
                name=check_source_name(device, taken),
                source_type="connectivity",
                hostname=device.ip_address or device.hostname,
                port=port if check == "tcp" else None,
                site_id=device.site_id,
                is_active=True,
                poll_interval_seconds=poll_interval_seconds,
                config={"connectivity": {"checks": [check], BOUND_DEVICE_KEY: str(device.id)}},
            ))
        return created

    async def recommendations(self, tenant_id: uuid.UUID) -> list[dict]:
        """One entry per host with at least one rule, rules in RULES order."""
        sensor_rows = (
            await self.db.execute(
                select(Sensor.device_id, MonitoringSource.source_type, func.count())
                .join(MonitoringSource, MonitoringSource.id == Sensor.monitoring_source_id)
                .where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False), Sensor.device_id.is_not(None))
                .group_by(Sensor.device_id, MonitoringSource.source_type)
            )
        ).all()
        sources: dict[uuid.UUID, set[str]] = defaultdict(set)
        sensor_counts: dict[uuid.UUID, int] = defaultdict(int)
        for device_id, source_type, n in sensor_rows:
            sources[device_id].add(source_type)
            sensor_counts[device_id] += n
        interface_counts = dict(
            (
                await self.db.execute(
                    select(Interface.device_id, func.count())
                    .join(Device, Device.id == Interface.device_id)
                    .where(Device.tenant_id == tenant_id, Interface.is_deleted.is_(False))
                    .group_by(Interface.device_id)
                )
            ).all()
        )
        devices = (
            await self.db.execute(
                select(Device.id, Device.name, Device.device_type, Device.ip_address, Device.hostname, Device.vendor)
                .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
                .order_by(Device.name)
            )
        ).all()
        order = list(RULES)
        out = []
        for d in devices:
            rules = rules_for(d.device_type, sources.get(d.id, set()), sensor_counts.get(d.id, 0), interface_counts.get(d.id, 0))
            if rules:
                out.append({
                    "device_id": d.id, "name": d.name, "device_type": d.device_type, "address": d.ip_address or d.hostname,
                    "vendor": d.vendor, "sensors": sensor_counts.get(d.id, 0), "interfaces": interface_counts.get(d.id, 0),
                    "rules": sorted(rules, key=order.index),
                })
        return out


__all__ = [
    "MAX_APPLY_HOSTS", "MAX_APPLY_SENSORS", "MAX_REACHABILITY_HOSTS", "MAX_RECOMMENDATION_HOSTS", "REACHABILITY_CHECKS",
    "RULES", "ReachabilityPlan", "SensorLibraryService", "check_reachability_request", "check_source_name",
    "SensorTemplate", "TEMPLATES", "TEMPLATES_BY_KEY", "TemplateNotFound", "TooManySensors", "check_thresholds",
    "default_thresholds", "rules_for",
]
