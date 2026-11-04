"""Network Monitoring Center (observability plan P4 slice 1).

Error rates from counters (pure); the saver keeping per-interface history only
from two real samples; and the overview, list, detail and history API with
RBAC and tenant isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedInterface
from app.core.auth import create_access_token
from app.main import app
from app.models.firewall import VpnTunnel
from app.models.interface import Interface
from app.models.metric import Metric
from app.services.monitoring.interface_rates import compute_error_rates

NOW = datetime.now(timezone.utc)


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


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(
        user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
        is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


def test_error_rates_need_two_honest_samples():
    t0 = NOW - timedelta(seconds=10)
    assert compute_error_rates(previous_in=5, previous_out=0, previous_at=t0, current_in=25, current_out=0, observed_at=NOW) == (2.0, 0.0)
    assert compute_error_rates(previous_in=None, previous_out=None, previous_at=None, current_in=5, current_out=5, observed_at=NOW) == (None, None)
    # A counter that went backwards is a reset, not a negative rate.
    assert compute_error_rates(previous_in=50, previous_out=1, previous_at=t0, current_in=3, current_out=2, observed_at=NOW) == (None, 0.1)
    assert compute_error_rates(previous_in=1, previous_out=1, previous_at=NOW, current_in=2, current_out=2, observed_at=NOW) == (None, None)


def _bundle(octets_in: float, octets_out: float, errors_in: float, *, uplink_oper: str = "up") -> NormalizedBundle:
    return NormalizedBundle(
        devices=[NormalizedDevice(external_id="sw-1", name="core-sw-1", device_type="switch", ip_address="10.0.0.2")],
        interfaces=[
            NormalizedInterface(device_external_id="sw-1", external_id="1", name="Gi0/1", admin_status="up",
                                oper_status=uplink_oper, speed_mbps=100.0, in_octets=octets_in, out_octets=octets_out,
                                in_errors=errors_in, out_errors=0.0),
            NormalizedInterface(device_external_id="sw-1", external_id="2", name="Gi0/2", admin_status="up",
                                oper_status="down", speed_mbps=1000.0),
            NormalizedInterface(device_external_id="sw-1", external_id="3", name="Gi0/3", admin_status="down",
                                oper_status="down", speed_mbps=1000.0),
        ],
    )


async def _age_samples(db, source, seconds: int) -> None:
    """Pretend the previous poll happened `seconds` ago."""
    for i in (await db.execute(select(Interface).where(Interface.monitoring_source_id == source.id))).scalars():
        if i.last_telemetry_at is not None:
            i.last_telemetry_at = i.last_telemetry_at - timedelta(seconds=seconds)
    await db.commit()


async def test_saver_records_interface_history_only_from_two_samples(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="snmp")
    saver = _saver(db)
    await saver.save(source, _bundle(1_000_000, 500_000, 10))
    await db.commit()
    assert (await db.execute(select(Metric).where(Metric.interface_id.is_not(None), Metric.tenant_id == source.tenant_id))).scalars().all() == []

    await _age_samples(db, source, 60)
    # 7.5 MB in over ~60 s is ~1 Mbps on a 100 Mbps port; 120 new errors is ~2 / s.
    await saver.save(source, _bundle(8_500_000, 1_250_000, 130))
    await db.commit()
    rows = (await db.execute(select(Metric).where(Metric.interface_id.is_not(None), Metric.tenant_id == source.tenant_id))).scalars().all()
    by_name = {r.metric_name: r for r in rows}
    assert set(by_name) == {"rx_bps", "tx_bps", "interface_utilization_pct", "errors_in", "errors_out"}
    assert 0.9e6 < by_name["rx_bps"].value < 1.1e6 and by_name["rx_bps"].unit == "bps"
    assert 0.9 < by_name["interface_utilization_pct"].value < 1.1
    assert 1.8 < by_name["errors_in"].value < 2.2 and by_name["errors_out"].value == 0.0
    assert by_name["rx_bps"].dimensions == {"channel": "rx_bps"} and by_name["rx_bps"].capability == "bandwidth"

    # A counter reset records no traffic rate for that direction.
    await _age_samples(db, source, 60)
    await saver.save(source, _bundle(100, 2_000_000, 130))
    await db.commit()
    rx = (await db.execute(select(Metric).where(Metric.tenant_id == source.tenant_id, Metric.metric_name == "rx_bps"))).scalars().all()
    assert len(rx) == 1


async def test_network_api_overview_list_detail_history_and_isolation(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="snmp")
    saver = _saver(db)
    await saver.save(source, _bundle(1_000_000, 500_000, 10))
    await db.commit()
    await _age_samples(db, source, 60)
    await saver.save(source, _bundle(8_500_000, 1_250_000, 130))
    await db.commit()
    tenant_id = source.tenant_id
    uplink = (await db.execute(select(Interface).where(Interface.monitoring_source_id == source.id, Interface.name == "Gi0/1"))).scalar_one()
    db.add(VpnTunnel(tenant_id=tenant_id, device_id=uplink.device_id, name="to-branch", status="down", last_seen_at=NOW))
    db.add(VpnTunnel(tenant_id=tenant_id, device_id=uplink.device_id, name="to-dc", status="up", last_seen_at=NOW))
    await db.commit()

    async with _client(tenant_id) as client:
        o = (await client.get("/api/network/overview")).json()
        assert {k: o["interfaces"][k] for k in ("total", "up", "down", "unexpected_down", "admin_down", "measured", "devices")} == {
            "total": 3, "up": 1, "down": 2, "unexpected_down": 1, "admin_down": 1, "measured": 1, "devices": 1}
        assert [i["name"] for i in o["top_utilization"]] == ["Gi0/1"] and o["top_utilization"][0]["fresh"] is True
        assert [i["name"] for i in o["unexpected_down"]] == ["Gi0/2"]
        assert [i["name"] for i in o["top_errors"]] == ["Gi0/1"] and o["top_errors"][0]["errors_per_s_1h"] > 1.5
        assert (o["tunnels"]["total"], o["tunnels"]["down"], o["tunnels"]["items"][0]["name"]) == (2, 1, "to-branch")

        page = (await client.get("/api/network/interfaces", params={"sort": "status"})).json()
        assert page["total"] == 3 and page["items"][0]["name"] == "Gi0/2"  # enabled but down first
        assert (await client.get("/api/network/interfaces", params={"status": "admin_down"})).json()["total"] == 1
        assert (await client.get("/api/network/interfaces", params={"q": "core-sw"})).json()["total"] == 3
        assert (await client.get("/api/network/interfaces", params={"sort": "loudest"})).status_code == 422

        d = (await client.get(f"/api/network/interfaces/{uplink.id}")).json()
        assert d["device_name"] == "core-sw-1" and d["history"]["rx_bps"]["samples"] == 1
        h = (await client.get(f"/api/network/interfaces/{uplink.id}/history", params={"hours": 1})).json()
        assert h["bucket_seconds"] == 60 and len(h["series"]["rx_bps"]) == 1 and h["series"]["errors_out"][0]["avg"] == 0.0
        assert (await client.get(f"/api/network/interfaces/{uuid.uuid4()}")).status_code == 404

    async with _client(tenant_id, permissions=frozenset()) as client:
        assert (await client.get("/api/network/overview")).status_code == 403

    async with _client(uuid.uuid4()) as client:
        assert (await client.get("/api/network/overview")).json()["interfaces"]["total"] == 0
        assert (await client.get(f"/api/network/interfaces/{uplink.id}")).status_code == 404
        assert (await client.get(f"/api/network/interfaces/{uplink.id}/history")).status_code == 404
