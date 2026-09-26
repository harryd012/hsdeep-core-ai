"""Aruba ArubaOS vendor-MIB health provider unit tests (Phase 3).

Pure unit tests: SNMP transport is faked at the SNMPCollector._get/_walk
seam - no network, no credentials on the wire. Covers the WLSX-SYSTEMEXT-MIB
OID map, the dedicated-memory-table + storage-RAM fallback paths, the shared
envelope, and the ActionResult hygiene.
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

_PROC_DESCR = ".1.3.6.1.4.1.14823.2.2.1.2.1.13.1.2"
_PROC_LOAD = ".1.3.6.1.4.1.14823.2.2.1.2.1.13.1.3"
_MEM_SIZE = ".1.3.6.1.4.1.14823.2.2.1.2.1.15.1.2"
_MEM_USED = ".1.3.6.1.4.1.14823.2.2.1.2.1.15.1.3"
_STOR_TYPE = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.2"
_STOR_SIZE = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.3"
_STOR_USED = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.4"
_STOR_NAME = ".1.3.6.1.4.1.14823.2.2.1.2.1.14.1.5"


def _target(hostname="10.0.2.21"):
    tid = _uuid.uuid4()
    return ActionTarget(
        tenant_id=tid, device_id=_uuid.uuid4(), name="aruba-ctrl-01",
        ip_address=hostname,
        connection=SimpleNamespace(
            id=_uuid.uuid4(), tenant_id=tid, name="Aruba SNMP source",
            source_type="snmp", hostname=hostname, ip_address=hostname,
            port=161, protocol="snmp", snmp_version="v2c",
            username=None, api_key="public", password="public",
            config={}, verify_ssl=False,
        ),
    )


def _patch(monkeypatch, scalars=None, walks=None):
    scalars = scalars or {}
    walks = walks or {}

    async def fake_get(self, *oids):
        return {oid: scalars[oid] for oid in oids if oid in scalars}

    async def fake_walk(self, base_oid):
        return list(walks.get(base_oid, []))

    monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", fake_get)
    monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)


@pytest.fixture
def provider():
    from app.actions.providers.aruba import ArubaActionProvider
    return ArubaActionProvider()


class TestArubaRegistration:
    def test_registered_and_implemented(self):
        cls = ActionProviderRegistry.try_get("vendor:aruba")
        assert cls is not None and cls.implemented

    def test_supports_only_own_health_action(self, provider):
        assert provider.supports(DeviceActionType.ARUBA_QUERY_HEALTH)
        assert not provider.supports(DeviceActionType.CISCO_QUERY_HEALTH)
        assert not provider.supports(DeviceActionType.GET_STATUS)


class TestArubaHealthReads:
    CPU_WALKS = {
        _PROC_DESCR: [(_PROC_DESCR + ".1", "Control Processor")],
        _PROC_LOAD: [(_PROC_LOAD + ".1", "23")],
    }

    async def test_full_health_memory_table(self, provider, monkeypatch):
        _patch(
            monkeypatch,
            scalars={_MEM_SIZE: "8388608", _MEM_USED: "4194304"},
            walks=dict(self.CPU_WALKS),
        )
        result = await provider.execute(DeviceActionType.ARUBA_QUERY_HEALTH, _target(), {})
        assert result.success
        assert result.output["vendor"] == "aruba"
        proc = result.output["processors"][0]
        assert proc["load_pct"] == 23
        assert proc["descr"] == "Control Processor"
        mem = result.output["memory"]
        assert mem["source"] == "wlsxSysExtMemoryTable"
        assert mem["unit"] == "KB"
        assert mem["total"] == 8388608
        assert mem["used"] == 4194304
        assert mem["free"] == 4194304
        assert mem["used_pct"] == 50.0
        assert result.provider == "vendor:aruba"

    async def test_storage_ram_fallback(self, provider, monkeypatch):
        _patch(
            monkeypatch,
            scalars={},
            walks={
                **self.CPU_WALKS,
                _STOR_TYPE: [(_STOR_TYPE + ".1", "1")],
                _STOR_SIZE: [(_STOR_SIZE + ".1", "4096")],
                _STOR_USED: [(_STOR_USED + ".1", "1024")],
                _STOR_NAME: [(_STOR_NAME + ".1", "ramdisk")],
            },
        )
        result = await provider.execute(DeviceActionType.ARUBA_QUERY_HEALTH, _target(), {})
        assert result.success
        mem = result.output["memory"]
        assert mem["source"] == "wlsxSysExtStorageTable"
        assert mem["unit"] == "MB"
        assert mem["used_pct"] == 25.0

    async def test_no_vendor_data_is_honest_failure(self, provider, monkeypatch):
        _patch(monkeypatch)
        result = await provider.execute(DeviceActionType.ARUBA_QUERY_HEALTH, _target(), {})
        assert not result.success
        assert "no vendor health data" in result.error


class TestArubaFailureModes:
    async def test_missing_connection_is_unavailable(self, provider):
        target = _target()
        target.connection = None
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.ARUBA_QUERY_HEALTH, target, {})

    async def test_timeout_is_failure(self, provider, monkeypatch):
        async def boom(self, *a, **k):
            raise TimeoutError("SNMP request timed out")

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", boom)
        result = await provider.execute(DeviceActionType.ARUBA_QUERY_HEALTH, _target(), {})
        assert not result.success
        assert "SNMP query failed" in result.error

    async def test_wrong_action_raises(self, provider):
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_STATUS, _target(), {})

    async def test_result_has_timing_and_no_secrets(self, provider, monkeypatch):
        _patch(
            monkeypatch,
            scalars={_MEM_SIZE: "1000", _MEM_USED: "250"},
            walks=dict(TestArubaHealthReads.CPU_WALKS),
        )
        result = await provider.execute(DeviceActionType.ARUBA_QUERY_HEALTH, _target(), {})
        blob = json.dumps(result.output)
        assert "public" not in blob
        assert result.started_at is not None and result.completed_at is not None
