"""
Unit tests for MerakiCollector.normalize().

Tests validate that raw Meraki Dashboard API responses are correctly converted to
NormalizedBundle format without external I/O or database access.
"""
import pytest

from app.collectors.meraki import MerakiCollector
from app.collectors.base import NormalizedBundle
from tests.fixtures.meraki_payloads import (
    MERAKI_COLLECT_EMPTY,
    MERAKI_COLLECT_SINGLE,
    MERAKI_COLLECT_MULTI,
)


class TestMerakiNormalize:
    """Test MerakiCollector.normalize() pure function."""

    def test_empty_payload(self):
        """Empty Meraki response normalizes to empty bundle."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_EMPTY)

        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 0
        assert len(bundle.sensors) == 0

    def test_single_network_switch_devices(self):
        """Single network with switch devices normalizes correctly."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_SINGLE)

        assert len(bundle.devices) == 2
        device_serials = {d.external_id for d in bundle.devices}
        assert "Q2QQ-XXXX-XXXX" in device_serials
        assert "Q2QQ-YYYY-YYYY" in device_serials

        # Verify device properties
        device1 = next(d for d in bundle.devices if d.external_id == "Q2QQ-XXXX-XXXX")
        assert device1.name == "Switch-Main-1"
        assert device1.device_type == "cloud_resource"
        assert device1.vendor == "Cisco"
        assert device1.model == "MS225-48"
        assert device1.mac_address == "00:11:22:33:44:55"
        assert device1.status == "up"

    def test_multiple_networks_mixed_devices(self):
        """Multiple networks with mixed device types normalize correctly."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_MULTI)

        assert len(bundle.devices) == 4

        device_serials = {d.external_id for d in bundle.devices}
        assert device_serials == {"Q2QQ-XXXX-XXXX", "Q2QQ-YYYY-YYYY", "Q2CD-XXXX-XXXX", "Q2CD-YYYY-YYYY"}

        # Verify all devices are cloud_resource type
        for device in bundle.devices:
            assert device.device_type == "cloud_resource"
            assert device.vendor == "Cisco"

    def test_device_status_online_offline(self):
        """Device status correctly maps from Meraki status."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_MULTI)

        # Online devices (3 total: Q2QQ-XXXX-XXXX, Q2QQ-YYYY-YYYY, Q2CD-XXXX-XXXX)
        online_devices = [d for d in bundle.devices if d.status == "up"]
        assert len(online_devices) == 3

        # Offline devices (1 total: Q2CD-YYYY-YYYY)
        offline_devices = [d for d in bundle.devices if d.status == "down"]
        assert len(offline_devices) == 1
        offline = offline_devices[0]
        assert offline.external_id == "Q2CD-YYYY-YYYY"

    def test_device_mac_addresses(self):
        """Device MAC addresses are extracted from Meraki data."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_SINGLE)

        device1 = next(d for d in bundle.devices if d.name == "Switch-Main-1")
        assert device1.mac_address == "00:11:22:33:44:55"

        device2 = next(d for d in bundle.devices if d.name == "Switch-Main-2")
        assert device2.mac_address == "00:11:22:33:44:66"

    def test_device_models(self):
        """Device model information is preserved."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_MULTI)

        # Switch models
        switches = [d for d in bundle.devices if d.external_id in ["Q2QQ-XXXX-XXXX", "Q2QQ-YYYY-YYYY"]]
        for switch in switches:
            assert switch.model == "MS225-48"

        # AP models
        aps = [d for d in bundle.devices if d.external_id in ["Q2CD-XXXX-XXXX", "Q2CD-YYYY-YYYY"]]
        for ap in aps:
            assert ap.model == "MR42"

    def test_sensor_readings_from_devices(self):
        """Environmental sensor readings are extracted if available."""
        device_with_sensors = {
            "serial": "Q2QQ-XXXX-XXXX",
            "name": "Switch-with-sensors",
            "model": "MS225-48",
            "type": "switch",
            "status": "online",
            "mac": "00:11:22:33:44:55",
            "temperature": 38,
            "humidity": 45,
        }

        raw = {
            "organizations": [],
            "networks": [],
            "devices": [device_with_sensors],
            "device_statuses": {}
        }

        collector = MerakiCollector(source=None)
        bundle = collector.normalize(raw)

        assert len(bundle.devices) == 1
        assert len(bundle.sensors) == 2

        # Verify temperature sensor
        temp_sensor = next(s for s in bundle.sensors if s.metric_type == "environmental_temperature")
        assert temp_sensor.value == 38
        assert temp_sensor.unit == "C"
        assert temp_sensor.device_external_id == "Q2QQ-XXXX-XXXX"

        # Verify humidity sensor
        humidity_sensor = next(s for s in bundle.sensors if s.metric_type == "environmental_humidity")
        assert humidity_sensor.value == 45
        assert humidity_sensor.unit == "%"
        assert humidity_sensor.device_external_id == "Q2QQ-XXXX-XXXX"

    def test_no_sensors_without_environmental_data(self):
        """Devices without environmental sensors don't create sensor entries."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_SINGLE)

        assert len(bundle.devices) == 2
        assert len(bundle.sensors) == 0

    def test_all_devices_are_cloud_resource_type(self):
        """All Meraki devices are correctly typed as cloud_resource."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_MULTI)

        for device in bundle.devices:
            assert device.device_type == "cloud_resource"

    def test_normalize_is_pure_function(self):
        """Calling normalize() twice with same input produces identical output."""
        collector = MerakiCollector(source=None)

        bundle1 = collector.normalize(MERAKI_COLLECT_MULTI)
        bundle2 = collector.normalize(MERAKI_COLLECT_MULTI)

        assert len(bundle1.devices) == len(bundle2.devices)
        assert len(bundle1.sensors) == len(bundle2.sensors)

        for d1, d2 in zip(bundle1.devices, bundle2.devices):
            assert d1.external_id == d2.external_id
            assert d1.name == d2.name
            assert d1.status == d2.status

    def test_no_hardcoded_values(self):
        """All device data comes from the payload."""
        collector = MerakiCollector(source=None)
        bundle = collector.normalize(MERAKI_COLLECT_SINGLE)

        for device in bundle.devices:
            # Verify device came from payload
            assert device.external_id in [d["serial"] for d in MERAKI_COLLECT_SINGLE["devices"]]
            # Vendor is hardcoded as "Cisco"
            assert device.vendor == "Cisco"
            # But name comes from payload
            payload_names = {d["name"] for d in MERAKI_COLLECT_SINGLE["devices"]}
            assert device.name in payload_names
