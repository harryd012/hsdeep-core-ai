"""
Unit tests for PRTGCollector.normalize().

Tests validate that raw PRTG API responses are correctly converted to
NormalizedBundle format without external I/O or database access.
"""
import pytest
from datetime import datetime, timezone

from app.collectors.prtg import PRTGCollector
from app.collectors.prtg import (
    _ACTIVE_MESSAGE_STATUS_CODES,
    _PRTG_STATUS_MAP,
    _derive_device_status,
    _normalize_sensor_status,
)
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


# ---------------------------------------------------------------------------
# PHASE 5C - PRTG status contract
#
# Regression cover for a severity inversion: PRTG sensor code 4 is Warning and
# code 5 is Down, but the map read (3:up, 4:down, 5:warning, 7:warning,
# 10:warning) - so every genuine Down sensor was published as "warning" and
# every Warning was escalated to "down". The canonical vocabulary stored on
# `Sensor.status` is exactly up | down | warning | unknown.
#
# Source of truth: Paessler PRTG "filter_status table" (Sensor States), which
# defines codes 1-14 as listed below.
# ---------------------------------------------------------------------------

#: PRTG's documented sensor status codes mapped to canonical `Sensor.status`.
PRTG_DOCUMENTED_SENSOR_STATUS = {
    1: "unknown",   # Unknown
    2: "unknown",   # Collecting
    3: "up",        # Up
    4: "warning",   # Warning
    5: "down",      # Down
    6: "unknown",   # NoProbe
    7: "unknown",   # Paused by user
    8: "unknown",   # Paused by dependency
    9: "unknown",   # Paused by schedule
    10: "warning",  # Unusual
    11: "unknown",  # Paused by license
    12: "unknown",  # Paused until
    13: "down",     # Down (Acknowledged)
    14: "down",     # Down (Partial)
}


def _normalize_one_sensor(**row):
    """Run one sensors-table row through the real normalize() path."""
    collector = PRTGCollector(source=None)
    bundle = collector.normalize({"devices": [], "sensors": [{"objid": "9001", **row}]})
    return bundle.sensors[0].status


class TestPRTGStatusContract:
    """The PRTG -> canonical mapping is a data contract: inverting warning and
    down turns a real outage into a benign warning, or vice versa."""

    def test_map_matches_prtg_documented_codes(self):
        assert _PRTG_STATUS_MAP == PRTG_DOCUMENTED_SENSOR_STATUS

    @pytest.mark.parametrize(
        "code,expected", sorted(PRTG_DOCUMENTED_SENSOR_STATUS.items())
    )
    def test_each_documented_code_normalizes(self, code, expected):
        assert _normalize_one_sensor(name=f"s{code}", status=code, status_raw=code) == expected

    def test_down_is_never_reported_as_warning(self):
        """Regression: code 5 = Down. Reported as "warning" it silently
        de-escalated every real outage."""
        assert _normalize_one_sensor(name="d", status=5, status_raw=5) == "down"
        assert _normalize_one_sensor(name="w", status=4, status_raw=4) == "warning"

    @pytest.mark.parametrize("code", [7, 8, 9, 11, 12])
    def test_deliberately_paused_sensors_are_not_alerting(self, code):
        assert _normalize_one_sensor(name="p", status=code, status_raw=code) == "unknown"

    def test_unrecognised_status_is_never_upgraded_to_an_alarm(self):
        assert _normalize_one_sensor(name="x", status="nonsense", status_raw="??") == "unknown"
        # A sensor row with no status at all must not look healthy.
        assert _normalize_one_sensor(name="y") == "unknown"

    @pytest.mark.parametrize(
        "row,expected",
        [
            ({"status": "5", "status_raw": "Down"}, "down"),      # digit string
            ({"status": 5, "status_raw": 5}, "down"),             # both numeric
            ({"status": "Down", "status_raw": "Down"}, "down"),   # both text
            ({"status": "Down (Acknowledged)", "status_raw": "Down"}, "down"),
            ({"status": "Down Partial", "status_raw": "Down Partial"}, "down"),
            ({"status": "Warning", "status_raw": "Warning"}, "warning"),
            ({"status": "Unusual", "status_raw": "Unusual"}, "warning"),
            ({"status": "Up", "status_raw": "Up"}, "up"),
            ({"status": "Up", "status_raw": 3}, "up"),
            ({"status": 5.0, "status_raw": None}, "down"),        # whole float
            ({"status": None, "status_raw": None}, "unknown"),
            ({"status": "", "status_raw": ""}, "unknown"),
            ({}, "unknown"),
        ],
    )
    def test_column_and_type_variants_all_resolve(self, row, expected):
        """Which of `status`/`status_raw` holds the code, and whether it is
        numeric or text, differs between PRTG API surfaces and versions - so
        every documented spelling must resolve rather than silently collapsing
        to "unknown"."""
        assert _normalize_sensor_status(row) == expected

    def test_device_status_shares_the_sensor_vocabulary(self):
        assert _derive_device_status("Up") == "up"
        assert _derive_device_status("Down (Acknowledged)") == "down"
        assert _derive_device_status("Warning") == "warning"
        # PRTG reports "Unusual" for devices too; the sensor path maps it to
        # "warning", so the device path must not disagree.
        assert _derive_device_status("Unusual") == "warning"
        # An operator-paused device is not an alarm.
        assert _derive_device_status("Paused") == "unknown"
        assert _derive_device_status(None) == "unknown"

    def test_message_filter_uses_prtg_log_status_scale(self):
        """`filter_status` on the messages table takes PRTG's LOG status
        values, not the sensor codes 1-14. The alarm codes on that scale are
        608 (Down), 609 (Warning), 610 (Unusual), 619 (Down Acknowledged) and
        620 (Down Partial)."""
        codes = {int(code) for code in _ACTIVE_MESSAGE_STATUS_CODES.split(",")}
        assert codes == {608, 609, 610, 619, 620}
        # On the log scale the sensor ids would mean "Probe Denied (IP)",
        # "Probe Denied (Key)", "Web Server" and "Data" - system noise, not
        # active alarms - so they must not appear here.
        assert not codes & set(PRTG_DOCUMENTED_SENSOR_STATUS)
