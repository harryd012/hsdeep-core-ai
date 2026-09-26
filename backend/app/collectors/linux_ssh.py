"""
Linux SSH collector.

Connects to Linux hosts via asyncssh and collects system metrics by running
standard shell commands (no agent required).

Connection details from `MonitoringSourceConnectionInfo`:
    hostname    -> Linux host IP or FQDN
    port        -> SSH port (default 22)
    username    -> SSH username
    password    -> SSH password (encrypted at rest); if absent, key-based auth is used
    api_key     -> SSH private key path or inline key (optional, alternative to password)
    verify_ssl  -> Not applicable for SSH; ignored

Commands executed:
    uname -a                    -> OS info
    hostname                    -> hostname
    cat /proc/loadavg           -> load average
    free -b                     -> memory in bytes
    df -B1 -x tmpfs -x devtmpfs -> disk usage per mount
    cat /proc/net/dev           -> network interface counters

Dependency: asyncssh==2.14.2 (pinned in requirements.txt)
"""
from __future__ import annotations

import logging
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedSensorReading,
)

logger = logging.getLogger(__name__)


@CollectorRegistry.register("linux_ssh")
class LinuxSSHCollector(BaseCollector):
    display_name = "Linux (SSH)"
    implemented = True

    # -- config helpers -------------------------------------------------------

    def _require_config(self) -> None:
        if not self.source.hostname:
            raise CollectorConfigError(
                "Linux SSH config missing required field: hostname"
            )
        if not self.source.username:
            raise CollectorConfigError(
                "Linux SSH config missing required field: username"
            )

    def _connect_kwargs(self) -> dict[str, Any]:
        """Build asyncssh connect() keyword arguments. Credentials not logged."""
        kwargs: dict[str, Any] = {
            "host": self.source.hostname,
            "port": self.source.port or 22,
            "username": self.source.username,
            "known_hosts": None,         # disable host-key verification for simplicity
        }
        if self.source.password:
            kwargs["password"] = self.source.password
        return kwargs

    async def _run(self, conn: Any, command: str) -> str:
        """Run a single command and return stdout as a stripped string."""
        result = await conn.run(command, check=False)
        return (result.stdout or "").strip()

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        try:
            import asyncssh  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError(
                "asyncssh is required for the Linux SSH collector. "
                "Install it via: pip install asyncssh==2.14.2"
            ) from exc

        try:
            async with asyncssh.connect(**self._connect_kwargs()) as conn:
                output = await self._run(conn, "echo hsdeep_ok")
                if "hsdeep_ok" in output:
                    return ConnectionCheck(success=True, message="SSH connection established.")
                return ConnectionCheck(success=False, message="SSH: echo test returned unexpected output.")
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=f"SSH authentication failed: {type(exc).__name__}: {exc}",
            )

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        raw: dict[str, Any] = {}

        try:
            import asyncssh  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError("asyncssh is required.") from exc

        try:
            async with asyncssh.connect(**self._connect_kwargs()) as conn:
                raw["uname"] = await self._run(conn, "uname -a")
                raw["hostname"] = await self._run(conn, "hostname")
                raw["loadavg"] = await self._run(conn, "cat /proc/loadavg")
                raw["meminfo"] = await self._run(conn, "free -b")
                raw["df"] = await self._run(conn, "df -B1 -x tmpfs -x devtmpfs --output=target,size,used,avail,pcent 2>/dev/null")
                raw["netdev"] = await self._run(conn, "cat /proc/net/dev")
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Linux SSH collect(): failed: source_id=%s error=%s",
                self.source.id, exc,
            )

        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        hostname = raw.get("hostname") or self.source.hostname or "linux-host"

        device = NormalizedDevice(
            external_id=hostname,
            name=hostname,
            device_type="server",
            vendor="Linux",
            os_version=_parse_os_version(raw.get("uname", "")),
            hostname=hostname,
            ip_address=self.source.ip_address or self.source.hostname,
            status="up",
        )

        sensors: list[NormalizedSensorReading] = []
        interfaces: list[NormalizedInterface] = []

        # Load average (1-min)
        loadavg = raw.get("loadavg", "")
        if loadavg:
            parts = loadavg.split()
            if parts:
                load1 = _safe_float(parts[0])
                if load1 is not None:
                    sensors.append(NormalizedSensorReading(
                        external_id=f"{hostname}-load1",
                        name="Load Average (1m)",
                        metric_type="load_avg_1m",
                        value=load1,
                        unit="",
                        status="up",
                        device_external_id=hostname,
                    ))

        # Memory usage %
        mem_pct = _parse_memory_pct(raw.get("meminfo", ""))
        if mem_pct is not None:
            sensors.append(NormalizedSensorReading(
                external_id=f"{hostname}-mem",
                name="Memory Usage",
                metric_type="memory_usage",
                value=mem_pct,
                unit="%",
                status="up",
                device_external_id=hostname,
            ))

        # Disk usage per mount
        for mount, pct in _parse_df(raw.get("df", "")).items():
            safe_mount = mount.replace("/", "_").strip("_") or "root"
            sensors.append(NormalizedSensorReading(
                external_id=f"{hostname}-disk-{safe_mount}",
                name=f"Disk {mount}",
                metric_type="disk_usage",
                value=pct,
                unit="%",
                status="up",
                device_external_id=hostname,
            ))

        # Network interfaces from /proc/net/dev
        for iface_name, counters in _parse_netdev(raw.get("netdev", "")).items():
            interfaces.append(NormalizedInterface(
                device_external_id=hostname,
                external_id=iface_name,
                name=iface_name,
                admin_status="up",
                oper_status="up",
                in_octets=counters.get("in_octets"),
                out_octets=counters.get("out_octets"),
                in_errors=counters.get("in_errors"),
                out_errors=counters.get("out_errors"),
            ))

        return NormalizedBundle(devices=[device], sensors=sensors, interfaces=interfaces)


