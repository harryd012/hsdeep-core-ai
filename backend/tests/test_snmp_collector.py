"""
Unit tests for SNMPCollector.normalize() + pure parsing helpers — Task 11.14.

Pure, no I/O, no SNMP traffic. Validates raw ifTable walk data →
NormalizedBundle mapping.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.collectors.snmp import SNMPCollector, _safe_float
from app.collectors.base import NormalizedBundle

pytestmark = pytest.mark.asyncio

_EMPTY_RAW = {
    "sys_descr": "",
    "sys_name": "",
    "interfaces": {},
}

_FULL_RAW = {
    "sys_descr": "Cisco IOS Software, Version 15.2(7)E2",
    "sys_name": "core-sw-01",
    "interfaces": {
        "descr":        {"1": "GigabitEthernet0/0", "2": "GigabitEthernet0/1", "3": "Loopback0"},
        "admin_status": {"1": "1", "2": "1",          "3": "1"},
        "oper_status":  {"1": "1", "2": "2",          "3": "1"},   # 2 = gi0/1 is down
        "speed":        {"1": "1000000000", "2": "1000000000", "3": "0"},
        "in_octets":    {"1": "12345678", "2": "987654",      "3": "0"},
        "out_octets":   {"1": "87654321", "2": "123456",      "3": "0"},
        "in_errors":    {"1": "0",        "2": "5",           "3": "0"},
        "out_errors":   {"1": "0",        "2": "3",           "3": "0"},
    },
}


def _make_source(hostname: str = "192.168.1.1") -> SimpleNamespace:
    return SimpleNamespace(
        hostname=hostname,
        ip_address=None,
        port=161,
        snmp_version="v2c",
        username=None,
        password=None,
        api_key="public",
        verify_ssl=False,
    )


class TestSNMPNormalize:

    def test_empty_payload_returns_single_device_no_interfaces(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 1
        assert len(bundle.interfaces) == 0

    def test_device_uses_sys_name_when_available(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        assert bundle.devices[0].name == "core-sw-01"

    def test_device_falls_back_to_hostname_when_no_sys_name(self):
        collector = SNMPCollector(source=_make_source("10.0.0.1"))
        bundle = collector.normalize({**_FULL_RAW, "sys_name": ""})
        assert bundle.devices[0].name == "10.0.0.1"

    def test_interface_count_matches_ifTable(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        assert len(bundle.interfaces) == 3

    def test_oper_status_one_maps_to_up(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        gi0_0 = next(i for i in bundle.interfaces if i.name == "GigabitEthernet0/0")
        assert gi0_0.oper_status == "up"
        assert gi0_0.admin_status == "up"

    def test_oper_status_two_maps_to_down(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        gi0_1 = next(i for i in bundle.interfaces if i.name == "GigabitEthernet0/1")
        assert gi0_1.oper_status == "down"

    def test_speed_converted_to_mbps(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        gi0_0 = next(i for i in bundle.interfaces if i.name == "GigabitEthernet0/0")
        assert gi0_0.speed_mbps == pytest.approx(1000.0)

    def test_zero_speed_returns_none(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        lo = next(i for i in bundle.interfaces if i.name == "Loopback0")
        assert lo.speed_mbps is None

    def test_octet_counters_preserved(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        gi0_0 = next(i for i in bundle.interfaces if i.name == "GigabitEthernet0/0")
        assert gi0_0.in_octets == 12345678.0
        assert gi0_0.out_octets == 87654321.0

    def test_error_counters_preserved(self):
        collector = SNMPCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        gi0_1 = next(i for i in bundle.interfaces if i.name == "GigabitEthernet0/1")
        assert gi0_1.in_errors == 5.0
        assert gi0_1.out_errors == 3.0

    def test_normalize_is_pure(self):
        collector = SNMPCollector(source=_make_source())
        b1 = collector.normalize(_FULL_RAW)
        b2 = collector.normalize(_FULL_RAW)
        assert len(b1.interfaces) == len(b2.interfaces)

    def test_safe_float_handles_none_and_invalid(self):
        assert _safe_float(None) is None
        assert _safe_float("abc") is None
        assert _safe_float("123") == 123.0
        assert _safe_float(0) == 0.0
