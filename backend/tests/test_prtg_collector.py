"""
Unit tests for PRTGCollector.normalize().

Tests validate that raw PRTG API responses are correctly converted to
NormalizedBundle format without external I/O or database access.
"""
import pytest
from datetime import datetime, timezone

from app.collectors.prtg import PRTGCollector
from app.collectors.base import NormalizedBundle
from tests.fixtures.prtg_payloads import (
    PRTG_COLLECT_EMPTY,
    PRTG_COLLECT_SINGLE,
    PRTG_COLLECT_MULTI,
    PRTG_COLLECT_WITH_ALARM,
)


class TestPRTGNormalize:
    """Test PRTGCollector.normalize() pure function."""

    def test_empty_payload(self):
        """Empty PRTG response normalizes to empty bundle."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_EMPTY)

        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 0
        assert len(bundle.sensors) == 0
        assert len(bundle.metrics) == 0
        assert len(bundle.alerts) == 0

    def test_single_device_single_sensor(self):
        """Single device with CPU sensor normalizes correctly."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_SINGLE)

        assert len(bundle.devices) == 1
        device = bundle.devices[0]
        assert device.external_id == "10001"
        assert device.name == "Router-Core-1"
        assert device.hostname == "192.168.1.1"
        assert device.status == "up"
        assert device.site_hint == "Local Network"

        assert len(bundle.sensors) == 1
        sensor = bundle.sensors[0]
        assert sensor.external_id == "20001"
        assert sensor.name == "CPU"
        assert sensor.device_external_id == "10001"
        assert sensor.value == 45.2
        assert sensor.status == "up"

    def test_multiple_devices(self):
        """Multiple devices with multiple sensors normalize correctly."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_MULTI)

        assert len(bundle.devices) == 2
        device_ids = {d.external_id for d in bundle.devices}
        assert device_ids == {"10001", "10002"}

        assert len(bundle.sensors) == 3
        sensor_names = {s.name for s in bundle.sensors}
        assert sensor_names == {"Eth0/0", "Eth0/1", "CPU"}

        # Verify device-sensor relationships
        cpu_sensor = next(s for s in bundle.sensors if s.name == "CPU")
        assert cpu_sensor.device_external_id == "10002"  # Firewall-1

        eth0_sensor = next(s for s in bundle.sensors if s.name == "Eth0/0")
        assert eth0_sensor.device_external_id == "10001"  # Switch-1

    def test_metrics_from_channels(self):
        """Channel data converts to metrics."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_SINGLE)

        # Channels should produce metrics
        channel_metrics = [m for m in bundle.metrics if m.metric_type == "prtg_channel"]
        assert len(channel_metrics) > 0

        cpu_metric = next(m for m in channel_metrics if m.sensor_external_id == "20001")
        assert cpu_metric.value == 45.2
        assert cpu_metric.unit == "%"

    def test_metrics_from_historic_data(self):
        """Historic data converts to metrics."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_SINGLE)

        # Historic data should produce metrics
        historic_metrics = [m for m in bundle.metrics if "historic" in m.metric_type]
        assert len(historic_metrics) == 2  # 2 historic points

        for metric in historic_metrics:
            assert metric.sensor_external_id == "20001"
            assert metric.value in (42.0, 45.2)

    def test_vendor_native_alert_signal(self):
        """PRTG messages convert to vendor-native alert signals."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_WITH_ALARM)

        assert len(bundle.alerts) == 1
        alert = bundle.alerts[0]
        assert alert.external_id == "40001"
        assert alert.title == "Sensor Down"
        assert alert.severity == "critical"
        assert alert.device_external_id == "10001"  # Links to device via name lookup
        assert "down" in alert.description.lower()

    def test_status_mapping(self):
        """PRTG status codes map to normalized statuses."""
        collector = PRTGCollector(source=None)

        # Device status should be "up" (status=3 maps to "up")
        bundle = collector.normalize(PRTG_COLLECT_SINGLE)
        device = bundle.devices[0]
        assert device.status == "up"

        # Sensor status should be "up" (status_raw=3 maps to "up")
        sensor = bundle.sensors[0]
        assert sensor.status == "up"

    def test_no_hardcoded_values(self):
        """Normalized data should only contain values from payload."""
        collector = PRTGCollector(source=None)
        bundle = collector.normalize(PRTG_COLLECT_SINGLE)

        # Every device should come from the payload
        for device in bundle.devices:
            assert device.external_id in PRTG_COLLECT_SINGLE["devices"][0]["objid"].__str__()

        # Every sensor should reference a real device
        for sensor in bundle.sensors:
            if sensor.device_external_id:
                assert any(
                    d.external_id == sensor.device_external_id
                    for d in bundle.devices
                )

    def test_normalize_is_pure_function(self):
        """Calling normalize() twice with same input produces same output."""
        collector = PRTGCollector(source=None)

        bundle1 = collector.normalize(PRTG_COLLECT_SINGLE)
        bundle2 = collector.normalize(PRTG_COLLECT_SINGLE)

        assert len(bundle1.devices) == len(bundle2.devices)
        assert len(bundle1.sensors) == len(bundle2.sensors)
        assert len(bundle1.metrics) == len(bundle2.metrics)
        assert len(bundle1.alerts) == len(bundle2.alerts)

        # Verify exact equality of first device
        assert bundle1.devices[0].external_id == bundle2.devices[0].external_id
        assert bundle1.devices[0].name == bundle2.devices[0].name
        assert bundle1.devices[0].status == bundle2.devices[0].status
