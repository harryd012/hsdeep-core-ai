"""
Windows device profile system.

Mirrors the SNMP profile abstraction: an OS family/version string maps to a
profile that defines *which* metrics are collected and how they are
interpreted — so supporting Windows Server 2016 vs 2019 vs 2022 (or adding a
new one) never requires touching the collector code.

A profile declares:
    - ``wmi_queries``: named WQL queries the collector should run.
    - ``metric_map``: mapping of normalized metric_type -> definition
      (unit, source class/property, description).
    - ``supported_auth``: auth types known to work for this family.

Profile selection is by OS caption matching (see ``select_profile``); unknown
OSes fall back to ``windows_generic`` so collection still works on unlisted
versions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDefinition:
    """One normalized metric produced from a WMI class/property."""
    metric_type: str          # canonical metric key used in NormalizedSensorReading
    unit: str | None
    wmi_class: str            # source WMI class (documentation / filtering)
    property_name: str        # source WMI property (documentation)
    description: str = ""


@dataclass(frozen=True)
class WindowsProfile:
    profile_id: str           # e.g. "windows_server_2022"
    vendor: str               # "Microsoft"
    family: str               # e.g. "Windows Server"
    os_matchers: tuple[str, ...]          # substrings matched against Win32_OperatingSystem.Caption
    wmi_queries: dict[str, str] = field(default_factory=dict)  # name -> WQL
    metrics: tuple[MetricDefinition, ...] = ()
    supported_auth: tuple[str, ...] = ("ntlm", "basic", "kerberos")


# ---------------------------------------------------------------------------
# Shared query blocks
# ---------------------------------------------------------------------------

_Q_OS = (
    "SELECT Caption, Version, BuildNumber, LastBootUpTime, "
    "TotalVisibleMemorySize, FreePhysicalMemory FROM Win32_OperatingSystem"
)
_Q_CS = "SELECT Name, Domain, NumberOfProcessors FROM Win32_ComputerSystem"
_Q_CPU = "SELECT Name, LoadPercentage, NumberOfCores, MaxClockSpeed FROM Win32_Processor"
_Q_DISK = (
    "SELECT DeviceID, Size, FreeSpace, VolumeName FROM Win32_LogicalDisk "
    "WHERE DriveType=3"
)
_Q_PERF_OS = (
    "SELECT ProcessorQueueLength, SystemUpTime FROM "
    "Win32_PerfFormattedData_PerfOS_System"
)
_Q_PERF_PROC = (
    "SELECT IDProcess, Name, PercentProcessorTime, WorkingSetPrivate FROM "
    "Win32_PerfFormattedData_PerfProc_Process WHERE Name != '_Total' "
    "AND Name != 'Idle'"
)
_Q_SERVICES = (
    "SELECT Name, DisplayName, State, StartMode FROM Win32_Service "
    "WHERE StartMode='Auto'"
)
_Q_NIC = (
    "SELECT InterfaceIndex, Description, MACAddress, IPAddress, IPSubnet, "
    "DefaultIPGateway FROM Win32_NetworkAdapterConfiguration WHERE IPEnabled=True"
)
# Hardware serial: the AUTHORITATIVE cross-source identity key (Phase 4 §16/§17),
# so a host seen by both WMI and SNMP resolves to one canonical Device. Placeholder
# values ("To be filled by O.E.M." etc.) are filtered by
# app/services/discovery/identity.normalise_serial, not here.
_Q_BIOS = "SELECT SerialNumber FROM Win32_BIOS"

_BASE_QUERIES: dict[str, str] = {
    "os": _Q_OS,
    "computer_system": _Q_CS,
    "cpu": _Q_CPU,
    "disk": _Q_DISK,
    "perf_os": _Q_PERF_OS,
    "perf_proc": _Q_PERF_PROC,
    "services": _Q_SERVICES,
    "network": _Q_NIC,
    "bios": _Q_BIOS,
}

_BASE_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition("cpu_usage", "%", "Win32_Processor", "LoadPercentage",
                     "Per-socket CPU utilization"),
    MetricDefinition("memory_usage", "%", "Win32_OperatingSystem",
                     "TotalVisibleMemorySize/FreePhysicalMemory",
                     "Physical memory utilization"),
    MetricDefinition("disk_usage", "%", "Win32_LogicalDisk", "Size/FreeSpace",
                     "Per-volume disk utilization"),
    MetricDefinition("uptime_seconds", "s", "Win32_OperatingSystem",
                     "LastBootUpTime", "Host uptime"),
    MetricDefinition("cpu_queue_length", "", "Win32_PerfFormattedData_PerfOS_System",
                     "ProcessorQueueLength", "Processor queue depth"),
    MetricDefinition("process_cpu", "%", "Win32_PerfFormattedData_PerfProc_Process",
                     "PercentProcessorTime", "Per-process CPU time"),
    MetricDefinition("process_memory_bytes", "bytes",
                     "Win32_PerfFormattedData_PerfProc_Process",
                     "WorkingSetPrivate", "Per-process private working set"),
)


def _profile(profile_id: str, family: str, matchers: tuple[str, ...],
             extra_metrics: tuple[MetricDefinition, ...] = ()) -> WindowsProfile:
    return WindowsProfile(
        profile_id=profile_id,
        vendor="Microsoft",
        family=family,
        os_matchers=matchers,
        wmi_queries=dict(_BASE_QUERIES),
        metrics=_BASE_METRICS + extra_metrics,
    )


WINDOWS_PROFILES: dict[str, WindowsProfile] = {
    p.profile_id: p
    for p in (
        _profile(
            "windows_server_2025", "Windows Server",
            ("Windows Server 2025",),
        ),
        _profile(
            "windows_server_2022", "Windows Server",
            ("Windows Server 2022",),
        ),
        _profile(
            "windows_server_2019", "Windows Server",
            ("Windows Server 2019",),
        ),
        _profile(
            "windows_server_2016", "Windows Server",
            ("Windows Server 2016",),
        ),
        _profile(
            "windows_client_11", "Windows Client",
            ("Windows 11",),
        ),
        _profile(
            "windows_client_10", "Windows Client",
            ("Windows 10",),
        ),
        # Fallback: any unrecognized Windows version collects the base set.
        _profile(
            "windows_generic", "Windows",
            (),
        ),
    )
}

_GENERIC_PROFILE_ID = "windows_generic"


def select_profile(os_caption: str | None) -> WindowsProfile:
    """Pick a profile by matching the OS caption against known matchers.

    Falls back to ``windows_generic`` when nothing matches or the caption is
    unavailable, so collection never fails just because an OS is unlisted.
    """
    if os_caption:
        for profile in WINDOWS_PROFILES.values():
            if profile.profile_id == _GENERIC_PROFILE_ID:
                continue
            if any(m.lower() in os_caption.lower() for m in profile.os_matchers):
                return profile
    return WINDOWS_PROFILES[_GENERIC_PROFILE_ID]