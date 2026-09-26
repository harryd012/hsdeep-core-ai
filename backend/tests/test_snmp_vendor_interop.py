"""SNMP provider interop tests (Cisco + Aruba, SNMP-first).

Decision (a): reuse the generic `snmp` provider - no vendor-specific
adapter files. Cisco and Aruba devices speak standard MIB-II, and the
dispatch path (catalog transport -> registry -> decrypted monitoring
source -> capability resolver) never branches on vendor. These tests
prove that a Cisco-identified source and an Aruba-identified source both
execute GET_STATUS / GET_INTERFACES / GET_FIRMWARE through the SAME
provider, with the vendor string carried only as device metadata.

All network I/O is faked at the SNMPCollector._get / _walk seam - no
live devices, no credentials on the wire.
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
from app.actions.providers.snmp import extract_firmware
from app.repositories.device_capability_repository import derive_capabilities

pytestmark = pytest.mark.asyncio

_OID_SYS_DESCR = ".1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME = ".1.3.6.1.2.1.1.5.0"
_OID_SYS_UPTIME = ".1.3.6.1.2.1.1.3.0"

_CISCO_DESCR = "Cisco IOS Software, C3750 Software (C3750-IPSERVICESK9-M), Version 15.2(4)E7"
_ARUBA_DESCR = "ArubaOS (MODEL: Aruba7216), Version 8.10.0.4"


def _target(vendor_source_type="snmp", hostname="10.0.0.21"):
    tid = _uuid.uuid4()
    return ActionTarget(
        tenant_id=tid, device_id=_uuid.uuid4(), name=f"{vendor_source_type}-host",
        ip_address=hostname,
        connection=SimpleNamespace(
            id=_uuid.uuid4(), tenant_id=tid, name="Vendor SNMP source",
            source_type=vendor_source_type, hostname=hostname, ip_address=hostname,
            port=161, protocol="snmp", snmp_version="v2c",
            username=None, api_key="public", password="public",
            config={}, verify_ssl=False,
        ),
    )


def _patch_snmp(monkeypatch, scalars, walks=None):
    async def fake_get(self, *oids):
        return {oid: scalars[oid] for oid in oids if oid in scalars}

    async def fake_walk(self, base_oid):
        return list((walks or {}).get(base_oid, []))

    monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", fake_get)
    monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)


@pytest.fixture
def provider():
    from app.actions.providers.snmp import SnmpActionProvider
    return SnmpActionProvider()


class TestSnmpRegistration:
    def test_snmp_registered_and_implemented(self):
        cls = ActionProviderRegistry.try_get("snmp")
        assert cls is not None and cls.implemented

    def test_vendor_health_adapters_exist_alongside_generic_snmp(self):
        # Phase 3: vendor MIB extensions (CISCO_QUERY_HEALTH / ARUBA_QUERY_HEALTH)
        # have their own adapters because MIB-II cannot answer them. The generic
        # provider still handles all MIB-II diagnostics for both vendors.
        assert ActionProviderRegistry.try_get("vendor:cisco") is not None
        assert ActionProviderRegistry.try_get("vendor:aruba") is not None
        assert ActionProviderRegistry.try_get("vendor:cisco").implemented
        assert ActionProviderRegistry.try_get("vendor:aruba").implemented

    def test_supported_actions(self, provider):
        for action in (
            DeviceActionType.GET_STATUS,
            DeviceActionType.GET_INTERFACES,
            DeviceActionType.GET_FIRMWARE,
        ):
            assert provider.supports(action)
        assert not provider.supports(DeviceActionType.PING)
        assert not provider.supports(DeviceActionType.GET_CONFIG_STATUS)


class TestCiscoSnmpInterop:
    async def test_get_status(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {
            _OID_SYS_DESCR: _CISCO_DESCR,
            _OID_SYS_NAME: "cisco-core-sw-01",
            _OID_SYS_UPTIME: "123456789",
        })
        result = await provider.execute(DeviceActionType.GET_STATUS, _target("cisco"), {})
        assert result.success
        assert result.output["sys_name"] == "cisco-core-sw-01"
        assert "Cisco" in result.output["sys_descr"]
        assert result.output["firmware"] == "15.2"  # conservative token, never guessed
        assert result.provider == "snmp"

    async def test_get_firmware(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {_OID_SYS_DESCR: _CISCO_DESCR})
        result = await provider.execute(DeviceActionType.GET_FIRMWARE, _target("cisco"), {})
        assert result.success
        assert result.output["firmware"] == "15.2"  # conservative token, never guessed

    async def test_get_interfaces(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {}, walks={
            ".1.3.6.1.2.1.2.2.1.2": [(".1.3.6.1.2.1.2.2.1.2.1", "GigabitEthernet1/0/1")],
            ".1.3.6.1.2.1.2.2.1.7": [(".1.3.6.1.2.1.2.2.1.7.1", "1")],
            ".1.3.6.1.2.1.2.2.1.8": [(".1.3.6.1.2.1.2.2.1.8.1", "1")],
        })
        result = await provider.execute(DeviceActionType.GET_INTERFACES, _target("cisco"), {})
        assert result.success
        assert result.output["count"] == 1
        assert result.output["interfaces"][0]["name"] == "GigabitEthernet1/0/1"
        assert result.output["interfaces"][0]["oper_status"] == "up"


class TestArubaSnmpInterop:
    async def test_get_status(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {
            _OID_SYS_DESCR: _ARUBA_DESCR,
            _OID_SYS_NAME: "aruba-ctrl-01",
            _OID_SYS_UPTIME: "987654321",
        })
        result = await provider.execute(DeviceActionType.GET_STATUS, _target("aruba"), {})
        assert result.success
        assert result.output["sys_name"] == "aruba-ctrl-01"
        assert "Aruba" in result.output["sys_descr"]
        assert result.output["firmware"] == "8.10.0.4"
        assert result.provider == "snmp"

    async def test_get_firmware(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {_OID_SYS_DESCR: _ARUBA_DESCR})
        result = await provider.execute(DeviceActionType.GET_FIRMWARE, _target("aruba"), {})
        assert result.success
        assert result.output["firmware"] == "8.10.0.4"

    async def test_get_interfaces(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {}, walks={
            ".1.3.6.1.2.1.2.2.1.2": [(".1.3.6.1.2.1.2.2.1.2.7", "ethernet7")],
            ".1.3.6.1.2.1.2.2.1.7": [(".1.3.6.1.2.1.2.2.1.7.7", "1")],
            ".1.3.6.1.2.1.2.2.1.8": [(".1.3.6.1.2.1.2.2.1.8.7", "2")],
        })
        result = await provider.execute(DeviceActionType.GET_INTERFACES, _target("aruba"), {})
        assert result.success
        assert result.output["count"] == 1
        assert result.output["interfaces"][0]["oper_status"] == "down"


class TestSnmpFailureModes:
    async def test_missing_connection_is_honest_unavailable(self, provider):
        target = _target()
        target.connection = None
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_STATUS, target, {})

    async def test_unreachable_device_is_failure(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {})
        result = await provider.execute(DeviceActionType.GET_STATUS, _target("cisco"), {})
        assert not result.success
        assert "no response" in result.error

    async def test_timeout_is_failure_not_crash(self, provider, monkeypatch):
        async def boom(self, *a, **k):
            raise TimeoutError("SNMP request timed out")

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", boom)
        result = await provider.execute(DeviceActionType.GET_STATUS, _target("aruba"), {})
        assert not result.success
        assert "SNMP query failed" in result.error

    async def test_auth_failure_is_failure(self, provider, monkeypatch):
        async def boom(self, *a, **k):
            raise PermissionError("SNMP authentication failure")

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", boom)
        result = await provider.execute(DeviceActionType.GET_STATUS, _target("cisco"), {})
        assert not result.success

    async def test_unsupported_action_raises(self, provider):
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_CONFIG_STATUS, _target("cisco"), {})

    async def test_result_never_echoes_community(self, provider, monkeypatch):
        _patch_snmp(monkeypatch, {
            _OID_SYS_DESCR: _CISCO_DESCR,
            _OID_SYS_NAME: "cisco-core-sw-01",
            _OID_SYS_UPTIME: "1",
        })
        result = await provider.execute(DeviceActionType.GET_STATUS, _target("cisco"), {})
        blob = json.dumps(result.output) + (result.error or "")
        assert "S3cret" not in blob


class TestSnmpCapabilityAndPolicy:
    def test_snmp_source_derives_supports_snmp(self):
        device = SimpleNamespace(ip_address="10.0.0.21", hostname="cisco-core-sw-01")
        for source_type in ("cisco", "aruba", "snmp"):
            source = SimpleNamespace(protocol="snmp", source_type=source_type, is_active=True)
            caps = derive_capabilities(device, source)
            assert caps["supports_snmp"] is True, source_type

    def test_inactive_source_derives_no_snmp(self):
        device = SimpleNamespace(ip_address="10.0.0.21", hostname="cisco-core-sw-01")
        source = SimpleNamespace(protocol="snmp", source_type="cisco", is_active=False)
        assert derive_capabilities(device, source)["supports_snmp"] is None

    def test_firmware_extraction_vendor_strings(self):
        # Conservative extractor: dotted numeric tokens only, never guessed.
        assert extract_firmware(_CISCO_DESCR) == "15.2"
        assert extract_firmware(_ARUBA_DESCR) == "8.10.0.4"
        assert extract_firmware("") is None
        assert extract_firmware("No version here") is None
