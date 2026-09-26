"""
Tests for the Windows WinRM collector, profiles, circuit breaker, and
discovery. All WinRM I/O is mocked — no real Windows host is required.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.collectors.base import CollectorConfigError, ConnectionCheck
from app.collectors.windows_profiles import select_profile, WINDOWS_PROFILES
from app.collectors.windows_winrm import (
    CircuitOpenError,
    WindowsWinRMCollector,
    _HostCircuitBreaker,
    reset_circuits,
)
from app.services.monitoring.windows_discovery import (
    discover_windows_host,
    WindowsDiscoveryError,
)

#: Sentinel distinguishing "key absent" from "key present but null" in payloads.
_MISSING = object()


def _source(source_id=None, config=None):
    return SimpleNamespace(
        id=source_id or uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="win-test",
        source_type="windows_winrm",
        hostname="10.0.0.5",
        ip_address="10.0.0.5",
        port=5985,
        protocol="http",
        snmp_version=None,
        username="CORP\\svc-monitor",
        api_key=None,
        password="secret-password",   # never asserted into any output below
        config=config or {},
        verify_ssl=False,
    )


def _collector(**kwargs) -> WindowsWinRMCollector:
    reset_circuits()
    return WindowsWinRMCollector(_source(**kwargs))


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

class TestProfiles:
    def test_server_2022_selected(self):
        assert select_profile(
            "Microsoft Windows Server 2022 Standard"
        ).profile_id == "windows_server_2022"

    def test_server_2019_selected(self):
        assert select_profile(
            "Microsoft Windows Server 2019 Datacenter"
        ).profile_id == "windows_server_2019"

    def test_unknown_os_falls_back_to_generic(self):
        assert select_profile("Windows Nano Server 2030").profile_id == \
            "windows_generic"

    def test_none_caption_falls_back_to_generic(self):
        assert select_profile(None).profile_id == "windows_generic"

    def test_all_profiles_define_required_queries(self):
        for profile in WINDOWS_PROFILES.values():
            for required in ("os", "computer_system", "cpu", "disk"):
                assert required in profile.wmi_queries


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------

RAW = {
    "computer_system": [{"Name": "SRV-APP01", "Domain": "corp.local",
                         "NumberOfProcessors": 2}],
    "os": [{
        "Caption": "Microsoft Windows Server 2022 Standard",
        "Version": "10.0.20348", "BuildNumber": "20348",
        "LastBootUpTime": "20260820081500.500000+330",
        "TotalVisibleMemorySize": 16777216,
        "FreePhysicalMemory": 4194304,
    }],
    "cpu": [{"Name": "Intel Xeon", "LoadPercentage": 37,
             "NumberOfCores": 8, "MaxClockSpeed": 2400}],
    "disk": [
        {"DeviceID": "C:", "Size": 500000000000, "FreeSpace": 125000000000,
         "VolumeName": "System"},
        {"DeviceID": "D:", "Size": 1000000000000, "FreeSpace": 900000000000,
         "VolumeName": "Data"},
    ],
    "perf_os": [{"ProcessorQueueLength": 2, "SystemUpTime": 345600}],
    "perf_proc": [
        {"IDProcess": 101, "Name": "sqlservr", "PercentProcessorTime": 22,
         "WorkingSetPrivate": 2147483648},
        {"IDProcess": 202, "Name": "Idle", "PercentProcessorTime": 60,
         "WorkingSetPrivate": 0},
    ],
    "services": [
        {"Name": "W32Time", "DisplayName": "Windows Time", "State": "Running",
         "StartMode": "Auto"},
        {"Name": "Spooler", "DisplayName": "Print Spooler", "State": "Stopped",
         "StartMode": "Auto"},
    ],
    "network": [{
        "InterfaceIndex": 12, "Description": "Intel Ethernet I210",
        "MACAddress": "AA-BB-CC-DD-EE-FF",
        "IPAddress": ["192.168.1.50", "fe80::1"], "IPSubnet": ["255.255.255.0"],
        "DefaultIPGateway": ["192.168.1.1"],
    }],
}


class TestNormalize:
    def setup_method(self):
        self.bundle = _collector().normalize(RAW)

    def test_device_identity_from_computer_system(self):
        device = self.bundle.devices[0]
        assert device.external_id == "SRV-APP01"
        assert device.name == "corp.local\\SRV-APP01"
        assert device.device_type == "server"
        assert device.vendor == "Microsoft"
        assert device.os_version == "Microsoft Windows Server 2022 Standard"

    def test_profile_recorded_in_metadata(self):
        meta = self.bundle.devices[0].custom_metadata
        assert meta["windows_profile"] == "windows_server_2022"
        assert meta["source_type"] == "wmi"

    def test_cpu_memory_disk_sensors(self):
        by_type = {}
        for s in self.bundle.sensors:
            by_type.setdefault(s.metric_type, []).append(s)
        assert by_type["cpu_usage"][0].value == 37.0
        mem = by_type["memory_usage"][0]
        assert mem.value == round((16777216 - 4194304) / 16777216 * 100, 2)
        disk_by_name = {s.name: s for s in by_type["disk_usage"]}
        assert disk_by_name["Disk C: Usage"].value == 75.0
        assert disk_by_name["Disk D: Usage"].value == 10.0

    def test_uptime_prefers_perf_counter(self):
        uptime = next(s for s in self.bundle.sensors
                      if s.metric_type == "uptime_seconds")
        assert uptime.value == 345600

    def test_process_metrics_exclude_idle_and_total(self):
        procs = [s for s in self.bundle.sensors if s.metric_type == "process_cpu"]
        assert len(procs) == 1
        assert procs[0].name == "Process sqlservr CPU"

    def test_service_status_running_vs_stopped(self):
        svc = {s.name: s for s in self.bundle.sensors
               if s.metric_type == "service_status"}
        assert svc["Service W32Time"].value == 1.0
        assert svc["Service W32Time"].status == "up"
        assert svc["Service Spooler"].value == 0.0
        assert svc["Service Spooler"].status == "warning"

    def test_network_interfaces_normalized(self):
        assert len(self.bundle.interfaces) == 1
        nic = self.bundle.interfaces[0]
        assert nic.name == "Intel Ethernet I210"
        assert nic.mac_address == "AA-BB-CC-DD-EE-FF"
        assert nic.oper_status == "up"

    def test_no_password_in_any_output(self):
        blob = repr(self.bundle)
        assert "secret-password" not in blob


class TestBiosSerialIdentity:
    """Phase 4.1: Win32_BIOS.SerialNumber -> NormalizedDevice.serial_number.

    This is what lets a host seen by both WMI and SNMP resolve to ONE canonical
    Device, so both the mapping and the placeholder filtering matter.
    """

    @staticmethod
    def _raw(serial):
        raw = dict(RAW)
        if serial is _MISSING:
            raw.pop("bios", None)
        else:
            raw["bios"] = [{"SerialNumber": serial}]
        return raw

    def test_bios_serial_is_mapped_and_normalised(self):
        bundle = _collector().normalize(self._raw("  vmware-42 1a b2 c3 "))
        assert bundle.devices[0].serial_number == "VMWARE-42 1A B2 C3"

    def test_bios_serial_placeholder_is_not_identity(self):
        """A board with no real serial must not become an identity key, or every
        such host would collapse into one device."""
        for junk in (
            "To be filled by O.E.M.",
            "To be filled by OEM",
            "Default string",
            "System Serial Number",
            "None",
            "",
            "   ",
            "00000000",
        ):
            bundle = _collector().normalize(self._raw(junk))
            assert bundle.devices[0].serial_number is None, junk

    def test_missing_bios_key_is_safe(self):
        bundle = _collector().normalize(self._raw(_MISSING))
        assert bundle.devices[0].serial_number is None

    def test_empty_bios_result_is_safe(self):
        raw = dict(RAW)
        raw["bios"] = []
        assert _collector().normalize(raw).devices[0].serial_number is None

    def test_bios_row_without_serialnumber_is_safe(self):
        raw = dict(RAW)
        raw["bios"] = [{"SerialNumber": None}]
        assert _collector().normalize(raw).devices[0].serial_number is None

    def test_real_serial_survives_unchanged(self):
        bundle = _collector().normalize(self._raw("FGT60F0000000001"))
        assert bundle.devices[0].serial_number == "FGT60F0000000001"

    def test_every_windows_profile_requests_win32_bios(self):
        """Regression guard: profiles are built from _BASE_QUERIES, so a newly
        added profile must inherit the BIOS query automatically."""
        from app.collectors.windows_profiles import WINDOWS_PROFILES

        assert WINDOWS_PROFILES, "profiles must exist"
        for profile in WINDOWS_PROFILES.values():
            assert "bios" in profile.wmi_queries, profile.profile_id
            assert profile.wmi_queries["bios"] == "SELECT SerialNumber FROM Win32_BIOS"

    def test_wmi_source_type_inherits_the_serial_mapping(self):
        """The `wmi` collector shares normalize(), so it must produce the same
        serial - otherwise only one of the two source types could correlate."""
        from app.collectors.wmi import WMICollector

        bundle = WMICollector(_source()).normalize(self._raw("WMI-SERIAL-1"))
        assert bundle.devices[0].serial_number == "WMI-SERIAL-1"


# ---------------------------------------------------------------------------
# Circuit breaker + reliability
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        cb = _HostCircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        assert cb.allow_request()
        cb.record_failure(); cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"
        assert not cb.allow_request()

    def test_success_resets(self):
        cb = _HostCircuitBreaker(failure_threshold=2, cooldown_seconds=300)
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.state == "closed"

    def test_half_open_after_cooldown(self):
        import time as _t
        cb = _HostCircuitBreaker(failure_threshold=1, cooldown_seconds=0.01)
        cb.record_failure()
        _t.sleep(0.02)
        assert cb.state == "half_open"
        assert cb.allow_request()

    def test_hosts_are_isolated(self):
        c1 = _collector()
        c2 = _collector()
        # Trip host 1's breaker directly through its key.
        from app.collectors import windows_winrm as ww
        ww._circuit_for(c1.source).record_failure()
        ww._circuit_for(c1.source).record_failure()
        ww._circuit_for(c1.source).record_failure()
        assert not ww._circuit_for(c1.source).allow_request()
        assert ww._circuit_for(c2.source).state == "closed"


class TestCollectReliability:
    # NOTE: these are declared `async def` and awaited directly rather than
    # driven with `asyncio.run()`. `asyncio.run()` closes and clears the current
    # event loop on exit, and with `asyncio_default_test_loop_scope = "session"`
    # that destroys the loop every later async test depends on - producing
    # "RuntimeError: There is no current event loop in thread 'MainThread'" in
    # unrelated test files that happen to run afterwards.
    async def test_collect_raises_circuit_open_when_open(self):
        col = _collector()
        from app.collectors import windows_winrm as ww
        breaker = ww._circuit_for(col.source)
        breaker.failure_threshold = 1
        breaker.record_failure()
        with pytest.raises(CircuitOpenError):
            await col.collect()

    async def test_authenticate_returns_check_not_raise_on_failure(self):
        col = _collector()

        class Boom(Exception):
            pass

        col._build_protocol = lambda: (_ for _ in ()).throw(Boom())
        result = await col.authenticate()
        assert isinstance(result, ConnectionCheck)
        assert result.success is False
        assert "secret-password" not in result.message

    def test_missing_config_raises_collector_config_error(self):
        src = _source()
        src.username = None
        with pytest.raises(CollectorConfigError):
            WindowsWinRMCollector(src)._require_config()

    def test_invalid_auth_type_rejected(self):
        col = _collector(config={"auth_type": "digest"})
        with pytest.raises(CollectorConfigError):
            col._auth_type()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class _FakeProtocol:
    """Mimics the pywinrm.Protocol surface used by discovery."""

    def __init__(self, rows_by_query: dict[str, list[dict]] | None = None,
                 fail: bool = False):
        self.rows_by_query = rows_by_query or {}
        self.fail = fail

    def open_shell(self):
        return "shell-1"

    def close_shell(self, shell_id):
        pass

    def run_command(self, shell_id, cmd, args):
        return "cmd-1"

    def cleanup_command(self, shell_id, command_id):
        pass

    def get_command_output(self, shell_id, command_id):
        if self.fail:
            return b"", b"auth failed", 1
        # Recover the WQL from the recorded command args
        command_line = " ".join(DISCOVERY_LAST_ARGS[-1]) if DISCOVERY_LAST_ARGS else ""
        for wql, rows in self.rows_by_query.items():
            if wql in command_line:
                import json
                return json.dumps(rows).encode(), b"", 0
        return b"", b"", 0


DISCOVERY_LAST_ARGS: list[list[str]] = []


def _patch_fake_protocol(monkeypatch, protocol: _FakeProtocol):
    fake_winrm = SimpleNamespace(Protocol=lambda **kw: protocol)

    orig_run = protocol.run_command

    def spy_run(shell_id, cmd, args):
        DISCOVERY_LAST_ARGS.append(args)
        return orig_run(shell_id, cmd, args)

    protocol.run_command = spy_run
    monkeypatch.setitem(__import__("sys").modules, "winrm", fake_winrm)


CS_ROWS = [{"Name": "SRV-DB01", "Domain": "corp.local"}]
OS_ROWS = [{"Caption": "Microsoft Windows Server 2019 Standard",
            "Version": "10.0.17763", "BuildNumber": "17763"}]


class TestDiscovery:
    def test_discovery_returns_verified_identity(self, monkeypatch):
        proto = _FakeProtocol({**{k: v for k, v in {
            "Win32_ComputerSystem": CS_ROWS,
            "Win32_OperatingSystem": OS_ROWS,
        }.items()}})
        _patch_fake_protocol(monkeypatch, proto)
        identity = discover_windows_host(
            "10.0.0.9", "CORP\\admin", "pw123", auth_type="ntlm")
        assert identity.computer_name == "SRV-DB01"
        assert identity.domain == "corp.local"
        assert identity.os_caption == "Microsoft Windows Server 2019 Standard"
        assert identity.build_number == "17763"

    def test_discovery_failure_has_no_secret_in_message(self, monkeypatch):
        _patch_fake_protocol(monkeypatch, _FakeProtocol(fail=True))
        with pytest.raises(WindowsDiscoveryError) as excinfo:
            discover_windows_host("10.0.0.9", "CORP\\admin", "super-secret-pw")
        assert "super-secret-pw" not in str(excinfo.value)

    def test_discovery_refuses_unverified_identity(self, monkeypatch):
        proto = _FakeProtocol({
            "Win32_ComputerSystem": [{}],
            "Win32_OperatingSystem": OS_ROWS,
        })
        _patch_fake_protocol(monkeypatch, proto)
        with pytest.raises(WindowsDiscoveryError):
            discover_windows_host("10.0.0.9", "u", "p")

    def test_discovery_invalid_auth_type(self):
        with pytest.raises(ValueError):
            discover_windows_host("h", "u", "p", auth_type="digest")