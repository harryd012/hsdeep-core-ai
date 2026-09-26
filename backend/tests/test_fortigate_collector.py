"""
Unit tests for FortiGateCollector.normalize().

Tests validate that raw FortiGate API responses are correctly converted to
NormalizedBundle format without external I/O or database access.
"""
import pytest

from app.collectors.fortigate import FortiGateCollector
from app.collectors.base import NormalizedBundle
from tests.fixtures.fortigate_payloads import (
    FORTIGATE_COLLECT_EMPTY,
    FORTIGATE_COLLECT_SINGLE,
    FORTIGATE_COLLECT_MULTI,
)


class TestFortiGateNormalize:
    """Test FortiGateCollector.normalize() pure function."""

    def test_empty_payload(self):
        """Empty FortiGate response normalizes to minimal device bundle."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_EMPTY)

        assert isinstance(bundle, NormalizedBundle)
        # FortiGate collector creates a device for the firewall itself
        assert len(bundle.devices) == 1
        assert bundle.devices[0].device_type == "firewall"
        assert len(bundle.interfaces) == 0

    def test_single_firewall_multi_interface(self):
        """Single FortiGate with multiple interfaces normalizes correctly."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_SINGLE)

        # Should have single device (the firewall)
        assert len(bundle.devices) == 1
        device = bundle.devices[0]
        assert device.external_id == "1"
        assert device.device_type == "firewall"
        assert device.vendor == "Fortinet"

        # Should have two interfaces
        assert len(bundle.interfaces) == 2

        # Verify interface names
        iface_names = {i.name for i in bundle.interfaces}
        assert "port1" in iface_names
        assert "port2" in iface_names

        # Verify interface statuses
        port1 = next(i for i in bundle.interfaces if i.name == "port1")
        assert port1.oper_status == "up"
        assert port1.speed_mbps == 1000.0

        port2 = next(i for i in bundle.interfaces if i.name == "port2")
        assert port2.oper_status == "down"

    def test_multiple_interfaces_with_vlan(self):
        """FortiGate with physical and VLAN interfaces normalizes correctly."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_MULTI)

        assert len(bundle.devices) == 1
        assert len(bundle.interfaces) == 4

        # Verify interface types are captured
        iface_names = {i.name for i in bundle.interfaces}
        assert "port1" in iface_names
        assert "port2" in iface_names
        assert "port3" in iface_names
        assert "vlan100" in iface_names

        # Verify VLAN interface
        vlan = next(i for i in bundle.interfaces if i.name == "vlan100")
        assert vlan.oper_status == "up"

    def test_interface_mac_address(self):
        """Interface MAC addresses are extracted."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_SINGLE)

        port1 = next(i for i in bundle.interfaces if i.name == "port1")
        assert port1.mac_address == "00:11:22:33:44:55"

        port2 = next(i for i in bundle.interfaces if i.name == "port2")
        assert port2.mac_address == "00:11:22:33:44:56"

    def test_interface_status_mapping(self):
        """Interface status correctly maps from FortiGate status."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_SINGLE)

        up_iface = next(i for i in bundle.interfaces if i.name == "port1")
        assert up_iface.admin_status == "up"
        assert up_iface.oper_status == "up"

        down_iface = next(i for i in bundle.interfaces if i.name == "port2")
        assert down_iface.admin_status == "down"
        assert down_iface.oper_status == "down"

    def test_all_interfaces_belong_to_device(self):
        """Every interface references the firewall device."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_MULTI)

        for interface in bundle.interfaces:
            assert interface.device_external_id == "1"

    def test_device_has_firewall_type(self):
        """FortiGate device is correctly typed as 'firewall'."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_SINGLE)

        assert len(bundle.devices) == 1
        device = bundle.devices[0]
        assert device.device_type == "firewall"
        assert device.vendor == "Fortinet"

    def test_normalize_is_pure_function(self):
        """Calling normalize() twice with same input produces identical output."""
        collector = FortiGateCollector(source=None)

        bundle1 = collector.normalize(FORTIGATE_COLLECT_SINGLE)
        bundle2 = collector.normalize(FORTIGATE_COLLECT_SINGLE)

        assert len(bundle1.devices) == len(bundle2.devices)
        assert len(bundle1.interfaces) == len(bundle2.interfaces)

        assert bundle1.devices[0].external_id == bundle2.devices[0].external_id
        assert bundle1.devices[0].device_type == bundle2.devices[0].device_type

        assert len(bundle1.interfaces) == len(bundle2.interfaces)
        for i1, i2 in zip(bundle1.interfaces, bundle2.interfaces):
            assert i1.name == i2.name
            assert i1.oper_status == i2.oper_status

    def test_no_hardcoded_values_beyond_structure(self):
        """Only the device structure is hardcoded; interfaces come from payload."""
        collector = FortiGateCollector(source=None)
        bundle = collector.normalize(FORTIGATE_COLLECT_SINGLE)

        # Device is hardcoded as "firewall" type with external_id="1"
        assert bundle.devices[0].external_id == "1"
        assert bundle.devices[0].device_type == "firewall"

        # But interface names and statuses come from payload
        for interface in bundle.interfaces:
            assert interface.name in FORTIGATE_COLLECT_SINGLE["interfaces"]["results"][0].get("name", "") or \
                   interface.name in [i["name"] for i in FORTIGATE_COLLECT_SINGLE["interfaces"].get("results", [])]
