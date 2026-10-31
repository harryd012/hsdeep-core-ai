"""
Firewall gateway persistence (docs/architecture/firewall-gateway.md).

Called by `BundleSaver.save()` for bundles that carry firewall tables
(`NormalizedBundle.firewall_status`). It:

  - upserts one `firewall_status` row per firewall, overwriting a table's
    columns only when that table was read this poll ("collected"/"empty"), so
    a failed or unsupported read never wipes what an earlier poll stored;
  - syncs `vpn_tunnels`, retiring tunnels a successful read no longer lists;
  - turns what the device reported into sensor readings (HA sync, session
    load, CPU, memory, licence expiry, VPN tunnels). Alerts then open and
    auto-resolve through the existing sensor path (AlertService.evaluate_sensor)
    instead of a second alerting mechanism.

Nothing is inferred: a value the device did not report produces no reading,
and a tunnel that has never been up is stored but never alerts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import (
    NormalizedBundle,
    NormalizedFirewallStatus,
    NormalizedLicence,
    NormalizedSensorReading,
)
from app.core.config import get_settings
from app.models.firewall import FirewallStatus, VpnTunnel

_READ_OK = ("collected", "empty")
#: Prefix of every sensor external_id this module produces.
SENSOR_PREFIX = "fw:"

_STATUS_FIELDS = (
    "ha_mode", "ha_role", "ha_sync", "ha_peer_serial", "session_count", "session_limit",
    "cpu_percent", "memory_percent", "firmware_version",
)


@dataclass
class FirewallSaveResult:
    firewalls_updated: int = 0
    tunnels_upserted: int = 0
    tunnels_retired: int = 0
    readings: list[NormalizedSensorReading] = field(default_factory=list)


def licence_state(lic: NormalizedLicence, now: datetime, warning_days: int) -> tuple[str, float | None]:
    """(valid | expiring | expired | unknown, days left or None)."""
    days = (lic.expires_at - now).total_seconds() / 86400 if lic.expires_at else None
    if lic.expired is True or (days is not None and days <= 0):
        return "expired", days
    if days is not None and days <= warning_days:
        return "expiring", days
    if days is not None or lic.expired is False:
        return "valid", days
    return "unknown", None


def _threshold_status(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "down"
    if value >= warning:
        return "warning"
    return "up"


def status_readings(fw: NormalizedFirewallStatus, device_name: str) -> list[NormalizedSensorReading]:
    """Sensor readings for one firewall's reported status. Pure."""
    cfg = get_settings()
    ext = fw.device_external_id
    out: list[NormalizedSensorReading] = []

    def add(key: str, label: str, metric: str, value: float | None, status: str, unit: str | None = None,
            warning: float | None = None, critical: float | None = None) -> None:
        out.append(NormalizedSensorReading(
            external_id=f"{SENSOR_PREFIX}{ext}:{key}", name=f"{device_name} {label}", metric_type=metric,
            value=value, unit=unit, status=status, warning_threshold=warning, critical_threshold=critical,
            device_external_id=ext,
        ))

    if fw.ha_sync is not None and fw.ha_mode not in (None, "standalone"):
        in_sync = fw.ha_sync == "in_sync"
        add("ha_sync", "HA sync", "firewall_ha_sync", 1.0 if in_sync else 0.0, "up" if in_sync else "down")
    if fw.session_count is not None and fw.session_limit:
        pct = round(fw.session_count / fw.session_limit * 100, 1)
        add("sessions", "session table", "firewall_session_pct", pct,
            _threshold_status(pct, cfg.firewall_session_warning_pct, cfg.firewall_session_critical_pct),
            "%", cfg.firewall_session_warning_pct, cfg.firewall_session_critical_pct)
    for key, label, value in (("cpu", "CPU", fw.cpu_percent), ("memory", "memory", fw.memory_percent)):
        if value is not None:
            add(key, label, f"firewall_{key}_pct", round(value, 1),
                _threshold_status(value, cfg.firewall_resource_warning_pct, cfg.firewall_resource_critical_pct),
                "%", cfg.firewall_resource_warning_pct, cfg.firewall_resource_critical_pct)
    return out


def licence_readings(licences: list[NormalizedLicence], device_name: str, now: datetime) -> list[NormalizedSensorReading]:
    warning_days = get_settings().firewall_licence_warning_days
    out: list[NormalizedSensorReading] = []
    for lic in licences:
        state, days = licence_state(lic, now, warning_days)
        if state == "unknown":
            continue
        out.append(NormalizedSensorReading(
            external_id=f"{SENSOR_PREFIX}{lic.device_external_id}:licence:{lic.feature}",
            name=f"{device_name} licence {lic.feature}", metric_type="firewall_licence_days",
            value=round(days, 1) if days is not None else None, unit="days",
            status={"expired": "down", "expiring": "warning"}.get(state, "up"),
            warning_threshold=float(warning_days), device_external_id=lic.device_external_id,
        ))
    return out


