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
    ip -j addr show             -> interface addresses (L3 topology)
    ip -j route show            -> main routing table (L3 topology)

Dependency: asyncssh==2.14.2 (pinned in requirements.txt)
"""
from __future__ import annotations

import json
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
    NormalizedInterfaceAddress,
    NormalizedRoute,
    NormalizedSensorReading,
)
from app.core.ip_addressing import (
    normalise_vrf,
    parse_interface_address,
    parse_next_hop,
    parse_route_destination,
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
                # iproute2 JSON output (-j) needs iproute2 >= 4.13; an older
                # host prints an error instead, which normalize() reports as
                # "unavailable" rather than "no addresses".
                raw["ip_addr"] = await self._run(conn, "ip -j addr show 2>/dev/null")
                raw["ip_route"] = await self._run(conn, "ip -j route show 2>/dev/null")
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

        l3_status: dict[str, str] = {}
        addresses: list[NormalizedInterfaceAddress] = []
        routes: list[NormalizedRoute] = []
        if "ip_addr" in raw:
            parsed = _parse_ip_addr_json(raw["ip_addr"], hostname)
            if parsed is None:
                l3_status["addresses"] = "unavailable"
            else:
                addresses = parsed
                l3_status["addresses"] = "collected" if addresses else "empty"
        if "ip_route" in raw:
            parsed_routes = _parse_ip_route_json(raw["ip_route"], hostname)
            if parsed_routes is None:
                l3_status["routes"] = "unavailable"
            else:
                routes = parsed_routes
                l3_status["routes"] = "collected" if routes else "empty"

        return NormalizedBundle(
            devices=[device],
            sensors=sensors,
            interfaces=interfaces,
            addresses=addresses,
            routes=routes,
            l3_status=l3_status,
        )


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


#: Container / hypervisor bridges. Every Docker host has 172.17.0.1/16 on
#: docker0, so treating these as real subnets would join unrelated servers.
_VIRTUAL_IFACE_PREFIXES = (
    "docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "lxc", "lxd",
    "podman", "kube-", "vxlan", "tun", "tap", "wg",
)


def _is_virtual_iface(name: str) -> bool:
    return name == "lo" or name.startswith(_VIRTUAL_IFACE_PREFIXES)


def _load_json_list(text: str) -> list | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _parse_ip_addr_json(text: str, hostname: str) -> list[NormalizedInterfaceAddress] | None:
    """``ip -j addr show``. ``None`` when the output is not iproute2 JSON."""
    data = _load_json_list(text)
    if data is None:
        return None
    out: list[NormalizedInterfaceAddress] = []
    for link in data:
        if not isinstance(link, dict):
            continue
        name = link.get("ifname")
        if not name or _is_virtual_iface(name):
            continue
        for info in link.get("addr_info") or []:
            if not isinstance(info, dict) or info.get("family") not in ("inet", "inet6"):
                continue
            parsed = parse_interface_address(info.get("local"), info.get("prefixlen"))
            if parsed is None:
                continue
            out.append(NormalizedInterfaceAddress(
                device_external_id=hostname,
                interface_external_id=name,
                interface_name=name,
                address=parsed.address,
                prefix_length=parsed.prefix_length,
                is_primary=not info.get("secondary", False),
            ))
    return out


_IPROUTE_PROTOCOLS = {
    "boot": "static",
    "static": "static",
    "dhcp": "static",
    "ra": "static",
    "bgp": "bgp",
    "ospf": "ospf",
}


def _parse_ip_route_json(text: str, hostname: str) -> list[NormalizedRoute] | None:
    """``ip -j route show`` (main table). ``proto kernel scope link`` is a
    connected route; boot/static/dhcp routes are treated as static."""
    data = _load_json_list(text)
    if data is None:
        return None
    out: list[NormalizedRoute] = []
    for route in data:
        if not isinstance(route, dict):
            continue
        dev = route.get("dev")
        if dev and _is_virtual_iface(dev):
            continue
        destination = parse_route_destination(route.get("dst"))
        if destination is None:
            continue
        proto = str(route.get("protocol", "")).lower()
        if proto == "kernel" and route.get("scope") == "link":
            protocol = "connected"
        else:
            protocol = _IPROUTE_PROTOCOLS.get(proto, "other")
        metric = route.get("metric")
        out.append(NormalizedRoute(
            device_external_id=hostname,
            destination=destination[0],
            prefix_length=destination[1],
            protocol=protocol,
            next_hop=parse_next_hop(route.get("gateway")),
            outgoing_interface=dev or None,
            metric=metric if isinstance(metric, int) else None,
            vrf=normalise_vrf(route.get("table")),
        ))
    return out
