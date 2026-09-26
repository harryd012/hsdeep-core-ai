"""
Minimum-viable Windows discovery via remote WMI over WinRM.

Given a hostname/IP + credential, verifies WinRM reachability and pulls the
sysName-equivalent identity (Win32_ComputerSystem.Name / .Domain) plus OS
version (Win32_OperatingSystem.Caption) to confirm the host's identity
BEFORE it is registered as a monitored device.

Full network-range discovery is intentionally out of scope here — that is
the SNMP/ICMP discovery pattern and must not be duplicated for Windows.

Security: credentials are accepted as plaintext arguments only in-process,
are never logged, never appear in returned results, and never appear in
error messages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_IDENTITY_QUERY = (
    "SELECT Name, Domain FROM Win32_ComputerSystem"
)
_OS_QUERY = (
    "SELECT Caption, Version, BuildNumber FROM Win32_OperatingSystem"
)


@dataclass(frozen=True)
class WindowsHostIdentity:
    """Identity facts pulled from a reachable Windows host."""
    computer_name: str
    domain: str | None
    os_caption: str | None
    os_version: str | None
    build_number: str | None


class WindowsDiscoveryError(RuntimeError):
    """WinRM unreachable or authentication failed. Message contains no secrets."""


def discover_windows_host(
    hostname: str,
    username: str,
    password: str,
    *,
    port: int | None = None,
    transport: str = "http",
    auth_type: str = "ntlm",
    verify_ssl: bool = False,
) -> WindowsHostIdentity:
    """Verify WinRM reachability and return the host's verified identity.

    Raises:
        WindowsDiscoveryError: host unreachable or auth failed.
        ValueError: invalid auth_type.
    """
    if auth_type not in ("ntlm", "basic", "kerberos"):
        raise ValueError(f"Unsupported auth_type {auth_type!r}")

    try:
        import winrm  # type: ignore[import]
    except ImportError as exc:
        raise WindowsDiscoveryError(
            "pywinrm is required for Windows discovery "
            "(pip install pywinrm==0.4.3)"
        ) from exc

    effective_port = port or (5986 if transport == "https" else 5985)
    protocol = winrm.Protocol(
        endpoint=f"{transport}://{hostname}:{effective_port}/wsman",
        transport=auth_type,
        username=username,
        password=password,
        server_cert_validation="validate" if verify_ssl else "ignore",
    )

    def _query(wql: str) -> list[dict]:
        ps = (
            'Get-CimInstance -Query "' + wql + '" | '
            "Select-Object * | ConvertTo-Json -Depth 2 -Compress"
        )
        shell_id = protocol.open_shell()
        try:
            command_id = protocol.run_command(
                shell_id, "powershell.exe",
                ["-NonInteractive", "-NoProfile", "-Command", ps],
            )
            stdout, _stderr, rc = protocol.get_command_output(shell_id, command_id)
            protocol.cleanup_command(shell_id, command_id)
        finally:
            protocol.close_shell(shell_id)
        if rc != 0:
            raise WindowsDiscoveryError(
                f"WinRM query failed on {hostname} (exit code {rc})"
            )
        import json
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]

    try:
        cs_rows = _query(_IDENTITY_QUERY)
        os_rows = _query(_OS_QUERY)
    except WindowsDiscoveryError:
        raise
    except Exception as exc:  # noqa: BLE001 — auth/transport failures land here
        # Log only exception type; message may embed endpoint details.
        logger.info("Windows discovery failed for %s: %s", hostname, type(exc).__name__)
        raise WindowsDiscoveryError(
            f"WinRM connection to {hostname} failed ({type(exc).__name__}); "
            "check reachability, credentials, and WinRM listener configuration"
        ) from exc

    if not cs_rows or not cs_rows[0].get("Name"):
        raise WindowsDiscoveryError(
            f"WinRM reachable but {hostname} did not report a computer name; "
            "refusing to register an unverified device"
        )

    cs = cs_rows[0]
    os_row = os_rows[0] if os_rows else {}

    return WindowsHostIdentity(
        computer_name=str(cs["Name"]),
        domain=cs.get("Domain") or None,
        os_caption=os_row.get("Caption"),
        os_version=os_row.get("Version"),
        build_number=os_row.get("BuildNumber"),
    )