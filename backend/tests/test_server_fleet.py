"""Server fleet resources (observability plan P5 slice 1).

Pure: which sensors are host resources, the platform, the worst reading
speaking for a resource, percentages compared only as percentages, staleness,
ordering and the summary. API: the fleet of one tenant from real sensor rows,
the platform filter, RBAC and tenant isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.device import Device
from app.models.sensor import Sensor
from app.services.host_resources_service import build_rows, platform_of, resource_of, summarize

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _sensor(device_id, metric_type, value, unit="%", status="up", name=None, at=NOW, source_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(), device_id=device_id, monitoring_source_id=source_id, metric_type=metric_type,
        name=name or metric_type, current_value=value, unit=unit, status=status, last_value_at=at,
    )


def _device(name, os_version=None):
    return SimpleNamespace(id=uuid.uuid4(), name=name, ip_address="10.0.0.1", os_version=os_version, status="up")


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_resource_and_platform_classification():
    assert [resource_of(t) for t in ("cpu_usage", "CPU", "memory_usage", "disk_usage", "load_avg_1m", "uptime_seconds")] == [
        "cpu", "cpu", "memory", "disk", "load", "uptime",
    ]
    assert resource_of("cpu_usage_mhz") == "cpu" and resource_of("memory_usage_mb") == "memory"
    for not_host in ("prtg_sensor", "ssl_security", "process_cpu", "cpu_queue_length", None):
        assert resource_of(not_host) is None
    assert platform_of({"linux_ssh"}, None) == "linux"
    assert platform_of({"prtg", "windows_winrm"}, None) == "windows"
    assert platform_of({"vmware"}, "Ubuntu") == "vmware"  # the reporting collector wins
    assert platform_of({"prtg"}, "Microsoft Windows Server 2022") == "windows"
    assert platform_of({"prtg"}, "Ubuntu 22.04") == "linux"
    assert platform_of({"prtg"}, None) == "other"


def test_rows_pick_worst_reading_and_order_by_risk():
    lin, win, vm, idle = _device("lin-01"), _device("win-01"), _device("esx-vm"), _device("printer")
    src = {"l": "linux_ssh", "w": "windows_winrm", "v": "vmware"}
    sensors = [
        _sensor(lin.id, "disk_usage", 71.0, name="/", source_id="l"),
        _sensor(lin.id, "disk_usage", 93.0, name="/var", status="warning", source_id="l"),
        _sensor(lin.id, "memory_usage", 40.0, source_id="l"),
        _sensor(lin.id, "load_avg_1m", 0.4, unit="", source_id="l"),
        _sensor(win.id, "cpu_usage", 20.0, name="Core 0", source_id="w"),
        _sensor(win.id, "cpu_usage", 97.0, name="Core 1", status="down", source_id="w"),
        _sensor(win.id, "uptime_seconds", 86400.0, unit="s", source_id="w"),
        # VMware MHz / MB are shown, never compared as percentages.
        _sensor(vm.id, "cpu_usage_mhz", 2400.0, unit="MHz", source_id="v", at=NOW - timedelta(hours=3)),
        _sensor(vm.id, "memory_usage_mb", 8192.0, unit="MB", source_id="v", at=NOW - timedelta(hours=3)),
        _sensor(idle.id, "prtg_sensor", None, unit=None),  # not a host resource
    ]
    rows = build_rows([lin, win, vm, idle], sensors, src, NOW)
    assert [r.name for r in rows] == ["win-01", "lin-01", "esx-vm"]  # critical, then warning, then the rest

    win_row, lin_row, vm_row = rows
    assert (win_row.platform, win_row.cpu.name, win_row.cpu.value, win_row.critical) == ("windows", "Core 1", 97.0, 1)
    assert win_row.uptime.value == 86400.0 and win_row.memory is None and win_row.disk is None
    assert (lin_row.disk.name, lin_row.disk.value, lin_row.disks, lin_row.peak_pct) == ("/var", 93.0, 2, 93.0)
    assert lin_row.load.value == 0.4 and not lin_row.stale
    assert (vm_row.platform, vm_row.cpu.unit, vm_row.peak_pct, vm_row.stale) == ("vmware", "MHz", None, True)

    s = summarize(rows)
    assert (s.servers, s.critical, s.warning, s.stale, s.disk_over_90, s.cpu_over_90) == (3, 1, 1, 1, 1, 1)
    assert s.by_platform == {"windows": 1, "linux": 1, "vmware": 1, "other": 0}


def test_missing_value_never_counts_as_a_percentage():
    d = _device("srv")
    rows = build_rows([d], [_sensor(d.id, "disk_usage", None, status="unknown")], {}, NOW)
    assert rows[0].disk.value is None and rows[0].peak_pct is None
    assert summarize(rows).disk_over_90 == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _client(tenant_id, permissions=frozenset({"devices.read"})):
    token = create_access_token(
        user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local", is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def test_fleet_api_platform_filter_rbac_and_isolation(tenant, monitoring_source_factory):
    linux = await monitoring_source_factory(source_type="linux_ssh")
    windows = await monitoring_source_factory(source_type="windows_winrm")
    async with AsyncSessionLocal() as db:
        lin = Device(tenant_id=tenant.id, name="lin-app-01", device_type="server", status="up", monitoring_source_id=linux.id)
        win = Device(tenant_id=tenant.id, name="win-dc-01", device_type="server", status="up", monitoring_source_id=windows.id)
        switch = Device(tenant_id=tenant.id, name="core-sw", device_type="switch", status="up")
        db.add_all([lin, win, switch])
        await db.flush()
        db.add_all([
            Sensor(tenant_id=tenant.id, monitoring_source_id=linux.id, device_id=lin.id, external_id="l-disk", name="/data",
                   metric_type="disk_usage", unit="%", current_value=95.5, status="down", last_value_at=NOW),
            Sensor(tenant_id=tenant.id, monitoring_source_id=linux.id, device_id=lin.id, external_id="l-mem", name="Memory",
                   metric_type="memory_usage", unit="%", current_value=55.0, status="up", last_value_at=NOW),
            Sensor(tenant_id=tenant.id, monitoring_source_id=windows.id, device_id=win.id, external_id="w-cpu", name="CPU 0",
                   metric_type="cpu_usage", unit="%", current_value=12.0, status="up", last_value_at=NOW),
            # A switch with only a non-host sensor is not a server row.
            Sensor(tenant_id=tenant.id, monitoring_source_id=linux.id, device_id=switch.id, external_id="sw-ssl", name="SSL",
                   metric_type="ssl_security", status="up"),
        ])
        await db.commit()

    async with _client(tenant.id) as client:
        body = (await client.get("/api/explorer/hosts/resources")).json()
        assert [s["name"] for s in body["servers"]] == ["lin-app-01", "win-dc-01"]
        first = body["servers"][0]
        assert (first["platform"], first["disk"]["name"], first["disk"]["value"], first["critical"]) == ("linux", "/data", 95.5, 1)
        assert body["summary"] == {
            "servers": 2, "by_platform": {"windows": 1, "linux": 1, "vmware": 0, "other": 0},
            "critical": 1, "warning": 0, "stale": 0, "disk_over_90": 1, "cpu_over_90": 0,
        }
        only_win = (await client.get("/api/explorer/hosts/resources", params={"platform": "windows"})).json()
        assert [s["name"] for s in only_win["servers"]] == ["win-dc-01"] and only_win["summary"]["servers"] == 2
        assert (await client.get("/api/explorer/hosts/resources", params={"platform": "solaris"})).status_code == 422

    async with _client(tenant.id, permissions=frozenset()) as client:
        assert (await client.get("/api/explorer/hosts/resources")).status_code == 403
    async with _client(uuid.uuid4()) as client:
        other = (await client.get("/api/explorer/hosts/resources")).json()
        assert other["servers"] == [] and other["summary"]["servers"] == 0
