"""Observability reports (observability plan P11 slice 1).

Availability from recorded reachability checks (a device without samples is
"not measured", never 100 %), sensor statistics with the time alerts were open
inside the window, interface traffic and errors from history; window rules,
RBAC and tenant isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedInterface, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.device import Device
from app.models.interface import Interface
from app.models.metric import Metric
from app.models.sensor import Sensor
from app.services.reports.observability_reports import WindowInvalid, window

UNTIL = datetime.now(timezone.utc).replace(microsecond=0)
SINCE = UNTIL - timedelta(hours=10)


def test_window_rules():
    s, u = window(None, None, now=UNTIL)
    assert u - s == timedelta(days=7)
    for a, b in ((UNTIL, UNTIL - timedelta(hours=1)), (UNTIL - timedelta(days=91), UNTIL), (datetime(2026, 1, 1), UNTIL)):
        with pytest.raises(WindowInvalid):
            window(a, b, now=UNTIL)


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
                       sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db))


async def _estate(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="snmp")
    await _saver(db).save(source, NormalizedBundle(
        devices=[NormalizedDevice(external_id="a", name="edge-a", device_type="router"),
                 NormalizedDevice(external_id="b", name="edge-b", device_type="router"),
                 NormalizedDevice(external_id="c", name="never-polled", device_type="router")],
        interfaces=[NormalizedInterface(device_external_id="a", external_id="1", name="wan0", speed_mbps=100.0)],
        sensors=[NormalizedSensorReading(external_id="cpu", name="CPU", metric_type="cpu_usage", value=10.0, unit="%",
                                         status="up", device_external_id="a")],
    ))
    await db.commit()
    tenant_id = source.tenant_id
    dev = {d.name: d for d in (await db.execute(select(Device).where(Device.tenant_id == tenant_id))).scalars()}
    # The saver's own canonical samples land at "now"; keep only ours for exact figures.
    for m in (await db.execute(select(Metric).where(Metric.tenant_id == tenant_id))).scalars():
        await db.delete(m)
    await db.commit()
    sensor = (await db.execute(select(Sensor).where(Sensor.monitoring_source_id == source.id))).scalar_one()
    iface = (await db.execute(select(Interface).where(Interface.monitoring_source_id == source.id))).scalar_one()

    def metric(**kw):
        db.add(Metric(tenant_id=tenant_id, monitoring_source_id=source.id, **kw))

    # edge-a: 10 checks, 9 up. edge-b: 4 checks, all up. One check outside the window.
    for i in range(10):
        metric(device_id=dev["edge-a"].id, metric_type="reachability", metric_name="reachability", capability="availability",
               boolean_value=i != 3, recorded_at=SINCE + timedelta(minutes=30 * i + 1))
    for i in range(4):
        metric(device_id=dev["edge-b"].id, metric_type="reachability", metric_name="reachability", capability="availability",
               boolean_value=True, recorded_at=SINCE + timedelta(hours=i + 1))
    metric(device_id=dev["edge-b"].id, metric_type="reachability", metric_name="reachability", capability="availability",
           boolean_value=False, recorded_at=SINCE - timedelta(hours=2))
    # CPU readings 10, 20 ... 100.
    for i in range(10):
        metric(sensor_id=sensor.id, metric_type="cpu_usage", value=10.0 * (i + 1), unit="%",
               recorded_at=SINCE + timedelta(minutes=30 * i + 2), dimensions={"channel": "primary"})
    # Interface: two polls.
    for t, rx, util, err in ((SINCE + timedelta(hours=1), 1e6, 10.0, 0.0), (SINCE + timedelta(hours=2), 9e6, 90.0, 2.0)):
        metric(interface_id=iface.id, device_id=dev["edge-a"].id, metric_type="interface_rx_bps", metric_name="rx_bps", value=rx, recorded_at=t)
        metric(interface_id=iface.id, device_id=dev["edge-a"].id, metric_type="interface_utilization_pct",
               metric_name="interface_utilization_pct", value=util, recorded_at=t)
        metric(interface_id=iface.id, device_id=dev["edge-a"].id, metric_type="interface_errors_in", metric_name="errors_in", value=err, recorded_at=t)
    # A status alert open from 1 h before the window to 1 h into it (counts 60 min), and one still open for the last 30 min.
    for start, end in ((SINCE - timedelta(hours=1), SINCE + timedelta(hours=1)), (UNTIL - timedelta(minutes=30), None)):
        db.add(Alert(tenant_id=tenant_id, sensor_id=sensor.id if end else None, device_id=dev["edge-a"].id,
                     monitoring_source_id=source.id, title="CPU high", severity="critical",
                     status="resolved" if end else "open", source="monitoring_sync", triggered_at=start, last_occurred_at=start,
                     resolved_at=end, occurrence_count=1, signal_external_id=None if end else "x"))
    await db.commit()
    return tenant_id, sensor, iface


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def test_reports_from_recorded_history(db, monitoring_source_factory):
    tenant_id, sensor, iface = await _estate(db, monitoring_source_factory)
    params = {"since": SINCE.isoformat(), "until": UNTIL.isoformat()}
    async with _client(tenant_id) as client:
        a = (await client.get("/api/reports/availability", params=params)).json()
        assert a["summary"] == {"devices_measured": 2, "devices_not_measured": 1, "fleet_availability_pct": round(13 / 14 * 100, 3),
                                "devices_below_99_9": 1, "devices_below_99": 1, "samples": 14}
        assert [(i["device"], i["availability_pct"], i["down_samples"]) for i in a["items"]] == [("edge-a", 90.0, 1), ("edge-b", 100.0, 0)]

        s = (await client.get("/api/reports/sensors", params=params)).json()
        [row] = s["items"]
        assert (row["sensor"], row["samples"], row["avg"], row["min"], row["max"]) == ("CPU", 10, 55.0, 10.0, 100.0)
        assert 95.0 < row["p95"] <= 100.0
        assert (row["alerts"], row["alert_minutes"]) == (1, 60.0)  # only the part inside the window
        assert (await client.get("/api/reports/sensors", params={**params, "metric_type": "memory_usage"})).json()["items"] == []

        i = (await client.get("/api/reports/interfaces", params=params)).json()
        [irow] = i["items"]
        assert (irow["interface"], irow["samples"], irow["rx_avg_bps"], irow["rx_peak_bps"]) == ("wan0", 2, 5e6, 9e6)
        assert (irow["utilization_peak_pct"], irow["errors_in_peak_per_s"], irow["tx_avg_bps"]) == (90.0, 2.0, None)
        assert i["summary"] == {"interfaces": 1, "busy_over_80": 1, "with_errors": 1}

        assert (await client.get("/api/reports/availability", params={"since": (UNTIL - timedelta(days=100)).isoformat()})).status_code == 422

    async with _client(tenant_id, permissions=frozenset()) as client:
        assert (await client.get("/api/reports/availability")).status_code == 403
    async with _client(uuid.uuid4()) as client:
        empty = (await client.get("/api/reports/availability", params=params)).json()
        assert empty["summary"]["devices_measured"] == 0 and empty["items"] == []
        assert (await client.get("/api/reports/sensors", params=params)).json()["items"] == []
        assert (await client.get("/api/reports/interfaces", params=params)).json()["items"] == []
    await db.commit()
