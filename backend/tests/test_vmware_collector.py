"""
Unit tests for VMwareCollector.normalize() — Task 11.10.

Pure, no I/O. Validates raw vSphere VM/host data → NormalizedBundle mapping.
"""
from __future__ import annotations

import pytest

from app.collectors.vmware import VMwareCollector
from app.collectors.base import NormalizedBundle

pytestmark = pytest.mark.asyncio

_EMPTY_RAW = {"vms": [], "hosts": []}

_SINGLE_VM_RAW = {
    "vms": [
        {
            "moref": "vm-101",
            "name": "web-server-01",
            "power_state": "poweredOn",
            "guest_os": "Ubuntu Linux (64-bit)",
            "hostname": "web-server-01.local",
            "ip_address": "192.168.1.10",
            "cpu_usage_mhz": 312,
            "memory_usage_mb": 1024,
        }
    ],
    "hosts": [],
}

_MIXED_RAW = {
    "vms": [
        {"moref": "vm-1", "name": "vm-online",  "power_state": "poweredOn"},
        {"moref": "vm-2", "name": "vm-offline", "power_state": "poweredOff"},
    ],
    "hosts": [
        {"moref": "host-1", "name": "esxi-01", "connection_state": "connected",   "power_state": "poweredOn"},
        {"moref": "host-2", "name": "esxi-02", "connection_state": "disconnected", "power_state": "poweredOn"},
    ],
}


class TestVMwareNormalize:

    def test_empty_payload_returns_empty_bundle(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 0
        assert len(bundle.sensors) == 0

    def test_single_vm_maps_correctly(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_SINGLE_VM_RAW)

        assert len(bundle.devices) == 1
        dev = bundle.devices[0]
        assert dev.external_id == "vm-101"
        assert dev.name == "web-server-01"
        assert dev.device_type == "vm"
        assert dev.vendor == "VMware"
        assert dev.hostname == "web-server-01.local"
        assert dev.ip_address == "192.168.1.10"
        assert dev.status == "up"

    def test_powered_on_maps_to_up(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_MIXED_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["vm-online"] == "up"

    def test_powered_off_maps_to_down(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_MIXED_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["vm-offline"] == "down"

    def test_connected_host_maps_to_up(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_MIXED_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["esxi-01"] == "up"

    def test_disconnected_host_maps_to_down(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_MIXED_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["esxi-02"] == "down"

    def test_cpu_and_memory_sensors_created(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_SINGLE_VM_RAW)
        sensor_types = {s.metric_type for s in bundle.sensors}
        assert "cpu_usage_mhz" in sensor_types
        assert "memory_usage_mb" in sensor_types

    def test_sensor_values_match_payload(self):
        collector = VMwareCollector(source=None)
        bundle = collector.normalize(_SINGLE_VM_RAW)
        cpu_sensor = next(s for s in bundle.sensors if s.metric_type == "cpu_usage_mhz")
        mem_sensor = next(s for s in bundle.sensors if s.metric_type == "memory_usage_mb")
        assert cpu_sensor.value == 312.0
        assert mem_sensor.value == 1024.0
