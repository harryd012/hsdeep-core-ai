"""
FortiGate Next-Generation Firewall collector.

Collector Framework implementation for FortiGate using FortiGate REST API v2.
Documented at https://docs.fortinet.com/document/fortigate/latest/administration-guide/

Connection details from `MonitoringSourceConnectionInfo`:
    hostname/port/protocol -> compose the FortiGate base URL
    username                -> FortiGate admin username
    password                -> FortiGate admin password (encrypted at rest)
    api_key                 -> FortiGate API token (preferred over password)
    verify_ssl              -> TLS verification (defaults to False for self-signed dev certs)

Single-file collector matching the PRTG pattern. `authenticate()`/`collect()`/`normalize()`
satisfy `BaseCollector`'s abstract methods; `save()` is not overridden - it relies on
`BaseCollector`'s default via `BundleSaver`.

Logging discipline: logs via `logging.getLogger(__name__)` with source_id/path/outcome only,
never username/password/api_key (matching SecretCrypto no-log rule).
"""
from __future__ import annotations

import asyncio
import logging
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
    NormalizedFirewallStatus,
    NormalizedInterface,
    NormalizedInterfaceAddress,
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

_STATUS_TIMEOUT = 10.0
_TABLE_TIMEOUT = 20.0
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


@CollectorRegistry.register("fortigate")
class FortiGateCollector(BaseCollector):
    display_name = "Fortinet FortiGate"
    implemented = True
    category = "firewall"
    firewall_capabilities = ("status", "vpn", "licences")

    # -- config / URL helpers -------------------------------------------------

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
            raise CollectorConfigError(f"FortiGate config missing required field(s): {missing}")

    def _base_url(self) -> str:
        protocol = self.source.protocol or "https"
        hostname = self.source.hostname
        port = self.source.port
        default_port = 443 if protocol == "https" else 80
        if port and port != default_port:
            return f"{protocol}://{hostname}:{port}"
        return f"{protocol}://{hostname}"

    def _auth_headers(self) -> dict[str, str]:
        """Return auth headers. FortiGate uses a bearer-style API token via
        the X-Auth-Token header. Prefer the dedicated api_key field, falling
        back to password (some deployments store the token there)."""
        token = self.source.api_key or self.source.password
        if token:
            return {"X-Auth-Token": token}
        return {}

    # -- shared HTTP helper -------------------------------------------------

    async def _request(self, path: str, params: dict[str, Any] | None = None, timeout: float = _TABLE_TIMEOUT) -> httpx.Response:
        """GET `path` with optional `params`, retrying transient failures.
        Never logs auth headers or sensitive params."""
        url = f"{self._base_url()}{path}"
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                headers = self._auth_headers()
                async with httpx.AsyncClient(verify=self.source.verify_ssl, timeout=timeout) as client:
                    response = await client.get(url, headers=headers, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "FortiGate request failed: source_id=%s path=%s attempt=%s/%s error_type=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise
            else:
                if response.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "FortiGate request returned server error: source_id=%s path=%s attempt=%s/%s status=%s",
                        self.source.id, path, attempt, _MAX_ATTEMPTS, response.status_code,
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                logger.debug(
                    "FortiGate request completed: source_id=%s path=%s attempt=%s/%s status=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, response.status_code,
                )
                return response

        if last_exc is not None:
            raise last_exc
        assert response is not None  # pragma: no cover
        return response

    # -- BaseCollector interface -------------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Test authentication against FortiGate /api/v2/cmdb/system/status"""
        self._require_config()
        try:
            resp = await self._request("/api/v2/cmdb/system/status", timeout=_STATUS_TIMEOUT)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(success=False, message=f"Could not reach FortiGate: {exc}")

        if resp.status_code == 200:
            return ConnectionCheck(success=True, message="Connected to FortiGate.")
        if resp.status_code == 401:
            return ConnectionCheck(success=False, message="FortiGate: Unauthorized (invalid credentials).")
        return ConnectionCheck(success=False, message=f"FortiGate responded with HTTP {resp.status_code}.")

    async def collect(self) -> dict[str, Any]:
        """Collect system status and interface information from FortiGate."""
        self._require_config()
        result: dict[str, Any] = {}

        # System status (hostname, version, mode)
        try:
            status_resp = await self._request("/api/v2/cmdb/system/status", timeout=_STATUS_TIMEOUT)
            result["status"] = status_resp.json() if status_resp.status_code == 200 else {}
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning("FortiGate collect(): system/status failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["status"] = {}

        # Interface list (propagate failures - interfaces are core data)
        try:
            iface_resp = await self._request("/api/v2/cmdb/system/interface", timeout=_TABLE_TIMEOUT)
            iface_resp.raise_for_status()
            result["interfaces"] = iface_resp.json()
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning("FortiGate collect(): system/interface failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["interfaces"] = {"results": []}
            result["interfaces_failed"] = True

        # Routing table (L3 topology). Optional: a missing permission or an
        # older FortiOS without the endpoint is "unavailable", never an error.
        try:
            route_resp = await self._request("/api/v2/monitor/router/ipv4", timeout=_TABLE_TIMEOUT)
            if route_resp.status_code == 200:
                result["routes"] = route_resp.json()
                result["routes_status"] = "collected"
            else:
                result["routes_status"] = "unavailable"
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning("FortiGate collect(): router/ipv4 failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["routes_status"] = "failed"

        # Firewall gateway tables. Each is independently statused: a missing
        # permission or endpoint is "unavailable", a transport error "failed".
        result["fw"], result["fw_status"] = {}, {}
        for key, path, params in _FIREWALL_READS:
            try:
                resp = await self._request(path, params=params, timeout=_TABLE_TIMEOUT)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning("FortiGate collect(): %s failed: source_id=%s error_type=%s", path, self.source.id, type(exc).__name__)
                result["fw_status"][key] = "failed"
                continue
            if resp.status_code == 200:
                result["fw"][key] = resp.json()
                result["fw_status"][key] = "collected"
            else:
                result["fw_status"][key] = "unavailable"

        return result

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Convert raw FortiGate API response to NormalizedBundle.

        FortiGate single device (the firewall itself) with multiple interfaces.
        Each interface becomes a NormalizedInterface.
        """
        devices: list[NormalizedDevice] = []
        interfaces: list[NormalizedInterface] = []

        # Create single device entry for the FortiGate unit itself
        status = raw.get("status", {}).get("results", {})
        hostname = status.get("hostname", "fortigate-device")
        device = NormalizedDevice(
            external_id="1",  # FortiGate is typically single device
            name=hostname,
            device_type="firewall",
            vendor="Fortinet",
            model=status.get("model"),
            os_version=status.get("version"),
            hostname=hostname,
            ip_address=hostname,
            status="up",
        )
        devices.append(device)

        # Parse interfaces
        iface_results = raw.get("interfaces", {}).get("results", [])
        for idx, iface_row in enumerate(iface_results):
            if not iface_row.get("name"):
                continue

            # Derive interface status
            iface_status = "up" if iface_row.get("status") == "up" else "down"

            interface = NormalizedInterface(
                device_external_id="1",  # All interfaces belong to the single device
                external_id=iface_row["name"],
                name=iface_row["name"],
                admin_status=iface_status,
                oper_status=iface_status,
                mac_address=iface_row.get("mac"),
                speed_mbps=_parse_speed(iface_row.get("speed")),
                in_octets=iface_row.get("rx_bytes"),
                out_octets=iface_row.get("tx_bytes"),
                in_errors=iface_row.get("rx_errors"),
                out_errors=iface_row.get("tx_errors"),
            )
            interfaces.append(interface)

        addresses = _parse_addresses(iface_results)
        routes = _parse_routes(raw.get("routes", {}).get("results", []))

        l3_status: dict[str, str] = {}
        if "interfaces" in raw:
            if raw.get("interfaces_failed"):
                l3_status["addresses"] = "failed"
            else:
                l3_status["addresses"] = "collected" if addresses else "empty"
        routes_status = raw.get("routes_status")
        if routes_status == "collected":
            l3_status["routes"] = "collected" if routes else "empty"
        elif routes_status is not None:
            l3_status["routes"] = routes_status

        bundle = NormalizedBundle(
            devices=devices,
            interfaces=interfaces,
            addresses=addresses,
            routes=routes,
            l3_status=l3_status,
        )
        if "fw_status" in raw:
            _normalize_firewall(raw, bundle, local_serial=raw.get("status", {}).get("serial"))
        return bundle


def _parse_speed(speed_str: str | None) -> float | None:
    """Parse FortiGate speed string (e.g. '1000') to Mbps."""
    if not speed_str or speed_str == "0":
        return None
    try:
        return float(speed_str)
    except (ValueError, TypeError):
        return None


def _vrf(value: Any) -> str:
    """FortiOS VRF ids are integers; 0 is the global table."""
    return normalise_vrf(value)


def _fortios_address(value: Any, netmask: Any = None):
    """FortiOS writes ``"192.168.1.1 255.255.255.0"`` in one field; some
    exports split the mask into ``netmask``. Both are accepted."""
    if isinstance(value, list) and len(value) == 2:
        value, netmask = value
    if not isinstance(value, str):
        return None
    return parse_interface_address(value, netmask if " " not in value.strip() else None)


def _parse_addresses(iface_results: list[dict[str, Any]]) -> list[NormalizedInterfaceAddress]:
    out: list[NormalizedInterfaceAddress] = []
    for row in iface_results:
        name = row.get("name")
        if not name:
            continue
        vrf = _vrf(row.get("vrf"))
        candidates = [(row.get("ip"), row.get("netmask"), True)]
        for secondary in row.get("secondaryip") or []:
            if isinstance(secondary, dict):
                candidates.append((secondary.get("ip"), secondary.get("netmask"), False))
        for value, netmask, primary in candidates:
            parsed = _fortios_address(value, netmask)
            if parsed is None or parsed.address == "0.0.0.0":
                continue
            out.append(NormalizedInterfaceAddress(
                device_external_id="1",
                interface_external_id=name,
                interface_name=name,
                address=parsed.address,
                prefix_length=parsed.prefix_length,
                vrf=vrf,
                is_primary=primary,
            ))
    return out


_FORTIOS_ROUTE_TYPES = {
    "connect": "connected",
    "connected": "connected",
    "static": "static",
    "bgp": "bgp",
    "ospf": "ospf",
}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_routes(route_results: list[dict[str, Any]]) -> list[NormalizedRoute]:
    """``/api/v2/monitor/router/ipv4`` rows: ``ip_mask`` "0.0.0.0/0",
    ``type`` connect|static|bgp|ospf|kernel, ``gateway``, ``interface``."""
    out: list[NormalizedRoute] = []
    for row in route_results:
        if not isinstance(row, dict):
            continue
        destination = parse_route_destination(row.get("ip_mask"))
        if destination is None:
            continue
        protocol = _FORTIOS_ROUTE_TYPES.get(str(row.get("type", "")).lower(), "other")
        out.append(NormalizedRoute(
            device_external_id="1",
            destination=destination[0],
            prefix_length=destination[1],
            protocol=protocol,
            next_hop=parse_next_hop(row.get("gateway")),
            outgoing_interface=row.get("interface") or None,
            metric=_int_or_none(row.get("metric")),
            administrative_distance=_int_or_none(row.get("distance")),
            vrf=_vrf(row.get("vrf")),
        ))
    return out


# -- Firewall gateway ---------------------------------------------------------

#: (raw key, read-only monitor/cmdb path, query params)
_FIREWALL_READS: tuple[tuple[str, str, dict[str, str] | None], ...] = (
    ("ha", "/api/v2/cmdb/system/ha", None),
    ("ha_checksums", "/api/v2/monitor/system/ha-checksums", None),
    ("resources", "/api/v2/monitor/system/resource/usage", {"interval": "1-min"}),
    ("vpn", "/api/v2/monitor/vpn/ipsec", None),
    ("licences", "/api/v2/monitor/license/status", None),
)

_HA_MODES = {"standalone": "standalone", "a-p": "active-passive", "a-a": "active-active"}


def _results(payload: Any) -> Any:
    return payload.get("results") if isinstance(payload, dict) else None


def _current(resources: Any, key: str) -> float | None:
    """``resource/usage`` -> ``{"cpu": [{"interval": "1-min", "current": 3}]}``."""
    rows = resources.get(key) if isinstance(resources, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        try:
            return float(rows[0]["current"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _table(statuses: list[str | None], has_rows: bool) -> str:
    if has_rows:
        return "collected"
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "collected" for s in statuses):
        return "empty"
    return "unavailable"


def _normalize_firewall(raw: dict[str, Any], bundle: NormalizedBundle, *, local_serial: str | None) -> None:
    fw, st = raw.get("fw", {}), raw.get("fw_status", {})

    # -- status: HA, resources, firmware ----------------------------------
    ha = _results(fw.get("ha"))
    ha = ha[0] if isinstance(ha, list) and ha else ha
    mode = _HA_MODES.get(str(ha.get("mode", "")).lower()) if isinstance(ha, dict) else None
    ha_sync = peer_serial = None
    members = _results(fw.get("ha_checksums"))
    if mode not in (None, "standalone") and st.get("ha_checksums") == "collected":
        members = [m for m in members or [] if isinstance(m, dict)]
        sums = [m.get("checksum") for m in members]
        # A cluster with one visible member has lost its peer.
        ha_sync = "in_sync" if len(members) >= 2 and all(c == sums[0] for c in sums) else "out_of_sync"
        peer_serial = next((m.get("serial_no") for m in members if m.get("serial_no") != local_serial), None)
    resources = _results(fw.get("resources"))
    sessions = _current(resources, "session")
    status = NormalizedFirewallStatus(
        device_external_id="1",
        ha_mode=mode,
        ha_sync=ha_sync,
        ha_peer_serial=peer_serial,
        session_count=int(sessions) if sessions is not None else None,
        cpu_percent=_current(resources, "cpu"),
        memory_percent=_current(resources, "mem"),
        firmware_version=raw.get("status", {}).get("version"),
    )
    reported = any(getattr(status, f) is not None for f in ("ha_mode", "session_count", "cpu_percent", "memory_percent"))
    bundle.firewall_status["status"] = _table([st.get("ha"), st.get("resources")], reported)
    if reported:
        bundle.firewalls.append(status)

    # -- VPN tunnels --------------------------------------------------------
    for row in _results(fw.get("vpn")) or []:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        selectors = [p for p in row.get("proxyid") or [] if isinstance(p, dict)]
        up = sum(1 for p in selectors if p.get("status") == "up")
        bundle.vpn_tunnels.append(NormalizedVpnTunnel(
            device_external_id="1",
            name=str(row["name"]),
            status="up" if up else "down",
            remote_gateway=row.get("rgwy") or None,
            phase2_up=up,
            phase2_total=len(selectors),
            bytes_in=row.get("incoming_bytes"),
            bytes_out=row.get("outgoing_bytes"),
        ))
    bundle.firewall_status["vpn"] = _table([st.get("vpn")], bool(bundle.vpn_tunnels))

    # -- Licences -----------------------------------------------------------
    lic = _results(fw.get("licences"))
    for feature, info in (lic.items() if isinstance(lic, dict) else []):
        if not isinstance(info, dict) or ("expires" not in info and "status" not in info):
            continue
        expires = _int_or_none(info.get("expires"))
        state = str(info.get("status", "")).lower()
        bundle.licences.append(NormalizedLicence(
            device_external_id="1",
            feature=str(feature),
            expires_at=datetime.fromtimestamp(expires, timezone.utc) if expires and expires > 0 else None,
            expired=True if state == "expired" else (False if state in ("licensed", "valid") else None),
        ))
    bundle.firewall_status["licences"] = _table([st.get("licences")], bool(bundle.licences))
