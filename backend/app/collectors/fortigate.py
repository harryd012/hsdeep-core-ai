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
    NormalizedRoute,
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

        return NormalizedBundle(
            devices=devices,
            interfaces=interfaces,
            addresses=addresses,
            routes=routes,
            l3_status=l3_status,
        )


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
