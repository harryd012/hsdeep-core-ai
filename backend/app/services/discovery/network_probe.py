"""
Bounded network host discovery + protocol detection (Phase 6 §5/§6).

This is the front half of the Universal Discovery pipeline that did not exist:
``ConnectivityCollector`` probes exactly ONE host named by ``source.hostname``,
and nothing in the codebase swept a subnet or classified per-host protocol
state. It reuses that collector's probe primitives rather than adding a second
probing stack.

Evidence rules enforced here (§5/§6):
  * a host is only ``reachable`` on real probe evidence (ICMP reply or a TCP
    accept) - a configured subnet alone never yields a device;
  * every protocol result is explicit: ``reachable`` | ``timeout`` |
    ``not_configured`` (nothing is collapsed into a generic "success");
  * probes are bounded by ``max_hosts`` / ``max_concurrency`` / ``timeout`` so a
    /24 can never become an unbounded scan.

This module only produces evidence. Persisting devices/interfaces/relationships
stays with the existing NormalizedBundle -> BundleSaver -> ResourceRelationship
path.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field

from app.collectors.connectivity import _icmp_result, _tcp_result

logger = logging.getLogger(__name__)

# TCP-detectable protocols we can classify without credentials. SNMP is UDP and
# is therefore NOT probed here; it is reported not_configured unless a credential
# exists (SNMP discovery stays inside the existing SNMPCollector).
PROTOCOL_PORTS: dict[str, int] = {
    "ssh": 22,
    "telnet": 23,
    "http": 80,
    "https": 443,
    "winrm": 5985,
    "rdp": 3389,
}

DEFAULT_MAX_HOSTS = 256
DEFAULT_MAX_CONCURRENCY = 32
DEFAULT_TIMEOUT_SECONDS = 1.0

# Global cap on SIMULTANEOUS TCP connect attempts across the whole sweep.
#
# Phase 6 live evidence: with 64 hosts in flight, each firing 6 protocol probes
# at once, a /24 sweep issued up to ~384 concurrent connects alongside 254 ICMP
# subprocesses. On Windows that contention made connects fail, and the failures
# were recorded as `timeout` - a false negative. The same host probed serially
# correctly reported ssh/telnet/http/https = reachable.
#
# Two independent bounds now prevent that: a host's protocols are probed
# SEQUENTIALLY (one connect at a time per host), and this semaphore caps the
# total number of in-flight connects for the whole sweep.
DEFAULT_MAX_CONCURRENT_TCP_PROBES = 8

# Explicit protocol states (§6).
STATE_REACHABLE = "reachable"
STATE_TIMEOUT = "timeout"
STATE_NOT_CONFIGURED = "not_configured"


# Protocols that exist in the platform but are NOT probeable by this TCP sweep
# (SNMP is UDP and stays inside the existing SNMPCollector). They are reported
# explicitly as not_configured rather than silently omitted (§ Phase 6.1 §3).
UNPROBED_PROTOCOLS: tuple[str, ...] = ("snmp",)


@dataclass
class HostProbeResult:
    """Probe evidence for a single address. Nothing here is inferred."""

    address: str
    reachable: bool = False
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    protocols: dict[str, str] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


@dataclass
class NetworkProbeReport:
    """Bounded sweep result for one CIDR."""

    cidr: str
    hosts_probed: int = 0
    hosts_total: int = 0
    truncated: bool = False
    hosts: list[HostProbeResult] = field(default_factory=list)

    @property
    def reachable(self) -> list[HostProbeResult]:
        return [h for h in self.hosts if h.reachable]

    def protocol_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for host in self.hosts:
            for proto, state in host.protocols.items():
                if state == STATE_REACHABLE:
                    counts[proto] = counts.get(proto, 0) + 1
        return counts


def hosts_in_cidr(cidr: str, max_hosts: int = DEFAULT_MAX_HOSTS) -> tuple[list[str], int, bool]:
    """Usable host addresses in ``cidr``, bounded by ``max_hosts``.

    Returns ``(hosts, total_usable, truncated)``. Network and broadcast
    addresses are excluded; a /31 or /32 is treated as point-to-point/host.
    """
    network = ipaddress.ip_network(cidr, strict=False)
    if network.prefixlen >= 31:
        usable = [str(ip) for ip in network]
    else:
        usable = [str(ip) for ip in network.hosts()]
    truncated = len(usable) > max_hosts
    return usable[:max_hosts], len(usable), truncated


def _is_excluded(address: str, patterns: list[str]) -> bool:
    """True when ``address`` falls inside any excluded IP or CIDR.

    Unparseable exclusion patterns are ignored rather than aborting the sweep;
    scope validation rejects them before they ever reach here.
    """
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return False
    for pattern in patterns:
        text = (pattern or "").strip()
        if not text:
            continue
        try:
            if "/" in text:
                if addr in ipaddress.ip_network(text, strict=False):
                    return True
            elif addr == ipaddress.ip_address(text):
                return True
        except ValueError:
            continue
    return False


async def probe_host(
    address: str,
    *,
    protocols: dict[str, int] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    tcp_gate: asyncio.Semaphore | None = None,
) -> HostProbeResult:
    """Probe one address: ICMP first, then a bounded TCP protocol sweep.

    A host counts as reachable only on real evidence (ICMP reply or a TCP
    accept). Every protocol gets an explicit state.

    Protocol probes for THIS host run sequentially (``tcp_gate`` only bounds how
    many hosts may be inside a connect at the same time), which is what removes
    the Phase 6 false-timeout contention.
    """
    result = HostProbeResult(address=address)
    ports = protocols if protocols is not None else PROTOCOL_PORTS

    icmp = await _icmp_result(address, 1, timeout)
    if icmp.get("ok"):
        result.reachable = True
        result.latency_ms = icmp.get("latency_ms")
        result.packet_loss_pct = icmp.get("packet_loss_pct")
        result.evidence.append("icmp:reply")

    for name, port in ports.items():
        await _probe_protocol(address, name, port, timeout, result, tcp_gate)

    # Declared-but-unprobeable protocols get an explicit state, never silence.
    for name in UNPROBED_PROTOCOLS:
        result.protocols.setdefault(name, STATE_NOT_CONFIGURED)

    return result


async def _probe_protocol(
    address: str,
    name: str,
    port: int,
    timeout: float,
    result: HostProbeResult,
    tcp_gate: asyncio.Semaphore | None = None,
) -> None:
    """Record one protocol's state. A TCP accept is real reachability evidence."""
    async def _connect() -> dict:
        return await asyncio.wait_for(
            _tcp_result(address, port, timeout), timeout=timeout * 2
        )

    try:
        if tcp_gate is None:
            probe = await _connect()
        else:
            async with tcp_gate:
                probe = await _connect()
    except (asyncio.TimeoutError, TimeoutError):
        result.protocols[name] = STATE_TIMEOUT
        return
    except Exception:  # noqa: BLE001 - a probe failure must never abort the sweep
        result.protocols[name] = STATE_TIMEOUT
        return

    if probe.get("ok"):
        result.protocols[name] = STATE_REACHABLE
        result.reachable = True
        result.evidence.append(f"tcp/{port}:open")
    else:
        result.protocols[name] = STATE_TIMEOUT


