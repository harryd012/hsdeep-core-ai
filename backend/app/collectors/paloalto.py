"""Palo Alto Networks PAN-OS diagnostic collector.

Implements the Collector Framework interface for PAN-OS firewalls using the
read-only PAN-OS XML/REST API (HTTPS, default port 443). Matches PAN-OS 10.x
and 11.x API shapes.

Safety rules (never violated):
  - Never log usernames, passwords, API keys, or tenant identifiers.
  - TLS verification is ON by default (verify_ssl defaults to True).
  - Explicit connect/read timeouts; no unbounded hangs.
  - Rate limits (429) and transient 503/504 are retried with capped
    exponential backoff. Client errors 400/401/403/404 are NEVER retried.
  - Responses are normalized into the shared NormalizedBundle schema.
  - This collector is strictly read-only: it never issues a commit, push,
    reboot, service restart, or configuration change.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedInterfaceAddress,
    NormalizedFirewallStatus,
    NormalizedLicence,
    NormalizedRoute,
    NormalizedVpnTunnel,
)
from app.core.ip_addressing import (
    normalise_vrf,
    parse_interface_address,
    parse_next_hop,
    parse_route_destination,
)

logger = logging.getLogger(__name__)

_STATUS_TIMEOUT = 8.0
_TABLE_TIMEOUT = 25.0
_CONN_TIMEOUT = 5.0
_MAX_ATTEMPTS = 3
_RETRY_BASE = 1.0
_RETRY_MAX = 8.0
_RETRY_STATUS = (429, 503, 504)


@CollectorRegistry.register("paloalto")
class PaloAltoCollector(BaseCollector):
    """Read-only PAN-OS collector behind the provider-agnostic interface."""

    display_name = "Palo Alto Networks PAN-OS"
    implemented = True
    category = "firewall"
    firewall_capabilities = ("status", "vpn", "licences")

    def _require_config(self) -> None:
        missing = [
            key
            for key, value in (
                ("hostname", self.source.hostname),
                ("api_key or password", self.source.password or self.source.api_key),
            )
            if not value
        ]
        if missing:
            raise CollectorConfigError(
                f"Palo Alto config missing required field(s): {missing}"
            )

    def _base_url(self) -> str:
        protocol = self.source.protocol or "https"
        host = self.source.hostname
        port = self.source.port
        default_port = 443 if protocol == "https" else 80
        if port and port != default_port:
            return f"{protocol}://{host}:{port}"
        return f"{protocol}://{host}"

    def _auth_headers(self) -> dict[str, str]:
        token = self.source.api_key
        if token:
            return {"X-PAN-KEY": token}
        if self.source.password:
            return {"Authorization": self._basic_auth()}
        return {}

    def _basic_auth(self) -> str:
        import base64

        creds = f"{self.source.username or ''}:{self.source.password or ''}"
        return "Basic " + base64.b64encode(creds.encode()).decode()

    async def _op(self, cmd_xml: str, timeout: float = _TABLE_TIMEOUT) -> httpx.Response:
        """POST an op-mode XML command. Retries only transient failures."""
        url = f"{self._base_url()}/api/?type=op"
        payload = f"<request><cmd>{cmd_xml}</cmd></request>"
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(
                    verify=self.source.verify_ssl,
                    timeout=httpx.Timeout(timeout, connect=_CONN_TIMEOUT),
                ) as client:
                    response = await client.post(
                        url, content=payload, headers=self._auth_headers()
                    )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Palo Alto op failed: source_id=%s attempt=%s/%s type=%s",
                    self.source.id, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(min(_RETRY_BASE * 2 ** (attempt - 1), _RETRY_MAX))
                continue

            if response.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "Palo Alto rate-limited HTTP %s: source_id=%s attempt=%s/%s",
                    response.status_code, self.source.id, attempt, _MAX_ATTEMPTS,
                )
                await asyncio.sleep(min(_RETRY_BASE * 2 ** (attempt - 1), _RETRY_MAX))
                continue

            return response

        if last_exc is not None:
            raise last_exc
        assert response is not None
        return response

    async def authenticate(self) -> ConnectionCheck:
        """Validate credentials via read-only 'show system info'."""
        self._require_config()
        try:
            resp = await self._op("<show><system><info/></system></show>", _STATUS_TIMEOUT)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(success=False, message=f"Could not reach Palo Alto: {exc}")

        if resp.status_code == 401:
            return ConnectionCheck(success=False, message="Palo Alto: Unauthorized (bad credentials).")
        if resp.status_code == 200:
            return ConnectionCheck(success=True, message="Connected to Palo Alto.")
        return ConnectionCheck(success=False, message=f"Palo Alto responded with HTTP {resp.status_code}.")

    async def collect(self) -> dict[str, Any]:
        """Gather system info, interface counters, and HA state (read-only)."""
        self._require_config()
        result: dict[str, Any] = {}
        jobs = {
            "system": "<show><system><info/></system></show>",
            "interface": "<show><interface><all/></interface></show>",
            "highavailability": "<show><high-availability><state/></high-availability></show>",
            "routing": "<show><routing><route/></routing></show>",
            # Firewall gateway tables (all read-only op commands).
            "sessions": "<show><session><info/></session></show>",
            "resources": "<show><system><resources/></system></show>",
            "vpn": "<show><vpn><flow/></vpn></show>",
            "licences": "<request><license><info/></license></request>",
        }
        for key, cmd in jobs.items():
            try:
                resp = await self._op(cmd)
                # PAN-OS answers an unsupported or refused command with HTTP 200
                # and status="error"; that is "unavailable", never an empty table.
                ok = resp.status_code == 200 and 'status="error"' not in resp.text[:300]
                result[key] = resp.text if ok else ""
                result[f"{key}_status"] = "collected" if ok else "unavailable"
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning(
                    "Palo Alto collect %s failed: source_id=%s type=%s",
                    key, self.source.id, type(exc).__name__,
                )
                result[key] = ""
                result[f"{key}_status"] = "failed"
        return result

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Convert raw PAN-OS XML into NormalizedDevice/Interface rows.

        Malformed or missing XML yields empty-but-honest structures; we never
        invent hostnames, serials, or versions that PAN-OS did not report.
        """
        devices: list[NormalizedDevice] = []
        interfaces: list[NormalizedInterface] = []

        info = _parse_xml(raw.get("system", ""))
        hostname = info.get("hostname") or self.source.hostname or "paloalto-device"
        device = NormalizedDevice(
            external_id=info.get("serial-number") or hostname,
            name=hostname,
            device_type="firewall",
            vendor="Palo Alto Networks",
            model=info.get("model"),
            os_version=info.get("sw-version"),
            hostname=hostname,
            ip_address=self.source.ip_address or self.source.hostname,
            serial_number=info.get("serial-number"),
            status="up" if info else "unknown",
            custom_metadata={
                "panos_version": info.get("sw-version"),
                "uptime": info.get("uptime"),
                "family": info.get("family"),
            },
        )
        devices.append(device)

        for iface in _parse_interfaces(raw.get("interface", ""), device.external_id):
            interfaces.append(iface)

        addresses = _parse_addresses(raw.get("interface", ""), device.external_id)
        routes = _parse_routes(raw.get("routing", ""), device.external_id)
        l3_status: dict[str, str] = {}
        for table, key, rows in (("addresses", "interface", addresses), ("routes", "routing", routes)):
            status = raw.get(f"{key}_status")
            if status == "collected":
                l3_status[table] = "collected" if rows else "empty"
            elif status is not None:
                l3_status[table] = status

        bundle = NormalizedBundle(
            devices=devices,
            interfaces=interfaces,
            metrics=[],
            addresses=addresses,
            routes=routes,
            l3_status=l3_status,
        )
        if "sessions_status" in raw:
            _normalize_firewall(raw, bundle, device.external_id, device.os_version)
        return bundle


