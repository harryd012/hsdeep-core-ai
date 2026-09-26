"""Cisco IOS vendor-MIB health provider unit tests (Phase 3).

Pure unit tests: SNMP transport is faked at the SNMPCollector._get/_walk
seam - no network, no credentials on the wire. Exercises the Cisco OID map
(CISCO-PROCESS-MIB + CISCO-MEMORY-POOL-MIB), the reading parser, the shared
envelope (timing, failure contract), and the ActionResult hygiene.
"""
from __future__ import annotations

import json
import uuid as _uuid
from types import SimpleNamespace

import pytest

import app.actions  # noqa: F401 - registers providers
from app.actions.base import (
    ActionNotSupported,
    ActionProviderRegistry,
    ActionTarget,
    DeviceActionType,
)

pytestmark = pytest.mark.asyncio

_CPU_1MIN = ".1.3.6.1.4.1.9.9.109.1.1.1.1.7"
_CPU_5MIN = ".1.3.6.1.4.1.9.9.109.1.1.1.1.8"
_MEM_NAME = ".1.3.6.1.4.1.9.9.48.1.1.1.2"
_MEM_USED = ".1.3.6.1.4.1.9.9.48.1.1.1.5"
_MEM_FREE = ".1.3.6.1.4.1.9.9.48.1.1.1.6"


def _target(hostname="10.0.1.11"):
    tid = _uuid.uuid4()
    return ActionTarget(
        tenant_id=tid, device_id=_uuid.uuid4(), name="cisco-core-sw-01",
        ip_address=hostname,
        connection=SimpleNamespace(
            id=_uuid.uuid4(), tenant_id=tid, name="Cisco SNMP source",
            source_type="snmp", hostname=hostname, ip_address=hostname,
            port=161, protocol="snmp", snmp_version="v2c",
            username=None, api_key="public", password="public",
            config={}, verify_ssl=False,
        ),
    )


def _patch(monkeypatch, walks):
    async def fake_walk(self, base_oid):
        return list(walks.get(base_oid, []))

    monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)


@pytest.fixture
def provider():
    from app.actions.providers.cisco import CiscoActionProvider
    return CiscoActionProvider()


class TestCiscoRegistration:
    def test_registered_and_implemented(self):
        cls = ActionProviderRegistry.try_get("vendor:cisco")
        assert cls is not None and cls.implemented

    def test_supports_only_own_health_action(self, provider):
        assert provider.supports(DeviceActionType.CISCO_QUERY_HEALTH)
        assert not provider.supports(DeviceActionType.ARUBA_QUERY_HEALTH)
        assert not provider.supports(DeviceActionType.GET_STATUS)


class TestCiscoHealthReads:
    CPU_WALKS = {
        _CPU_1MIN: [(_CPU_1MIN + ".1", "17")],
        _CPU_5MIN: [(_CPU_5MIN + ".1", "12")],
    }
    MEM_WALKS = {
        _MEM_NAME: [(_MEM_NAME + ".1", "Processor")],
        _MEM_USED: [(_MEM_USED + ".1", "412345678")],
        _MEM_FREE: [(_MEM_FREE + ".1", "587654322")],
    }

    async def test_full_health(self, provider, monkeypatch):
        _patch(monkeypatch, {**self.CPU_WALKS, **self.MEM_WALKS})
        result = await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, _target(), {})
        assert result.success
        assert result.output["vendor"] == "cisco"
        assert result.output["cpus"] == [
            {"index": "1", "cpu_1min_pct": 17, "cpu_5min_pct": 12}
        ]
        pool = result.output["memory_pools"][0]
        assert pool["name"] == "Processor"
        assert pool["used_bytes"] == 412345678
        assert pool["free_bytes"] == 587654322
        assert abs(pool["used_pct"] - 41.2) < 0.1
        assert result.provider == "vendor:cisco"

    async def test_cpu_only_when_no_memory(self, provider, monkeypatch):
        _patch(monkeypatch, dict(self.CPU_WALKS))
        result = await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, _target(), {})
        assert result.success
        assert result.output["cpu_count"] == 1
        assert result.output["memory_pool_count"] == 0

    async def test_no_vendor_data_is_honest_failure(self, provider, monkeypatch):
        _patch(monkeypatch, {})
        result = await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, _target(), {})
        assert not result.success
        assert "no vendor health data" in result.error


class TestCiscoFailureModes:
    async def test_missing_connection_is_unavailable(self, provider):
        target = _target()
        target.connection = None
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, target, {})

    async def test_timeout_is_failure(self, provider, monkeypatch):
        async def boom(self, *a, **k):
            raise TimeoutError("SNMP request timed out")

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", boom)
        result = await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, _target(), {})
        assert not result.success
        assert "SNMP query failed" in result.error

    async def test_non_numeric_readings_are_dropped(self, provider, monkeypatch):
        _patch(monkeypatch, {
            _CPU_1MIN: [(_CPU_1MIN + ".1", "N/A")],
            _CPU_5MIN: [],
        })
        result = await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, _target(), {})
        assert not result.success  # no usable readings -> honest failure

    async def test_wrong_action_raises(self, provider):
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_STATUS, _target(), {})

    async def test_result_has_timing_and_no_secrets(self, provider, monkeypatch):
        _patch(monkeypatch, {**TestCiscoHealthReads.CPU_WALKS,
                             **TestCiscoHealthReads.MEM_WALKS})
        result = await provider.execute(DeviceActionType.CISCO_QUERY_HEALTH, _target(), {})
        blob = json.dumps(result.output)
        assert "public" not in blob
        assert result.started_at is not None and result.completed_at is not None
