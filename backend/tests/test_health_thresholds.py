"""Threshold-based health evaluation tests (Phase 3).

Pure unit tests over app.actions.health_thresholds plus its integration
through VendorSnmpHealthProvider.execute(). No network, no DB, no OIDs.
"""
from __future__ import annotations

import uuid as _uuid
from types import SimpleNamespace

import pytest

import app.actions  # noqa: F401 - registers providers
from app.actions.base import ActionTarget, DeviceActionType
from app.actions.health_thresholds import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARNING,
    evaluate_health,
    evaluate_threshold,
    extract_readings,
)

pytestmark = pytest.mark.asyncio


class TestEvaluateThreshold:
    def test_ok_below_warning(self):
        assert evaluate_threshold(50.0, 80.0, 95.0) == STATUS_OK

    def test_warning_at_and_above_threshold(self):
        assert evaluate_threshold(80.0, 80.0, 95.0) == STATUS_WARNING
        assert evaluate_threshold(94.9, 80.0, 95.0) == STATUS_WARNING

    def test_critical_at_and_above_threshold(self):
        assert evaluate_threshold(95.0, 80.0, 95.0) == STATUS_CRITICAL
        assert evaluate_threshold(99.0, 80.0, 95.0) == STATUS_CRITICAL

    def test_no_thresholds_is_ok(self):
        assert evaluate_threshold(99.0, None, None) == STATUS_OK


class TestExtractReadings:
    def test_cisco_shape(self):
        output = {
            "cpus": [{"index": "1", "cpu_1min_pct": 17, "cpu_5min_pct": 12}],
            "memory_pools": [
                {"index": "1", "name": "Processor", "used_pct": 41.2},
                {"index": "2", "name": "I/O", "used_pct": None},
            ],
        }
        readings = dict(extract_readings(output))
        assert readings["cpu.1.cpu_1min_pct"] == 17.0
        assert readings["cpu.1.cpu_5min_pct"] == 12.0
        assert readings["memory_pool.Processor.used_pct"] == 41.2
        assert not any("pool2" in k for k in readings)  # None never surfaces

    def test_aruba_shape(self):
        output = {
            "processors": [{"index": "1", "load_pct": 23}],
            "memory": {"used_pct": 50.0},
        }
        readings = dict(extract_readings(output))
        assert readings["processor.1.load_pct"] == 23.0
        assert readings["memory.used_pct"] == 50.0

    def test_empty_payload_has_no_readings(self):
        assert extract_readings({}) == []


class TestEvaluateHealth:
    def test_all_ok(self):
        out = evaluate_health({"processors": [{"index": "1", "load_pct": 10}]})
        assert out["health_status"] == STATUS_OK
        assert out["verdicts"][0]["status"] == STATUS_OK

    def test_worst_wins_critical_over_warning(self):
        out = evaluate_health({
            "cpus": [{"index": "1", "cpu_5min_pct": 96}],
            "memory_pools": [{"index": "1", "name": "P", "used_pct": 90}],
        })
        assert out["health_status"] == STATUS_CRITICAL

    def test_warning_only(self):
        out = evaluate_health({"cpus": [{"index": "1", "cpu_5min_pct": 85}]})
        assert out["health_status"] == STATUS_WARNING

    def test_overrides_lower_thresholds(self):
        out = evaluate_health(
            {"processors": [{"index": "1", "load_pct": 75}]},
            {"cpu": {"warning": 70, "critical": 90}},
        )
        assert out["verdicts"][0]["status"] == STATUS_WARNING
        assert out["verdicts"][0]["warning"] == 70.0

    def test_no_readings_is_unknown(self):
        out = evaluate_health({})
        assert out["health_status"] == STATUS_UNKNOWN
        assert out["verdicts"] == []


def _target():
    tid = _uuid.uuid4()
    return ActionTarget(
        tenant_id=tid, device_id=_uuid.uuid4(), name="vendor-host",
        ip_address="10.9.9.9",
        connection=SimpleNamespace(
            id=_uuid.uuid4(), tenant_id=tid, name="src", source_type="snmp",
            hostname="10.9.9.9", ip_address="10.9.9.9", port=161,
            protocol="snmp", snmp_version="v2c", username=None,
            api_key="public", password="public", config={}, verify_ssl=False,
        ),
    )


class TestEnvelopeIntegration:
    async def test_aruba_result_carries_threshold_evaluation(self, monkeypatch):
        from app.actions.providers.aruba import ArubaActionProvider

        load_oid = ".1.3.6.1.4.1.14823.2.2.1.2.1.13.1.3"

        async def fake_walk(self, base_oid):
            rows = {load_oid: [(load_oid + ".1", "97")]}
            return rows.get(base_oid, [])

        async def fake_get(self, *oids):
            return {}

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)
        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", fake_get)
        result = await ArubaActionProvider().execute(
            DeviceActionType.ARUBA_QUERY_HEALTH, _target(), {}
        )
        assert result.success
        evaluation = result.output["threshold_evaluation"]
        assert evaluation["health_status"] == STATUS_CRITICAL
        assert any(v["status"] == STATUS_CRITICAL for v in evaluation["verdicts"])

    async def test_cisco_result_with_default_thresholds_is_ok(self, monkeypatch):
        from app.actions.providers.cisco import CiscoActionProvider

        cpu_oid = ".1.3.6.1.4.1.9.9.109.1.1.1.1.7"

        async def fake_walk(self, base_oid):
            return [(cpu_oid + ".1", "20")] if base_oid == cpu_oid else []

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)
        result = await CiscoActionProvider().execute(
            DeviceActionType.CISCO_QUERY_HEALTH, _target(), {}
        )
        assert result.success
        assert result.output["threshold_evaluation"]["health_status"] == STATUS_OK

    async def test_malformed_threshold_override_never_fails_the_read(self, monkeypatch):
        from app.actions.providers.cisco import CiscoActionProvider

        cpu_oid = ".1.3.6.1.4.1.9.9.109.1.1.1.1.7"

        async def fake_walk(self, base_oid):
            return [(cpu_oid + ".1", "20")] if base_oid == cpu_oid else []

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)
        result = await CiscoActionProvider().execute(
            DeviceActionType.CISCO_QUERY_HEALTH, _target(),
            {"thresholds": {"cpu": "not-a-dict"}},
        )
        assert result.success  # the read still succeeds
        assert result.output["threshold_evaluation"]["health_status"] == STATUS_OK