async def discover_network(
    cidr: str,
    *,
    max_hosts: int = DEFAULT_MAX_HOSTS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    protocols: dict[str, int] | None = None,
    max_tcp_concurrency: int = DEFAULT_MAX_CONCURRENT_TCP_PROBES,
    exclude: list[str] | None = None,
) -> NetworkProbeReport:
    """Bounded host-discovery sweep of one CIDR.

    Two independent bounds keep the sweep safe on Windows (§ Phase 6):
      * ``max_concurrency`` caps how many HOSTS are probed at once;
      * ``max_tcp_concurrency`` caps how many TCP connects are in flight for the
        whole sweep, and each host probes its protocols sequentially.

    ``exclude`` honours a DiscoveryScope's excluded_targets. Excluded addresses
    are removed BEFORE probing, so an excluded host is never contacted - it is
    not merely filtered out of the results afterwards.

    Nothing is persisted here - the caller decides what evidence is strong
    enough to become a device.
    """
    report = NetworkProbeReport(cidr=cidr)
    addresses, total, truncated = hosts_in_cidr(cidr, max_hosts)
    if exclude:
        addresses = [a for a in addresses if not _is_excluded(a, exclude)]
    report.hosts_total = total
    report.truncated = truncated

    host_gate = asyncio.Semaphore(max(1, max_concurrency))
    tcp_gate = asyncio.Semaphore(max(1, max_tcp_concurrency))

    async def _one(address: str) -> HostProbeResult:
        async with host_gate:
            try:
                return await probe_host(
                    address,
                    protocols=protocols,
                    timeout=timeout,
                    tcp_gate=tcp_gate,
                )
            except Exception:  # noqa: BLE001 - one bad host never stops the sweep
                logger.warning("Host probe failed for %s", address, exc_info=True)
                return HostProbeResult(address=address)

    results = await asyncio.gather(*(_one(a) for a in addresses)) if addresses else []
    report.hosts = list(results)
    report.hosts_probed = len(report.hosts)
    logger.info(
        "Discovery probe %s: probed=%s reachable=%s truncated=%s",
        cidr,
        report.hosts_probed,
        len(report.reachable),
        report.truncated,
    )
    return report