def _parse_xml(text: str) -> dict[str, str]:
    """Parse PAN-OS XML into a flat k->v dict. Returns {} on bad input."""
    if not text or not text.strip():
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    out: dict[str, str] = {}
    for child in root.iter():
        tag = child.tag.split("}")[-1]
        if child.text and child.text.strip():
            out[tag] = child.text.strip()
    return out


def _parse_interfaces(text: str, device_external_id: str) -> list[NormalizedInterface]:
    """Parse '<interface>' XML into NormalizedInterface rows."""
    if not text or not text.strip():
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    out: list[NormalizedInterface] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("ethernet", "logical", "ifnet"):
            continue
        name = node.get("name") or (node.findtext("name") or "").strip()
        if not name:
            continue
        out.append(
            NormalizedInterface(
                device_external_id=device_external_id,
                external_id=name,
                name=name,
                admin_status=(node.findtext("state") or "unknown").strip() or "unknown",
                oper_status=(node.findtext("link-status") or "unknown").strip() or "unknown",
            )
        )
    return out


def _xml_root(text: str):
    if not text or not text.strip():
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def _parse_addresses(text: str, device_external_id: str) -> list[NormalizedInterfaceAddress]:
    """``ifnet`` entries of ``show interface all``: ``<ip>10.0.0.1/24</ip>``
    (or ``N/A``), secondaries as ``<addr><member>``, virtual router in
    ``<fwd>vr:NAME</fwd>``."""
    root = _xml_root(text)
    if root is None:
        return []
    out: list[NormalizedInterfaceAddress] = []
    for ifnet in root.iter("ifnet"):
        for entry in ifnet.iter("entry"):
            name = (entry.findtext("name") or "").strip()
            if not name:
                continue
            fwd = (entry.findtext("fwd") or "").strip()
            vrf = normalise_vrf(fwd[3:] if fwd.startswith("vr:") else None)
            values = [((entry.findtext("ip") or "").strip(), True)]
            addr = entry.find("addr")
            if addr is not None:
                values += [((m.text or "").strip(), False) for m in addr.iter("member")]
            seen: set[str] = set()
            for value, primary in values:
                parsed = parse_interface_address(value)
                if parsed is None or parsed.address in seen:
                    continue
                seen.add(parsed.address)
                out.append(NormalizedInterfaceAddress(
                    device_external_id=device_external_id,
                    interface_external_id=name,
                    interface_name=name,
                    address=parsed.address,
                    prefix_length=parsed.prefix_length,
                    vrf=vrf,
                    is_primary=primary and len(seen) == 1,
                ))
    return out


