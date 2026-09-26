"""Server-side probe layer: real protocol probes -> DiscoveryEvidence.

This is the collector capability the discovery API calls. It performs genuine
network probes (no fixtures, no seeded results) and returns the normalized
evidence contract the recognition core consumes.

Protocol notes:
  * SNMP is UDP/161. A closed TCP/161 port proves nothing about SNMP, so this
    layer never uses TCP to decide SNMP availability.
  * Every probe is independently bounded and independently reported, so a
    timeout on one protocol never invalidates another.
  * Nothing here logs or returns credentials.
"""
from __future__ import annotations

import logging
import platform
import ipaddress
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any

from app.services.discovery.evidence import DiscoveryEvidence

logger = logging.getLogger(__name__)

OUTCOME_SUCCESS = "success"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_UNAUTHORIZED = "unauthorized"
OUTCOME_NO_RESPONSE = "no_response"
OUTCOME_NETWORK_UNREACHABLE = "network_unreachable"
OUTCOME_CONFIGURATION_ERROR = "configuration_error"

DEFAULT_SNMP_OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
}

# Ports worth probing for identity evidence: (port, capability token).
IDENTITY_PORTS: tuple[tuple[int, str], ...] = ((22, "ssh"), (443, "api"), (80, "api"))


@dataclass
class ProbeResult:
    """One protocol probe against one target."""

    protocol: str
    target: str
    outcome: str
    detail: str = ""
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.outcome == OUTCOME_SUCCESS

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "target": self.target,
            "outcome": self.outcome,
            "detail": self.detail,
            "values": dict(self.values),
        }


@dataclass
class ProbeBundle:
    """All probe results for one target plus the evidence they produced."""

    target: str
    results: list[ProbeResult] = field(default_factory=list)
    evidence: DiscoveryEvidence | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "probe_results": {r.protocol: r.outcome for r in self.results},
            "probes": [r.as_dict() for r in self.results],
        }


def icmp_probe(target: str, timeout_seconds: int = 1) -> ProbeResult:
    """Real ICMP echo. Uses the OS ping binary (no extra dependency)."""
    windows = platform.system().lower() == "windows"
    args = ["ping", "-n" if windows else "-c", "1"]
    args += ["-w", str(timeout_seconds * 1000)] if windows else ["-W", str(timeout_seconds)]
    args.append(target)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout_seconds + 4
        )
    except Exception as exc:
        return ProbeResult("icmp", target, OUTCOME_NETWORK_UNREACHABLE, str(exc))
    if proc.returncode == 0:
        return ProbeResult("icmp", target, OUTCOME_SUCCESS, "echo reply received")
    return ProbeResult("icmp", target, OUTCOME_NO_RESPONSE, "no echo reply")


def tcp_probe(target: str, port: int, timeout: float = 2.0) -> ProbeResult:
    """Real TCP connect. Reports reachability of that TCP service only."""
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return ProbeResult(f"tcp/{port}", target, OUTCOME_SUCCESS, "connected")
    except socket.timeout:
        return ProbeResult(f"tcp/{port}", target, OUTCOME_TIMEOUT, "connect timed out")
    except OSError as exc:
        return ProbeResult(f"tcp/{port}", target, OUTCOME_NO_RESPONSE, str(exc))


def ssh_banner_probe(target: str, timeout: float = 3.0) -> ProbeResult:
    """Read the SSH banner. Evidence only; this never authenticates."""
    try:
        with socket.create_connection((target, 22), timeout=timeout) as sock:
            sock.settimeout(timeout)
            raw = sock.recv(256).decode("utf-8", "replace").strip()
    except socket.timeout:
        return ProbeResult("ssh", target, OUTCOME_TIMEOUT, "banner read timed out")
    except OSError as exc:
        return ProbeResult("ssh", target, OUTCOME_NO_RESPONSE, str(exc))
    if not raw:
        return ProbeResult("ssh", target, OUTCOME_NO_RESPONSE, "empty banner")
    return ProbeResult("ssh", target, OUTCOME_SUCCESS, raw[:120], {"banner": raw[:200]})


