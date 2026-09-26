"""
WMI collector (Phase 3B §2): WMI as a first-class discovery protocol.

WHY THIS IS A SUBCLASS AND NOT A NEW COLLECTOR
----------------------------------------------
WMI already existed in this codebase - `windows_winrm.py` executes real WQL
queries against `Win32_*` classes (`_run_wmi_once`/`_run_wmi`) with query
profiles in `windows_profiles.py` (Win32_ComputerSystem, Win32_Processor,
Win32_DiskDrive, Win32_NetworkAdapter, Win32_Service, Win32_OperatingSystem).
What was missing was a REGISTRY ENTRY: `wmi` is a `source_type` already used by
seed data and by `MonitoringSource`, but `CollectorRegistry.get("wmi")` raised
`ValueError` because nothing registered it.

So this module makes WMI first-class by registering the existing implementation
under the `wmi` source type. It deliberately reuses the whole pipeline -
transport, WQL profiles, retry/backoff, per-host circuit breaker, timeout and
`normalize()` -> `NormalizedDevice` / `NormalizedInterface` / sensors - rather
than creating a parallel discovery framework. Only the identity and the
operator-facing wording differ.

TRANSPORT - STATED HONESTLY
---------------------------
This is WMI over **WSMan/WinRM** (`Get-CimInstance` over the WinRM listener,
default ports 5985 HTTP / 5986 HTTPS). That is the agentless, cross-platform
path, and it needs no Windows-only Python dependency.

It is NOT legacy DCOM/RPC WMI on port 135. A `wmi` source configured with
port 135 will NOT connect here; the fix is to enable the WinRM listener on the
target and use 5985/5986. True DCOM would require a Windows-only dependency
(pywin32) and is deliberately not implemented - pretending otherwise would
produce a source that silently fails with a misleading message.

Config required (identical to the WinRM source): hostname, username, password.
Optional: port, protocol (http|https), verify_ssl, config.auth_type
(ntlm | basic | kerberos, default ntlm).
"""
from __future__ import annotations

from app.collectors.base import CollectorRegistry
from app.collectors.windows_winrm import WindowsWinRMCollector


@CollectorRegistry.register("wmi")
class WMICollector(WindowsWinRMCollector):
    """Remote WMI discovery, registered as its own `source_type`.

    Inherits everything: `authenticate`, `collect`, `normalize`, the WQL query
    profiles, the circuit breaker and the timeout/retry behaviour. Overriding
    nothing but the label means a WMI source and a WinRM source cannot drift
    apart - fixing the WMI implementation fixes both.
    """

    display_name = "Windows (WMI)"
    implemented = True
    transport_label = "WMI"


__all__ = ["WMICollector"]