def _route_protocol(flags: str) -> str | None:
    """PAN-OS route flags: A active, C connect, S static, B bgp, O ospf,
    H host. Inactive and host entries are not topology."""
    letters = set(flags.replace(" ", "").upper())
    if "A" not in letters or "H" in letters:
        return None
    if "C" in letters:
        return "connected"
    if "S" in letters:
        return "static"
    if "B" in letters:
        return "bgp"
    if "O" in letters or any(f.startswith("O") for f in flags.split()):
        return "ospf"
    return "other"


def _parse_routes(text: str, device_external_id: str) -> list[NormalizedRoute]:
    root = _xml_root(text)
    if root is None:
        return []
    out: list[NormalizedRoute] = []
    for entry in root.iter("entry"):
        destination = parse_route_destination((entry.findtext("destination") or "").strip())
        if destination is None:
            continue
        protocol = _route_protocol(entry.findtext("flags") or "")
        if protocol is None:
            continue
        metric = (entry.findtext("metric") or "").strip()
        out.append(NormalizedRoute(
            device_external_id=device_external_id,
            destination=destination[0],
            prefix_length=destination[1],
            protocol=protocol,
            next_hop=parse_next_hop((entry.findtext("nexthop") or "").strip()),
            outgoing_interface=(entry.findtext("interface") or "").strip() or None,
            metric=int(metric) if metric.isdigit() else None,
            vrf=normalise_vrf(entry.findtext("virtual-router")),
        ))
    return out


# -- Firewall gateway ---------------------------------------------------------


def _root(text: str) -> ET.Element | None:
    if not text or not text.strip():
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def _text(node: ET.Element | None, path: str) -> str | None:
    found = node.find(path) if node is not None else None
    value = found.text.strip() if found is not None and found.text else None
    return value or None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_ha(text: str) -> dict[str, str | None]:
    """``show high-availability state``. HA disabled is "standalone"."""
    root = _root(text)
    result = root.find("result") if root is not None else None
    if result is None:
        return {}
    if (_text(result, "enabled") or "").lower() == "no":
        return {"ha_mode": "standalone"}
    group = result.find("group")
    mode = (_text(group, "mode") or "").lower()
    sync = (_text(group, "running-sync") or "").lower()
    peer_conn = (_text(group, "peer-info/conn-status") or "").lower()
    in_sync = sync == "synchronized" and peer_conn != "down"
    return {
        "ha_mode": mode if mode in ("active-passive", "active-active") else None,
        "ha_role": (_text(group, "local-info/state") or "").lower() or None,
        "ha_sync": ("in_sync" if in_sync else "out_of_sync") if sync or peer_conn else None,
        "ha_peer_serial": _text(group, "peer-info/serial-num"),
    }


