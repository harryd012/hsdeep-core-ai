"""
Unit tests for LinuxSSHCollector.normalize() + pure parsing helpers — Task 11.16.

Pure, no I/O, no SSH connections. Validates simulated command output →
NormalizedBundle mapping plus all parsing helper edge cases.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.collectors.linux_ssh import (
    LinuxSSHCollector,
    _parse_df,
    _parse_memory_pct,
    _parse_netdev,
    _parse_os_version,
    _safe_float,
)
from app.collectors.base import NormalizedBundle

pytestmark = pytest.mark.asyncio

_EMPTY_RAW = {"uname": "", "hostname": "", "loadavg": "", "meminfo": "", "df": "", "netdev": ""}

_FULL_RAW = {
    "uname": "Linux web-01 5.15.0-89-generic #99-Ubuntu SMP Mon Oct 30 20:42:41 UTC 2023 x86_64 x86_64 x86_64 GNU/Linux",
    "hostname": "web-01",
    "loadavg": "0.52 0.45 0.41 2/512 12345",
    "meminfo": (
        "              total        used        free      shared  buff/cache   available\n"
        "Mem:     8368979968  2516582400  4295294976    524288  1557102592  5611438080\n"
        "Swap:    2147479552           0  2147479552\n"
    ),
    "df": (
        "Filesystem      1B-blocks       Used  Available Use%\n"
        "/               107374182400  53687091200  53687091200  50%\n"
        "/data           214748364800  32212254720 182536110080  15%\n"
    ),
    "netdev": (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  eth0: 12345678  100000    5    0    0     0          0         0 87654321   80000    3    0    0     0       0          0\n"
        "  eth1:  9876543   50000    0    0    0     0          0         0  5432100   40000    0    0    0     0       0          0\n"
        "    lo:   100000    1000    0    0    0     0          0         0   100000    1000    0    0    0     0       0          0\n"
    ),
}


def _make_source(hostname: str = "192.168.1.50") -> SimpleNamespace:
    return SimpleNamespace(
        hostname=hostname,
        ip_address=None,
        port=22,
        username="admin",
        password="secret",
        api_key=None,
        verify_ssl=False,
    )


class TestLinuxSSHNormalize:

    def test_empty_payload_returns_single_device_no_sensors(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 1
        assert len(bundle.sensors) == 0
        assert len(bundle.interfaces) == 0

    def test_device_name_from_hostname_output(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        assert bundle.devices[0].name == "web-01"
        assert bundle.devices[0].hostname == "web-01"

    def test_device_falls_back_to_source_hostname(self):
        collector = LinuxSSHCollector(source=_make_source("10.0.0.99"))
        bundle = collector.normalize({**_FULL_RAW, "hostname": ""})
        assert bundle.devices[0].hostname == "10.0.0.99"

    def test_os_version_from_uname(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        assert "5.15.0-89-generic" in (bundle.devices[0].os_version or "")

    def test_load_average_sensor_created(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        load_sensors = [s for s in bundle.sensors if s.metric_type == "load_avg_1m"]
        assert len(load_sensors) == 1
        assert load_sensors[0].value == pytest.approx(0.52)

    def test_memory_sensor_created(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        mem_sensors = [s for s in bundle.sensors if s.metric_type == "memory_usage"]
        assert len(mem_sensors) == 1
        # 2516582400 / 8368979968 ≈ 30.07%
        assert 25.0 < mem_sensors[0].value < 40.0

    def test_disk_sensors_created_per_mount(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        disk_sensors = [s for s in bundle.sensors if s.metric_type == "disk_usage"]
        assert len(disk_sensors) == 2

    def test_network_interfaces_excluding_loopback(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        iface_names = {i.name for i in bundle.interfaces}
        assert "eth0" in iface_names
        assert "eth1" in iface_names
        assert "lo" not in iface_names  # loopback excluded

    def test_interface_counters_correct(self):
        collector = LinuxSSHCollector(source=_make_source())
        bundle = collector.normalize(_FULL_RAW)
        eth0 = next(i for i in bundle.interfaces if i.name == "eth0")
        assert eth0.in_octets == 12345678.0
        assert eth0.out_octets == 87654321.0
        assert eth0.in_errors == 5.0
        assert eth0.out_errors == 3.0

    def test_normalize_is_pure(self):
        collector = LinuxSSHCollector(source=_make_source())
        b1 = collector.normalize(_FULL_RAW)
        b2 = collector.normalize(_FULL_RAW)
        assert len(b1.sensors) == len(b2.sensors)
        assert len(b1.interfaces) == len(b2.interfaces)


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------

class TestParseOsVersion:
    def test_extracts_kernel_from_uname(self):
        uname = "Linux host 5.15.0-89-generic #99-Ubuntu x86_64 GNU/Linux"
        assert _parse_os_version(uname) == "5.15.0-89-generic"

    def test_empty_returns_none(self):
        assert _parse_os_version("") is None

    def test_short_string_returns_truncated(self):
        assert _parse_os_version("Linux") is not None


class TestParseMemoryPct:
    def test_standard_free_output(self):
        meminfo = (
            "              total        used        free\n"
            "Mem:     8000000000  4000000000  4000000000\n"
        )
        result = _parse_memory_pct(meminfo)
        assert result == pytest.approx(50.0)

    def test_empty_returns_none(self):
        assert _parse_memory_pct("") is None

    def test_no_mem_line_returns_none(self):
        assert _parse_memory_pct("Swap: 1000 500 500\n") is None


class TestParseDf:
    def test_parses_mount_and_percent(self):
        df = (
            "Filesystem 1B-blocks Used Available Use%\n"
            "/ 100 60 40 60%\n"
            "/data 200 50 150 25%\n"
        )
        result = _parse_df(df)
        assert result["/"] == 60.0
        assert result["/data"] == 25.0

    def test_empty_returns_empty_dict(self):
        assert _parse_df("") == {}


class TestParseNetdev:
    def test_excludes_loopback(self):
        netdev = (
            "header1\nheader2\n"
            "  lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0\n"
            " eth0: 9999 100 2 0 0 0 0 0 8888 80 1 0 0 0 0 0\n"
        )
        result = _parse_netdev(netdev)
        assert "lo" not in result
        assert "eth0" in result

    def test_parses_in_out_octets_and_errors(self):
        netdev = (
            "h1\nh2\n"
            " eth0: 12345 100 5 0 0 0 0 0 67890 80 3 0 0 0 0 0\n"
        )
        result = _parse_netdev(netdev)
        assert result["eth0"]["in_octets"] == 12345.0
        assert result["eth0"]["out_octets"] == 67890.0
        assert result["eth0"]["in_errors"] == 5.0
        assert result["eth0"]["out_errors"] == 3.0

    def test_empty_returns_empty_dict(self):
        assert _parse_netdev("") == {}


class TestSafeFloat:
    def test_valid_string(self):
        assert _safe_float("3.14") == 3.14

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_invalid_string_returns_none(self):
        assert _safe_float("not-a-number") is None

    def test_integer_input(self):
        assert _safe_float(42) == 42.0