def _tunnel_reading(device_ext: str, device_name: str, name: str, status: str) -> NormalizedSensorReading:
    return NormalizedSensorReading(
        external_id=f"{SENSOR_PREFIX}{device_ext}:vpn:{name}", name=f"{device_name} VPN {name}",
        metric_type="firewall_vpn_tunnel", value=1.0 if status == "up" else 0.0 if status == "warning" else None,
        status=status, device_external_id=device_ext,
    )


async def save_firewall(
    db: AsyncSession,
    *,
    tenant_id,
    source_id,
    bundle: NormalizedBundle,
    device_id_by_external: dict[str, object],
    now: datetime,
) -> FirewallSaveResult:
    result = FirewallSaveResult()
    tables = bundle.firewall_status
    if not tables:
        return result

    names = {d.external_id: d.name for d in bundle.devices}
    status_by_dev = {fw.device_external_id: fw for fw in bundle.firewalls}
    licences_by_dev: dict[str, list[NormalizedLicence]] = {}
    for lic in bundle.licences:
        licences_by_dev.setdefault(lic.device_external_id, []).append(lic)
    tunnels_by_dev: dict[str, list] = {}
    for t in bundle.vpn_tunnels:
        tunnels_by_dev.setdefault(t.device_external_id, []).append(t)

    candidates = {d.external_id for d in bundle.devices if d.device_type == "firewall"}
    candidates |= set(status_by_dev) | set(licences_by_dev) | set(tunnels_by_dev)
    warning_days = get_settings().firewall_licence_warning_days

    for ext in sorted(candidates):
        device_id = device_id_by_external.get(ext)
        if device_id is None:
            continue
        name = names.get(ext, ext)

        row = (await db.execute(select(FirewallStatus).where(FirewallStatus.device_id == device_id))).scalar_one_or_none()
        if row is None:
            row = FirewallStatus(tenant_id=tenant_id, device_id=device_id, table_status={})
            db.add(row)
        row.monitoring_source_id = source_id
        row.table_status = dict(tables)

        if tables.get("status") in _READ_OK:
            fw = status_by_dev.get(ext) or NormalizedFirewallStatus(device_external_id=ext)
            for f in _STATUS_FIELDS:
                setattr(row, f, getattr(fw, f))
            row.observed_at = now
            result.readings.extend(status_readings(fw, name))

        if tables.get("licences") in _READ_OK:
            lics = licences_by_dev.get(ext, [])
            row.licences = [
                {
                    "feature": lic.feature,
                    "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
                    "status": licence_state(lic, now, warning_days)[0],
                }
                for lic in lics
            ]
            result.readings.extend(licence_readings(lics, name, now))

        if tables.get("vpn") in _READ_OK:
            await _sync_tunnels(db, tenant_id, device_id, ext, name, tunnels_by_dev.get(ext, []), now, result)

        result.firewalls_updated += 1

    await db.flush()
    return result


async def _sync_tunnels(db, tenant_id, device_id, ext, device_name, tunnels, now, result) -> None:
    existing = {
        t.name: t
        for t in (await db.execute(select(VpnTunnel).where(VpnTunnel.device_id == device_id))).scalars()
    }
    seen: set[str] = set()
    for t in tunnels:
        if t.name in seen:
            continue
        seen.add(t.name)
        row = existing.get(t.name)
        if row is None:
            row = VpnTunnel(tenant_id=tenant_id, device_id=device_id, name=t.name, ever_up=False, status=t.status)
            db.add(row)
            row.last_change_at = now
        elif row.status != t.status:
            row.last_change_at = now
        row.status = t.status
        row.tunnel_type = t.tunnel_type
        row.remote_gateway = t.remote_gateway
        row.phase2_up, row.phase2_total = t.phase2_up, t.phase2_total
        row.bytes_in, row.bytes_out = t.bytes_in, t.bytes_out
        row.last_seen_at = now
        if t.status == "up":
            row.ever_up = True
        result.tunnels_upserted += 1
        # A tunnel that has never come up (idle dial-up) never alerts; one that
        # was up and is now down is a warning.
        if row.ever_up:
            result.readings.append(
                _tunnel_reading(ext, device_name, t.name, "up" if t.status == "up" else "warning")
            )

    gone = [row for name, row in existing.items() if name not in seen]
    for row in gone:
        if row.ever_up:
            # Removed from the device: resolve any alert it had open.
            result.readings.append(_tunnel_reading(ext, device_name, row.name, "unknown"))
    for row in gone:
        await db.delete(row)
    result.tunnels_retired += len(gone)
