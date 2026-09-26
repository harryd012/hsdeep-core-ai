"""
Unit tests for FortiManagerCollector.normalize() — Task 11.4.

Tests are pure (no I/O, no DB) and validate the raw-payload → NormalizedBundle
mapping against representative FortiManager JSON-RPC response fixtures.
"""
from __future__ import annotations

import pytest

from app.collectors.fortimanager import FortiManagerCollector
from app.collectors.base import NormalizedBundle

pytestmark = pytest.mark.asyncio

_EMPTY_RAW = {"status": {}, "devices": []}

_SINGLE_DEVICE_RAW = {
    "status": {"Hostname": "FMG-100F", "Version": "7.2.4"},
    "devices": [
        {
            "name": "FGT-Core-1",
            "sn": "FG100FTK22000001",
            "ip": "10.0.0.1",
            "platform_str": "FortiGate-100F",
            "os_ver": "v7.2",
            "conn_status": 0,   # 0 = connected/up
        }
    ],
}

_MULTI_DEVICE_RAW = {
    "status": {},
    "devices": [
        {"name": "FGT-A", "sn": "SN-A", "ip": "10.0.0.1", "conn_status": 0},
        {"name": "FGT-B", "sn": "SN-B", "ip": "10.0.0.2", "conn_status": 1},  # down
        {"name": "FGT-C", "sn": "SN-C", "ip": "10.0.0.3", "conn_status": 0},
    ],
}


class TestFortiManagerNormalize:

    def test_empty_payload_returns_empty_bundle(self):
        collector = FortiManagerCollector(source=None)
        bundle = collector.normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 0

    def test_single_device_maps_correctly(self):
        collector = FortiManagerCollector(source=None)
        bundle = collector.normalize(_SINGLE_DEVICE_RAW)

        assert len(bundle.devices) == 1
        dev = bundle.devices[0]
        assert dev.external_id == "FG100FTK22000001"
        assert dev.name == "FGT-Core-1"
        assert dev.device_type == "firewall"
        assert dev.vendor == "Fortinet"
        assert dev.model == "FortiGate-100F"
        assert dev.os_version == "v7.2"
        assert dev.hostname == "10.0.0.1"
        assert dev.status == "up"

    def test_conn_status_zero_maps_to_up(self):
        collector = FortiManagerCollector(source=None)
        bundle = collector.normalize(_MULTI_DEVICE_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["FGT-A"] == "up"
        assert statuses["FGT-C"] == "up"

    def test_conn_status_nonzero_maps_to_down(self):
        collector = FortiManagerCollector(source=None)
        bundle = collector.normalize(_MULTI_DEVICE_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["FGT-B"] == "down"

    def test_multiple_devices_all_present(self):
        collector = FortiManagerCollector(source=None)
        bundle = collector.normalize(_MULTI_DEVICE_RAW)
        assert len(bundle.devices) == 3
        names = {d.name for d in bundle.devices}
        assert names == {"FGT-A", "FGT-B", "FGT-C"}

    def test_normalize_is_pure(self):
        collector = FortiManagerCollector(source=None)
        b1 = collector.normalize(_SINGLE_DEVICE_RAW)
        b2 = collector.normalize(_SINGLE_DEVICE_RAW)
        assert len(b1.devices) == len(b2.devices)
        assert b1.devices[0].external_id == b2.devices[0].external_id
