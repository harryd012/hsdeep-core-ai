"""
Unit tests for AzureCollector.normalize() — Task 11.8.

Pure, no I/O, validates raw Azure VM listing → NormalizedBundle mapping.
"""
from __future__ import annotations

import pytest

from app.collectors.azure import AzureCollector
from app.collectors.base import NormalizedBundle

pytestmark = pytest.mark.asyncio

_EMPTY_RAW = {"vms": [], "metrics": {}}

_SINGLE_VM_RAW = {
    "vms": [
        {
            "id": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-web-01",
            "name": "vm-web-01",
            "location": "eastus",
            "vm_size": "Standard_D2s_v3",
            "os_type": "Windows",
            "provisioning_state": "Succeeded",
        }
    ],
    "metrics": {},
}

_MULTI_VM_RAW = {
    "vms": [
        {
            "id": "/subs/s1/vms/vm-a",
            "name": "vm-a",
            "location": "westus",
            "vm_size": "Standard_B1s",
            "os_type": "Linux",
            "provisioning_state": "Succeeded",
        },
        {
            "id": "/subs/s1/vms/vm-b",
            "name": "vm-b",
            "location": "eastus",
            "vm_size": "Standard_B1s",
            "os_type": "Windows",
            "provisioning_state": "Failed",
        },
    ],
    "metrics": {},
}


class TestAzureNormalize:

    def test_empty_payload_returns_empty_bundle(self):
        collector = AzureCollector(source=None)
        bundle = collector.normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 0

    def test_single_vm_maps_correctly(self):
        collector = AzureCollector(source=None)
        bundle = collector.normalize(_SINGLE_VM_RAW)

        assert len(bundle.devices) == 1
        dev = bundle.devices[0]
        assert dev.name == "vm-web-01"
        assert dev.device_type == "cloud_resource"
        assert dev.vendor == "Microsoft"
        assert dev.model == "Standard_D2s_v3"
        assert dev.os_version == "Windows"
        assert dev.status == "up"
        assert dev.site_hint == "eastus"

    def test_succeeded_provisioning_maps_to_up(self):
        collector = AzureCollector(source=None)
        bundle = collector.normalize(_MULTI_VM_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["vm-a"] == "up"

    def test_failed_provisioning_maps_to_down(self):
        collector = AzureCollector(source=None)
        bundle = collector.normalize(_MULTI_VM_RAW)
        statuses = {d.name: d.status for d in bundle.devices}
        assert statuses["vm-b"] == "down"

    def test_all_vms_present(self):
        collector = AzureCollector(source=None)
        bundle = collector.normalize(_MULTI_VM_RAW)
        assert len(bundle.devices) == 2

    def test_normalize_is_pure(self):
        collector = AzureCollector(source=None)
        b1 = collector.normalize(_SINGLE_VM_RAW)
        b2 = collector.normalize(_SINGLE_VM_RAW)
        assert len(b1.devices) == len(b2.devices)
        assert b1.devices[0].name == b2.devices[0].name