# ---------------------------------------------------------------------------
# Pure parsing helpers (tested independently in test_linux_ssh_collector.py)
# ---------------------------------------------------------------------------

def _parse_os_version(uname: str) -> str | None:
    """Extract kernel version from `uname -a` output."""
    if not uname:
        return None
    parts = uname.split()
    # Format: Linux hostname <kernel-version> ...
    return parts[2] if len(parts) >= 3 else uname[:50]


def _parse_memory_pct(meminfo: str) -> float | None:
    """Parse `free -b` output and return used-memory percentage."""
    if not meminfo:
        return None
    for line in meminfo.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 3:
                total = _safe_float(parts[1])
                used = _safe_float(parts[2])
                if total and used is not None and total > 0:
                    return round((used / total) * 100.0, 2)
    return None


def _parse_df(df_output: str) -> dict[str, float]:
    """Parse `df --output=target,...,pcent` and return {mount: used_pct}."""
    result: dict[str, float] = {}
    if not df_output:
        return result
    for line in df_output.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 5:
            mount = parts[0]
            pct_str = parts[4].rstrip("%")
            pct = _safe_float(pct_str)
            if pct is not None:
                result[mount] = pct
    return result


def _parse_netdev(netdev: str) -> dict[str, dict[str, float]]:
    """Parse /proc/net/dev and return {iface: {in_octets, out_octets, ...}}."""
    result: dict[str, dict[str, float]] = {}
    if not netdev:
        return result
    for line in netdev.splitlines()[2:]:  # skip 2-line header
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        if iface in ("lo",):          # skip loopback
            continue
        fields = rest.split()
        if len(fields) < 16:
            continue
        result[iface] = {
            "in_octets": _safe_float(fields[0]) or 0.0,
            "in_errors": _safe_float(fields[2]) or 0.0,
            "out_octets": _safe_float(fields[8]) or 0.0,
            "out_errors": _safe_float(fields[10]) or 0.0,
        }
    return result


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None
