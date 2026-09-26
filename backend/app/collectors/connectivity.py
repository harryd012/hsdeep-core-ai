"""
Connectivity collector (ICMP / TCP / DNS).

Agentless reachability + latency monitoring for any host responding to ICMP
echo, a TCP port probe, or a DNS query. This implements the Section 16
"agentless monitoring" / ICMP / TCP / DNS protocol path: no vendor
integration and no on-box agent - only an IP/hostname and (for TCP) a port.

Connection details from ``MonitoringSourceConnectionInfo``:
    hostname -> target host / IP
    port     -> TCP port to probe (only when checks includes 'tcp')
    config["connectivity"] -> {
        "checks": ["icmp", "tcp", "dns"]   (default ["icmp"])
        "timeout_seconds": 3.0
        "count": 3                          (icmp probes for averaging)
    }

Implemented with the Python standard library only (subprocess ping, socket
TCP connect, socket.getaddrinfo DNS) so it works on any platform and in
air-gapped/offline environments with no extra dependencies.

Documented limitations (see docs/DEVICE_SUPPORT_MATRIX.md):
  * ping shells out to the platform binary with ``-n`` (Windows) / ``-c``
    (POSIX); latency/packet-loss parsing is best-effort per platform.
  * blocking socket work runs in worker threads via asyncio.to_thread so the
    event loop is never blocked; DNS uses the system resolver.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import socket
import subprocess
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedSensorReading,
)

logger = logging.getLogger(__name__)

DEFAULT_CHECKS = ("icmp",)
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_COUNT = 3
_VALID_CHECKS = {"icmp", "tcp", "dns"}


@CollectorRegistry.register("connectivity")
class ConnectivityCollector(BaseCollector):
    display_name = "Connectivity (ICMP/TCP/DNS)"
    implemented = True

    def _host(self) -> str:
        if not self.source.hostname:
            raise CollectorConfigError(
                "Connectivity config missing required field: hostname"
            )
        return self.source.hostname

    def _cnf(self) -> dict[str, Any]:
        return (self.source.config or {}).get("connectivity", {}) or {}

    def _timeout(self) -> float:
        try:
            return float(self._cnf().get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS

    def _count(self) -> int:
        try:
            return int(self._cnf().get("count", DEFAULT_COUNT))
        except (TypeError, ValueError):
            return DEFAULT_COUNT

    def _checks(self) -> list[str]:
        checks = self._cnf().get("checks") or list(DEFAULT_CHECKS)
        out = [c for c in checks if c in _VALID_CHECKS]
        if not out:
            raise CollectorConfigError(
                f"Connectivity config: no valid check in {checks!r}; "
                f"supported: {sorted(_VALID_CHECKS)}"
            )
        return out

    async def authenticate(self) -> ConnectionCheck:
        try:
            self._host()
            self._checks()
        except CollectorConfigError as exc:
            return ConnectionCheck(success=False, message=str(exc))
        return ConnectionCheck(success=True, message="Configuration valid.")

    async def collect(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for check in self._checks():
            if check == "icmp":
                results["icmp"] = await _icmp_result(
                    self._host(), self._count(), self._timeout()
                )
            elif check == "tcp":
                port = self.source.port or 443
                results["tcp"] = await _tcp_result(self._host(), port, self._timeout())
            elif check == "dns":
                results["dns"] = await _dns_result(self._host(), self._timeout())
        return results

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        hostname = self._host()
        statuses: list[str] = []
        for key in ("icmp", "tcp", "dns"):
            entry = raw.get(key)
            if entry is None:
                continue
            statuses.append("up" if entry.get("ok") else "down")
        device_status = (
            "up" if statuses and all(s == "up" for s in statuses) else
            ("down" if statuses else "unknown")
        )

        device = NormalizedDevice(
            external_id=hostname,
            name=hostname,
            device_type="other",
            hostname=hostname,
            ip_address=self.source.ip_address or hostname,
            status=device_status,
        )
        return NormalizedBundle(devices=[device], sensors=_latency_sensors(raw, hostname))


def _latency_sensors(raw: dict[str, Any], hostname: str) -> list[NormalizedSensorReading]:
    """Build latency / packet-loss sensors from an ICMP result (pure)."""
    sensors: list[NormalizedSensorReading] = []
    icmp = raw.get("icmp") or {}
    if not icmp.get("ok"):
        return sensors
    if icmp.get("latency_ms") is not None:
        sensors.append(NormalizedSensorReading(
            external_id="latency_ms",
            name="RTT (ms)",
            metric_type="latency",
            value=icmp.get("latency_ms"),
            unit="ms",
            status="up",
            device_external_id=hostname,
        ))
    if icmp.get("packet_loss_pct") is not None:
        sensors.append(NormalizedSensorReading(
            external_id="packet_loss_pct",
            name="Packet loss (%)",
            metric_type="packet_loss",
            value=icmp.get("packet_loss_pct"),
            unit="%",
            status="up",
            device_external_id=hostname,
        ))
    return sensors


# ---------------------------------------------------------------------------
# Check implementations + pure parsing helpers (unit-tested directly)
# ---------------------------------------------------------------------------

def _run_ping(host: str, count: int, timeout: float, windows: bool) -> str:
    try:
        cmd = (["ping", "-n", str(count), "-w", str(int(timeout * 1000)), host]
               if windows else
               ["ping", "-c", str(count), "-W", str(int(timeout)), host])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Connectivity ping failed host=%s err=%s", host, exc)
        return ""


async def _icmp_result(host: str, count: int, timeout: float) -> dict[str, Any]:
    windows = platform.system().lower() == "windows"
    output = await asyncio.to_thread(_run_ping, host, count, timeout, windows)
    return parse_ping_output(output, windows=windows)


def parse_ping_output(output: str, windows: bool = True) -> dict[str, Any]:
    """Parse ping stdout into {ok, latency_ms, packet_loss_pct}.

    Best-effort per platform; returns latency=None / packet_loss=None when a
    pattern can't be found. Intentionally conservative (no fabricated data).
    """
    if not output:
        return {"ok": False, "latency_ms": None, "packet_loss_pct": None}
    lower = output.lower()

    if windows:
        ok = "reply from" in lower
        loss_pct = None
        m = re.search(r"lost\s*=\s*(\d+)\s*\(\s*(\d+)%?\s*loss", output, re.I)
        if m:
            loss_pct = _to_float(m.group(2))
        latency = None
        m = re.search(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", lower)
        if m:
            latency = _to_float(m.group(1))
    else:
        ok = "0% packet loss" in lower or "1 received" in lower
        loss_pct = None
        m = re.search(r"([\d.]+)%\s*packet loss", lower)
        if m:
            loss_pct = _to_float(m.group(1))
        latency = None
        m = re.search(r"rtt[^=]*=\s*[\d.]+/([\d.]+)", lower)
        if m:
            latency = _to_float(m.group(1))

    if loss_pct is not None and loss_pct >= 100:
        ok = False
    if not ok:
        latency = None
    return {"ok": ok, "latency_ms": latency, "packet_loss_pct": loss_pct}


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _tcp_result(host: str, port: int, timeout: float) -> dict[str, Any]:
    ok = await asyncio.to_thread(_tcp_probe, host, port, timeout)
    return {"ok": ok, "port": port}


def _dns_probe(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


async def _dns_result(host: str, timeout: float) -> dict[str, Any]:
    ok = await asyncio.to_thread(_dns_probe, host)
    return {"ok": ok}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

