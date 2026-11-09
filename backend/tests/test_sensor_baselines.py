"""Dynamic baselines (observability plan P9 slice 2).

Baseline maths (pure): learning until enough history, hour-of-day vs overall
expectation, direction, the spread floor for flat series, hysteresis. Then
learning from real stored history and judging the latest reading: the anomaly
alert opens as a baseline signal (never touching the sensor's status alert),
resolves when back within range, and resolves when the reading goes stale or
the sensor is paused. And the API with RBAC, validation, audit and isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.metric import Metric
from app.models.sensor import Sensor
from app.models.sensor_baseline import SensorBaseline
from app.services.monitoring.baseline_math import Baseline, Stat, judge, spread
from app.services.monitoring.baseline_service import BaselineService

NOW = datetime.now(timezone.utc).replace(minute=30, second=0, microsecond=0)


def _baseline(**hours) -> Baseline:
    return Baseline(hours={int(k[1:]): v for k, v in hours.items()}, overall=Stat(50.0, 5.0, 200), span_days=7.0)


def test_judge_rules():
    young = Baseline(hours={}, overall=Stat(50.0, 5.0, 10), span_days=1.0)
    assert judge(young, 99.0, 3, sensitivity=3, direction="both", currently_anomalous=False).state == "learning"
    assert judge(_baseline(), None, 3, sensitivity=3, direction="both", currently_anomalous=False).state == "learning"

    b = _baseline(h9=Stat(80.0, 5.0, 30), h3=Stat(20.0, 1.0, 3))
    nine = judge(b, 92.5, 9, sensitivity=3, direction="both", currently_anomalous=False)
    assert (nine.state, nine.basis, nine.z, nine.low, nine.high) == ("normal", "hour", 2.5, 65.0, 95.0)
    assert judge(b, 96.0, 9, sensitivity=3, direction="both", currently_anomalous=False).state == "anomalous"
    # Hour 3 has too few samples: the overall stat stands in.
    assert judge(b, 50.0, 3, sensitivity=3, direction="both", currently_anomalous=False).basis == "overall"
    # Direction.
    assert judge(b, 60.0, 9, sensitivity=3, direction="above", currently_anomalous=False).state == "normal"
    assert judge(b, 60.0, 9, sensitivity=3, direction="below", currently_anomalous=False).state == "anomalous"
    # Hysteresis: once anomalous, clears only below 0.75 * sensitivity (z 2.25 here).
    assert judge(b, 92.0, 9, sensitivity=3, direction="both", currently_anomalous=True).state == "anomalous"
    assert judge(b, 91.0, 9, sensitivity=3, direction="both", currently_anomalous=True).state == "normal"
    # A flat series (std 0) is floored at 5 % of its mean, not 0.
    assert spread(Stat(100.0, 0.0, 50)) == 5.0
    assert judge(Baseline({}, Stat(100.0, 0.0, 50), 7.0), 101.0, 0, sensitivity=3, direction="both",
                 currently_anomalous=False).state == "normal"
    assert Baseline.from_json(b.to_json()) == b


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
                       sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db))


def _expected(hour: int) -> float:
    """Busy office hours, quiet nights."""
    return 70.0 if 9 <= hour < 17 else 30.0


async def _sensor_with_history(db, monitoring_source_factory) -> Sensor:
    source = await monitoring_source_factory(source_type="windows_winrm")
    await _saver(db).save(source, NormalizedBundle(sensors=[
        # The saver records this first reading as history too: make it this hour's usual value.
        NormalizedSensorReading(external_id="cpu", name="CPU", metric_type="cpu_usage", value=_expected(datetime.now(timezone.utc).hour),
                                unit="%", status="up"),
    ]))
    await db.commit()
    sensor = (await db.execute(select(Sensor).where(Sensor.monitoring_source_id == source.id))).scalar_one()
    # A week of hourly readings: the hour's level, +/- 2.
    start = NOW - timedelta(days=7)
    for i in range(7 * 24):
        t = start + timedelta(hours=i)
        db.add(Metric(tenant_id=sensor.tenant_id, sensor_id=sensor.id, monitoring_source_id=source.id,
                      metric_type="cpu_usage", value=_expected(t.hour) + (i % 5 - 2), unit="%", recorded_at=t,
                      dimensions={"channel": "primary"}))
    await db.commit()
    return sensor


async def _set_reading(db, sensor, value, at=None):
    sensor.current_value, sensor.last_value_at = value, at or NOW
    await db.commit()


async def _baseline_alerts(db, sensor):
    return (await db.execute(select(Alert).where(Alert.signal_external_id == f"baseline:{sensor.id}"))).scalars().all()


async def test_learn_judge_alert_and_resolve(db, monitoring_source_factory):
    sensor = await _sensor_with_history(db, monitoring_source_factory)
    service = BaselineService(db)
    normal = _expected(NOW.hour)
    await _set_reading(db, sensor, normal)
    row, _, _ = await service.configure(sensor.tenant_id, sensor, enabled=True, direction="both", sensitivity=3.0)
    await db.commit()
    b = Baseline.from_json(row.stats)
    assert b.ready and b.span_days > 6.9 and b.overall.n >= 160
    assert abs(b.hours[NOW.hour].mean - normal) < 1.0
    assert row.state == "normal" and await _baseline_alerts(db, sensor) == []

    # Far outside this hour's range: a warning alert keyed as a baseline signal.
    await _set_reading(db, sensor, normal + 40)
    assert await service.evaluate(row, sensor, now=NOW) == "anomalous"
    await db.commit()
    [alert] = await _baseline_alerts(db, sensor)
    assert (alert.source, alert.severity, alert.status, alert.sensor_id) == ("baseline", "warning", "open", None)
    assert "standard deviations above" in alert.description
    # The sensor's own status alert path is untouched.
    assert (await db.execute(select(Alert).where(Alert.sensor_id == sensor.id))).scalars().all() == []

    # Back to normal: resolved.
    await _set_reading(db, sensor, normal)
    assert await service.evaluate(row, sensor, now=NOW) == "normal"
    await db.commit()
    assert [a.status for a in await _baseline_alerts(db, sensor)] == ["resolved"]

    # A stale reading is not judged, and an open anomaly resolves.
    await _set_reading(db, sensor, normal + 40)
    await service.evaluate(row, sensor, now=NOW)
    await db.commit()
    await _set_reading(db, sensor, normal + 40, at=NOW - timedelta(hours=3))
    assert await service.evaluate(row, sensor, now=NOW) == "stale"
    await db.commit()
    assert all(a.status == "resolved" for a in await _baseline_alerts(db, sensor))

    # Paused sensor: not judged.
    await _set_reading(db, sensor, normal + 40)
    sensor.admin_state = "paused"
    await db.commit()
    assert await service.evaluate(row, sensor, now=NOW) == "paused"
    # Commit so the fixture's tenant cleanup is not left waiting on this session's rows.
    await db.commit()


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


@pytest.mark.usefixtures("seeded_subscription")
async def test_baseline_api(db, monitoring_source_factory):
    from app.models.knowledge_audit import AuditEvent

    sensor = await _sensor_with_history(db, monitoring_source_factory)
    await _set_reading(db, sensor, _expected(NOW.hour))
    url = f"/api/monitoring/sensors/{sensor.id}/baseline"
    async with _client(sensor.tenant_id) as client:
        off = (await client.get(url)).json()
        assert (off["configured"], off["state"], off["hours"]) == (False, "off", [])
        assert (await client.put(url, json={"enabled": True})).status_code == 403

    writer = frozenset({"monitoring.read", "monitoring.update"})
    async with _client(sensor.tenant_id, writer) as client:
        assert (await client.put(url, json={"sensitivity": 10})).status_code == 422
        assert (await client.put(url, json={"direction": "sideways"})).status_code == 422
        on = (await client.put(url, json={"enabled": True, "sensitivity": 4, "direction": "above"})).json()
        assert (on["configured"], on["state"], on["ready"], on["sensitivity"], len(on["hours"])) == (True, "normal", True, 4.0, 24)
        noon = next(h for h in on["hours"] if h["hour"] == 12)
        assert noon["basis"] == "hour" and 65 < noon["expected"] < 75 and noon["high"] > noon["expected"]
        listed = (await client.get("/api/monitoring/baselines")).json()
        assert [x["sensor_id"] for x in listed] == [str(sensor.id)]
        assert (await client.put(url, json={"enabled": False})).json()["state"] == "paused"

    events = (await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == sensor.tenant_id,
                                                        AuditEvent.action == "sensor.baseline_updated"))).scalars().all()
    assert len(events) == 2
    async with _client(uuid.uuid4(), writer) as client:
        assert (await client.get(url)).status_code == 404
        assert (await client.get("/api/monitoring/baselines")).json() == []
    row = (await db.execute(select(SensorBaseline).where(SensorBaseline.sensor_id == sensor.id))).scalar_one()
    assert row.enabled is False
    await db.commit()