_CPU_IDLE = re.compile(r"%Cpu\(s\):.*?([\d.]+)\s*id")
_MEM = re.compile(r"MiB Mem\s*:\s*([\d.]+)\s*total.*?([\d.]+)\s*free")
_MEM_AVAIL = re.compile(r"([\d.]+)\s*avail Mem")


def parse_resources(text: str) -> tuple[float | None, float | None]:
    """``show system resources`` returns ``top`` output in <result>."""
    root = _root(text)
    body = _text(root, "result") if root is not None else None
    if not body:
        return None, None
    cpu = mem = None
    if m := _CPU_IDLE.search(body):
        cpu = round(100.0 - float(m.group(1)), 1)
    if m := _MEM.search(body):
        total = float(m.group(1))
        avail = _MEM_AVAIL.search(body)
        free = float(avail.group(1)) if avail else float(m.group(2))
        if total > 0:
            mem = round((total - free) / total * 100, 1)
    return cpu, mem


def parse_vpn_flow(text: str, device_external_id: str) -> list[NormalizedVpnTunnel]:
    root = _root(text)
    out: list[NormalizedVpnTunnel] = []
    if root is None:
        return out
    for entry in root.iter("entry"):
        name, state = _text(entry, "name"), _text(entry, "state")
        if not name or state is None:
            continue
        out.append(NormalizedVpnTunnel(
            device_external_id=device_external_id, name=name,
            status="up" if state.lower() == "active" else "down",
            remote_gateway=_text(entry, "peerip"),
        ))
    return out


def parse_licences(text: str, device_external_id: str) -> list[NormalizedLicence]:
    root = _root(text)
    out: list[NormalizedLicence] = []
    if root is None:
        return out
    for entry in root.iter("entry"):
        feature = _text(entry, "feature")
        if not feature:
            continue
        expires_raw = _text(entry, "expires") or ""
        try:
            expires = datetime.strptime(expires_raw, "%B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            expires = None  # "Never" or an unknown format
        expired = (_text(entry, "expired") or "").lower()
        out.append(NormalizedLicence(
            device_external_id=device_external_id, feature=feature, expires_at=expires,
            expired=True if expired == "yes" else (False if expired == "no" else None),
        ))
    return out


def _table_status(raw_status: str | None, has_rows: bool) -> str:
    if raw_status == "collected":
        return "collected" if has_rows else "empty"
    return raw_status or "unavailable"


def _normalize_firewall(raw: dict[str, Any], bundle: NormalizedBundle, device_external_id: str,
                        firmware: str | None) -> None:
    ha = parse_ha(raw.get("highavailability", ""))
    session_root = _root(raw.get("sessions", ""))
    cpu, mem = parse_resources(raw.get("resources", ""))
    status = NormalizedFirewallStatus(
        device_external_id=device_external_id,
        **ha,
        session_count=_int(_text(session_root, "result/num-active")),
        session_limit=_int(_text(session_root, "result/num-max")),
        cpu_percent=cpu,
        memory_percent=mem,
        firmware_version=firmware,
    )
    reported = any(
        getattr(status, f) is not None for f in ("ha_mode", "session_count", "cpu_percent", "memory_percent")
    )
    if reported:
        bundle.firewalls.append(status)
    reads = [raw.get(f"{k}_status") for k in ("highavailability", "sessions", "resources")]
    bundle.firewall_status["status"] = (
        "collected" if reported else "failed" if "failed" in reads else "empty" if "collected" in reads else "unavailable"
    )
    bundle.vpn_tunnels.extend(parse_vpn_flow(raw.get("vpn", ""), device_external_id))
    bundle.firewall_status["vpn"] = _table_status(raw.get("vpn_status"), bool(bundle.vpn_tunnels))
    bundle.licences.extend(parse_licences(raw.get("licences", ""), device_external_id))
    bundle.firewall_status["licences"] = _table_status(raw.get("licences_status"), bool(bundle.licences))
