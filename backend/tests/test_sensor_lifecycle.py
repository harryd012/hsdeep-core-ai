"""Sensor lifecycle, user thresholds, channels and history (observability plan P1).

Pure policy rules; the saver honouring pause / disable / user thresholds and
recording each sensor's own reading and named channels as history; the sensor
detail, history and update API with RBAC, audit and tenant isolation; and the
metric retention purge.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedMetric, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.metric import Metric
from app.models.sensor import Sensor
from app.repositories.alert_repository import AlertRepository
from app.services.alert_service import AlertService
from app.services.monitoring.sensor_policy import UserThresholds, effective_status, threshold_status

NOW = datetime.now(timezone.utc).replace(microsecond=0)


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


def _bundle(cpu: float = 42.0, cpu_status: str = "up") -> NormalizedBundle:
    return NormalizedBundle(
        sensors=[
            NormalizedSensorReading(external_id="s-cpu", name="CPU load", metric_type="cpu", value=cpu, unit="%",
                                    status=cpu_status, warning_threshold=80.0, critical_threshold=95.0),
            NormalizedSensorReading(external_id="s-mem", name="Memory", metric_type="memory", value=None, status="unknown"),
        ],
        metrics=[
            NormalizedMetric(metric_type="prtg_channel", value=61.0, recorded_at=NOW, unit="%", sensor_external_id="s-cpu", channel="Core 1"),
            NormalizedMetric(metric_type="prtg_channel", value=23.0, recorded_at=NOW, unit="%", sensor_external_id="s-cpu", channel="Core 2"),
        ],
    )


async def _sensor(db, source, external_id="s-cpu") -> Sensor:
    return (
        await db.execute(select(Sensor).where(Sensor.monitoring_source_id == source.id, Sensor.external_id == external_id))
    ).scalar_one()


def _client(tenant_id, permissions=frozenset({"monitoring.read"}), user_id: uuid.UUID | None = None):
    token = create_access_token(
        user_id=user_id or uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
        is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# Policy (pure)
# ---------------------------------------------------------------------------


def test_threshold_status_both_directions_and_missing_value():
    above = UserThresholds(warning=80, critical=95, direction="above")
    assert [threshold_status(v, above, "up") for v in (50, 80, 96)] == ["up", "warning", "down"]
    below = UserThresholds(warning=20, critical=5, direction="below")
    assert [threshold_status(v, below, "up") for v in (50, 20, 4)] == ["up", "warning", "down"]
    assert threshold_status(None, above, "down") == "down"  # no value: the collector's verdict stands
    assert threshold_status(99, UserThresholds(None, None, "above"), "down") == "up"


def test_effective_status_lifecycle_wins():
    t = UserThresholds(warning=1, critical=2, direction="above")
    assert effective_status("down", 5, "paused", t) == "paused"
    assert effective_status("down", 5, "disabled", None) == "disabled"
    assert effective_status("warning", 5, "active", None) == "warning"
    assert effective_status("up", 5, "active", t) == "down"


# ---------------------------------------------------------------------------
# Saver
# ---------------------------------------------------------------------------


async def test_saver_records_primary_reading_and_named_channels(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="prtg")
    await _saver(db).save(source, _bundle())
    await db.commit()
    cpu = await _sensor(db, source)
    rows = (await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars().all()
    channels = sorted((r.dimensions or {}).get("channel") for r in rows)
    assert channels == ["Core 1", "Core 2", "primary"]
    primary = next(r for r in rows if r.dimensions["channel"] == "primary")
    assert (primary.value, primary.metric_type, primary.unit) == (42.0, "cpu", "%")
    mem = await _sensor(db, source, "s-mem")
    assert (await db.execute(select(Metric).where(Metric.sensor_id == mem.id))).scalars().all() == []  # no value, no point


async def test_saver_uses_collector_series_as_primary_without_duplicating(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="prtg")
    bundle = NormalizedBundle(
        sensors=[NormalizedSensorReading(external_id="s-cpu", name="CPU load", metric_type="cpu", value=42.0, unit="%", status="up")],
        metrics=[NormalizedMetric(metric_type="cpu", value=42.0, recorded_at=NOW, unit="%", sensor_external_id="s-cpu")],
    )
    result = await _saver(db).save(source, bundle)
    await db.commit()
    cpu = await _sensor(db, source)
    rows = (await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars().all()
    assert [(r.value, r.dimensions["channel"]) for r in rows] == [(42.0, "primary")]
    assert result.metrics_recorded == 1


async def test_saver_honours_pause_disable_and_user_thresholds(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="prtg")
    saver = _saver(db)
    await saver.save(source, _bundle())
    await db.commit()
    cpu = await _sensor(db, source)

    # User thresholds: 40 % is already critical for this user, and survive the collector's 80/95.
    cpu.threshold_source, cpu.warning_threshold, cpu.critical_threshold, cpu.threshold_direction = "user", 30.0, 40.0, "above"
    await db.commit()
    result = await saver.save(source, _bundle(cpu=42.0))
    await db.commit()
    await db.refresh(cpu)
    assert (cpu.status, cpu.warning_threshold, cpu.critical_threshold) == ("down", 30.0, 40.0)
    assert cpu in result.changed_sensors

    # Paused: values still recorded, status paused, the change resolves the open alert.
    cpu.admin_state = "paused"
    await db.commit()
    result = await saver.save(source, _bundle(cpu=43.0))
    await db.commit()
    await db.refresh(cpu)
    assert (cpu.status, cpu.current_value) == ("paused", 43.0)

    # Disabled: the collector's reading and channels are ignored.
    cpu.admin_state = "disabled"
    cpu.status = "disabled"
    await db.commit()
    before = len((await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars().all())
    await saver.save(source, _bundle(cpu=99.0, cpu_status="down"))
    await db.commit()
    await db.refresh(cpu)
    assert (cpu.status, cpu.current_value) == ("disabled", 43.0)
    assert len((await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars().all()) == before


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded_subscription")
async def test_sensor_detail_history_update_rbac_audit_and_isolation(db, monitoring_source_factory, tenant):
    from app.models.knowledge_audit import AuditEvent

    source = await monitoring_source_factory(source_type="prtg")
    saver = _saver(db)
    # Three polls an hour apart give the primary channel three history points.
    for hours, value in ((2, 30.0), (1, 50.0), (0, 70.0)):
        bundle = _bundle(cpu=value)
        await saver.save(source, bundle)
        await db.commit()
        cpu = await _sensor(db, source)
        if hours:
            for row in (await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars():
                if row.recorded_at > NOW - timedelta(minutes=5) and (row.dimensions or {}).get("channel") == "primary" and row.value == value:
                    row.recorded_at = NOW - timedelta(hours=hours)
            await db.commit()
    cpu = await _sensor(db, source)

    async with _client(source.tenant_id) as client:
        d = (await client.get(f"/api/monitoring/sensors/{cpu.id}")).json()
        assert (d["name"], d["admin_state"], d["threshold_source"], d["source_type"]) == ("CPU load", "active", "provider", "prtg")
        keys = [c["key"] for c in d["channels"]]
        assert keys[0] == "primary" and set(keys) == {"primary", "Core 1", "Core 2"}
        primary = d["channels"][0]
        assert (primary["last_value"], primary["unit"], primary["samples_24h"]) == (70.0, "%", 3)

        h = (await client.get(f"/api/monitoring/sensors/{cpu.id}/history", params={"hours": 24})).json()
        assert h["bucket_seconds"] == 288 and h["unit"] == "%"
        assert [p["avg"] for p in h["points"]] == [30.0, 50.0, 70.0]
        core = (await client.get(f"/api/monitoring/sensors/{cpu.id}/history", params={"channel": "Core 1"})).json()
        assert core["points"] and all(p["count"] >= 1 for p in core["points"])
        assert (await client.get(f"/api/monitoring/sensors/{cpu.id}/history", params={"hours": 721})).status_code == 422
        assert (await client.get(f"/api/monitoring/sensors/{uuid.uuid4()}")).status_code == 404
        # monitoring.read alone cannot change a sensor.
        assert (await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"admin_state": "paused"})).status_code == 403

    writer = frozenset({"monitoring.read", "monitoring.update"})
    async with _client(source.tenant_id, permissions=writer) as client:
        assert (await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"admin_state": "sleeping"})).status_code == 422
        bad = await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"thresholds": {"warning": 1, "critical": 2, "direction": "sideways"}})
        assert bad.status_code == 422

        # A user threshold below the current 70 % opens a critical alert at once.
        body = (await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"thresholds": {"warning": 50, "critical": 60, "direction": "above"}})).json()
        assert (body["status"], body["threshold_source"], body["critical_threshold"]) == ("down", "user", 60.0)
        assert body["open_alert"]["severity"] == "critical"

        # Pausing resolves it; resuming waits for the next reading.
        body = (await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"admin_state": "paused", "description": "Maintenance on host"})).json()
        assert (body["status"], body["admin_state"], body["description"], body["open_alert"]) == ("paused", "paused", "Maintenance on host", None)
        body = (await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"admin_state": "active", "reset_thresholds": True})).json()
        assert (body["status"], body["threshold_source"]) == ("unknown", "provider")

    alerts = (await db.execute(select(Alert).where(Alert.sensor_id == cpu.id))).scalars().all()
    assert [a.status for a in alerts] == ["resolved"]
    events = (
        await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == source.tenant_id, AuditEvent.action == "sensor.updated").order_by(AuditEvent.created_at))
    ).scalars().all()
    assert len(events) == 3
    assert events[0].new_values["threshold_source"] == "user" and events[1].new_values["admin_state"] == "paused"

    # Another tenant sees nothing.
    other = uuid.uuid4()
    async with _client(other, permissions=writer) as client:
        assert (await client.get(f"/api/monitoring/sensors/{cpu.id}")).status_code == 404
        assert (await client.get(f"/api/monitoring/sensors/{cpu.id}/history")).status_code == 404
        assert (await client.patch(f"/api/monitoring/sensors/{cpu.id}", json={"admin_state": "paused"})).status_code in (402, 404)


async def test_metric_retention_purges_only_old_rows(db, monitoring_source_factory):
    from app.scheduler.metric_jobs import purge_old_metrics

    source = await monitoring_source_factory(source_type="prtg")
    await _saver(db).save(source, _bundle())
    await db.commit()
    cpu = await _sensor(db, source)
    old = (await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars().first()
    old.recorded_at = NOW - timedelta(days=40)
    await db.commit()
    assert await purge_old_metrics(db, days=0) == 0  # off by default
    deleted = await purge_old_metrics(db, days=30)
    assert deleted >= 1
    left = (await db.execute(select(Metric).where(Metric.sensor_id == cpu.id))).scalars().all()
    assert len(left) == 2 and all(r.recorded_at > NOW - timedelta(days=30) for r in left)


async def test_alert_engine_ignores_paused_sensor(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="prtg")
    await _saver(db).save(source, _bundle(cpu=99.0, cpu_status="down"))
    await db.commit()
    cpu = await _sensor(db, source)
    service = AlertService(AlertRepository(db))
    opened = await service.evaluate_sensor(cpu)
    assert opened.action != "noop"
    cpu.status = "paused"
    resolved = await service.evaluate_sensor(cpu)
    assert resolved.action == "resolved"
    # Commit so the fixture's tenant cleanup is not left waiting on this
    # session's uncommitted alert rows.
    await db.commit()
