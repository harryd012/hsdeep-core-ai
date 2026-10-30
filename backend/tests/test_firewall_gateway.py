"""Firewall gateway: persistence, honest gaps, and alerting through sensors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.collectors.base import (
    NormalizedBundle,
    NormalizedDevice,
    NormalizedFirewallStatus,
    NormalizedLicence,
    NormalizedVpnTunnel,
)
from app.models.alert import Alert
from app.models.firewall import FirewallStatus, VpnTunnel
from app.models.sensor import Sensor
from app.repositories.alert_repository import AlertRepository
from app.services.alert_service import AlertService
from app.services.monitoring.firewall_saver import licence_state

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)
FULL = {"status": "collected", "vpn": "collected", "licences": "collected"}


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db),
    )


def _bundle(*, status=None, tunnels=(), licences=(), tables=FULL):
    return NormalizedBundle(
        devices=[NormalizedDevice(external_id="fw1", name="edge-fw", device_type="firewall", status="up")],
        firewalls=[status] if status else [],
        vpn_tunnels=list(tunnels),
        licences=list(licences),
        firewall_status=dict(tables),
    )


HEALTHY = NormalizedFirewallStatus(
    device_external_id="fw1", ha_mode="active-passive", ha_role="active", ha_sync="in_sync",
    session_count=1000, session_limit=10000, cpu_percent=20.0, memory_percent=40.0, firmware_version="7.4.3",
)


def _tunnel(status: str, name="to-branch"):
    return NormalizedVpnTunnel(device_external_id="fw1", name=name, status=status, remote_gateway="198.51.100.7")


async def _sensors(db, tenant_id) -> dict[str, Sensor]:
    rows = (await db.execute(select(Sensor).where(Sensor.tenant_id == tenant_id))).scalars()
    return {s.external_id: s for s in rows}


def _name(alert: Alert) -> str:
    """Sensor name part of an alert title ("<Metric> — <sensor name>")."""
    return alert.title.split(" — ", 1)[-1]


async def _evaluate(db, result) -> list[Alert]:
    service = AlertService(AlertRepository(db))
    out = []
    for sensor in result.changed_sensors:
        evaluation = await service.evaluate_sensor(sensor)
        if evaluation.alert is not None:
            out.append(evaluation.alert)
    return out


async def test_status_is_stored_and_becomes_healthy_sensors(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="fortigate")
    result = await _saver(db).save(source, _bundle(status=HEALTHY))
    await db.commit()
    assert result.firewalls_updated == 1

    row = (await db.execute(select(FirewallStatus).where(FirewallStatus.tenant_id == source.tenant_id))).scalar_one()
    assert (row.ha_mode, row.ha_sync, row.session_count, row.firmware_version) == ("active-passive", "in_sync", 1000, "7.4.3")
    assert row.observed_at is not None and row.table_status == FULL

    sensors = await _sensors(db, source.tenant_id)
    assert set(sensors) == {"fw:fw1:ha_sync", "fw:fw1:sessions", "fw:fw1:cpu", "fw:fw1:memory"}
    assert {s.status for s in sensors.values()} == {"up"}
    assert all(s.device_id == row.device_id for s in sensors.values())
    assert await _evaluate(db, result) == []


async def test_unreported_values_produce_no_reading(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="fortigate")
    bare = NormalizedFirewallStatus(device_external_id="fw1", session_count=500)  # no limit, no HA
    await _saver(db).save(source, _bundle(status=bare))
    await db.commit()
    assert await _sensors(db, source.tenant_id) == {}
    row = (await db.execute(select(FirewallStatus).where(FirewallStatus.tenant_id == source.tenant_id))).scalar_one()
    assert (row.ha_mode, row.session_limit, row.cpu_percent) == (None, None, None)


async def test_ha_out_of_sync_raises_a_critical_alert_that_auto_resolves(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="fortigate")
    saver = _saver(db)
    await saver.save(source, _bundle(status=HEALTHY))
    broken = NormalizedFirewallStatus(**{**HEALTHY.__dict__, "ha_sync": "out_of_sync", "cpu_percent": 96.0})
    result = await saver.save(source, _bundle(status=broken))
    alerts = await _evaluate(db, result)
    await db.commit()
    assert sorted((_name(a), a.severity, a.device_id is not None) for a in alerts) == [
        ("edge-fw CPU", "critical", True), ("edge-fw HA sync", "critical", True),
    ]

    result = await saver.save(source, _bundle(status=HEALTHY))
    resolved = await _evaluate(db, result)
    await db.commit()
    assert len(resolved) == 2 and {a.status for a in resolved} == {"resolved"}


async def test_a_failed_read_keeps_what_was_stored(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="fortigate")
    saver = _saver(db)
    await saver.save(source, _bundle(status=HEALTHY, tunnels=[_tunnel("up")]))
    result = await saver.save(source, _bundle(tables={"status": "failed", "vpn": "failed", "licences": "unavailable"}))
    await db.commit()
    assert result.vpn_tunnels_retired == 0 and result.changed_sensors == []
    row = (await db.execute(select(FirewallStatus).where(FirewallStatus.tenant_id == source.tenant_id))).scalar_one()
    assert row.ha_sync == "in_sync" and row.table_status["status"] == "failed"
    assert [t.name for t in (await db.execute(select(VpnTunnel).where(VpnTunnel.tenant_id == source.tenant_id))).scalars()] == ["to-branch"]


async def test_tunnel_alerts_only_after_it_has_been_up(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="fortigate")
    saver = _saver(db)

    # Never up: stored, no sensor, no alert.
    result = await saver.save(source, _bundle(tunnels=[_tunnel("down")]))
    assert await _evaluate(db, result) == []
    assert "fw:fw1:vpn:to-branch" not in await _sensors(db, source.tenant_id)

    await saver.save(source, _bundle(tunnels=[_tunnel("up")]))
    result = await saver.save(source, _bundle(tunnels=[_tunnel("down")]))
    [alert] = await _evaluate(db, result)
    assert (alert.severity, _name(alert)) == ("warning", "edge-fw VPN to-branch")

    # Removed from the device: the tunnel row goes and the alert resolves.
    result = await saver.save(source, _bundle(tunnels=[]))
    [resolved] = await _evaluate(db, result)
    await db.commit()
    assert result.vpn_tunnels_retired == 1 and resolved.status == "resolved"
    assert (await db.execute(select(VpnTunnel).where(VpnTunnel.tenant_id == source.tenant_id))).first() is None


async def test_licences_expiring_and_expired(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="paloalto")
    lics = [
        NormalizedLicence(device_external_id="fw1", feature="Threat Prevention", expires_at=NOW + timedelta(days=10)),
        NormalizedLicence(device_external_id="fw1", feature="URL Filtering", expires_at=NOW - timedelta(days=1)),
        NormalizedLicence(device_external_id="fw1", feature="Support", expires_at=NOW + timedelta(days=300)),
        NormalizedLicence(device_external_id="fw1", feature="PAN-DB"),  # no date, no verdict
    ]
    result = await _saver(db).save(source, _bundle(licences=lics))
    alerts = await _evaluate(db, result)
    await db.commit()
    assert sorted((_name(a), a.severity) for a in alerts) == [
        ("edge-fw licence Threat Prevention", "warning"), ("edge-fw licence URL Filtering", "critical"),
    ]
    row = (await db.execute(select(FirewallStatus).where(FirewallStatus.tenant_id == source.tenant_id))).scalar_one()
    assert {lic["feature"]: lic["status"] for lic in row.licences} == {
        "Threat Prevention": "expiring", "URL Filtering": "expired", "Support": "valid", "PAN-DB": "unknown",
    }


async def test_licence_state_trusts_the_device_verdict():
    assert licence_state(NormalizedLicence("fw1", "x", expired=True), NOW, 30)[0] == "expired"
    assert licence_state(NormalizedLicence("fw1", "x", expired=False), NOW, 30)[0] == "valid"
    assert licence_state(NormalizedLicence("fw1", "x"), NOW, 30) == ("unknown", None)


async def test_bundles_without_firewall_tables_are_untouched(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="fortigate")
    result = await _saver(db).save(source, _bundle(status=HEALTHY, tables={}))
    await db.commit()
    assert result.firewalls_updated == 0
    assert (await db.execute(select(FirewallStatus).where(FirewallStatus.tenant_id == source.tenant_id))).first() is None