async def snmp_probe(
    target: str,
    *,
    community: str = "public",
    version: int = 1,
    timeout: float = 2.0,
    retries: int = 1,
    port: int = 161,
) -> ProbeResult:
    """Real SNMP GET over UDP (never TCP) using the collector's transport API."""
    try:
        from pysnmp.hlapi.asyncio import (  # type: ignore
            CommunityData, ContextData, ObjectIdentity, ObjectType,
            SnmpEngine, UdpTransportTarget, getCmd,
        )
    except Exception as exc:
        return ProbeResult(
            "snmp", target, OUTCOME_CONFIGURATION_ERROR, f"pysnmp unavailable: {exc}"
        )

    try:
        transport = UdpTransportTarget(
            (target, port), timeout=timeout, retries=retries
        )
        err_ind, err_stat, err_idx, var_binds = await getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=version),
            transport,
            ContextData(),
            *[ObjectType(ObjectIdentity(o)) for o in DEFAULT_SNMP_OIDS.values()],
        )
    except Exception as exc:
        return ProbeResult(
            "snmp", target, OUTCOME_NETWORK_UNREACHABLE, f"{type(exc).__name__}: {exc}"
        )

    if err_ind:
        text = str(err_ind)
        upper = text.upper()
        if "TIMEOUT" in upper:
            return ProbeResult("snmp", target, OUTCOME_TIMEOUT, text)
        if "UNREACHABLE" in upper:
            return ProbeResult("snmp", target, OUTCOME_NETWORK_UNREACHABLE, text)
        return ProbeResult("snmp", target, OUTCOME_NO_RESPONSE, text)

    if err_stat:
        text = f"{err_stat.prettyPrint()} (index {err_idx})"
        if "AUTH" in str(err_stat).upper():
            return ProbeResult("snmp", target, OUTCOME_UNAUTHORIZED, text)
        return ProbeResult("snmp", target, OUTCOME_NO_RESPONSE, text)

    values: dict[str, str] = {}
    for oid, name in DEFAULT_SNMP_OIDS.items():
        for vb in var_binds:
            if str(vb[0]) == oid:
                values[name] = vb[1].prettyPrint()
    if not values:
        return ProbeResult("snmp", target, OUTCOME_NO_RESPONSE, "empty varbind list")
    return ProbeResult("snmp", target, OUTCOME_SUCCESS, "", values)


async def probe_target(
    target: str,
    *,
    protocols: list[str] | None = None,
    community: str = "public",
    snmp_version: int = 1,
) -> ProbeBundle:
    """Probe one target with the requested protocols and build its evidence.

    ICMP runs first: when it fails, deeper probes are pointless and are skipped
    rather than left to time out individually.
    """
    wanted = {p.lower() for p in (protocols or ["icmp", "snmp", "https", "ssh"])}
    bundle = ProbeBundle(target=target)

    icmp = icmp_probe(target)
    bundle.results.append(icmp)

    evidence = DiscoveryEvidence(
        address=target,
        transport="icmp",
        source="discovery-api",
        reachable=icmp.succeeded,
        capabilities=["icmp"] if icmp.succeeded else [],
    )

    if icmp.succeeded:
        # Identity-bearing TCP services.
        for port, token in IDENTITY_PORTS:
            if wanted & {"https", "http", "api", f"tcp/{port}"} or port in (22, 443, 80):
                res = tcp_probe(target, port)
                bundle.results.append(res)
                if res.succeeded:
                    evidence.capabilities.append(token)

        if "ssh" in wanted:
            banner = ssh_banner_probe(target)
            bundle.results.append(banner)
            if banner.succeeded:
                if "ssh" not in evidence.capabilities:
                    evidence.capabilities.append("ssh")
                evidence.raw_evidence["ssh_banner"] = banner.values.get("banner")

        if "snmp" in wanted:
            snmp = await snmp_probe(
                target, community=community, version=snmp_version
            )
            bundle.results.append(snmp)
            if snmp.succeeded:
                evidence.transport = "snmp"
                evidence.sys_descr = snmp.values.get("sysDescr")
                evidence.sys_object_id = snmp.values.get("sysObjectID")
                evidence.hostname = snmp.values.get("sysName") or evidence.hostname
                if "snmp" not in evidence.capabilities:
                    evidence.capabilities.append("snmp")

    bundle.evidence = evidence
    return bundle


def expand_targets(target: str) -> list[str]:
    """Expand a single address, hostname, or CIDR into probe targets.

    CIDRs are bounded so a large range cannot produce an unbounded probe burst.
    """
    text = (target or "").strip()
    if not text:
        return []
    if "/" in text:
        network = ipaddress.ip_network(text, strict=False)
        return [str(ip) for ip in network.hosts()][:MAX_CIDR_HOSTS]
    return [text]


MAX_CIDR_HOSTS = 256

