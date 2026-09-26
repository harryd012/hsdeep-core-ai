"""
Fortinet FortiManager collector.

FortiManager is the centralized management platform for multiple FortiGate
devices. This collector discovers managed devices and their status through
the FortiManager JSON-RPC API.

Connection details from `MonitoringSourceConnectionInfo`:
    hostname / port / protocol  -> FortiManager base URL
    username                    -> FortiManager admin username
    password                    -> FortiManager admin password (encrypted at rest)
    api_key                     -> FortiManager API access token (preferred)
    verify_ssl                  -> TLS verification (default False for self-signed certs)

API reference: https://fndn.fortinet.net/index.php?/fortiapi/5-fortimanager/
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
)

logger = logging.getLogger(__name__)

_STATUS_TIMEOUT = 10.0
_TABLE_TIMEOUT = 30.0
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


@CollectorRegistry.register("fortimanager")
class FortiManagerCollector(BaseCollector):
    display_name = "Fortinet FortiManager"
    implemented = True

    # -- config helpers -------------------------------------------------------

    def _require_config(self) -> None:
        missing = [
            key
            for key, value in (
                ("hostname", self.source.hostname),
                ("api_key or password", self.source.api_key or self.source.password),
            )
            if not value
        ]
        if missing:
            raise CollectorConfigError(
                f"FortiManager config missing required field(s): {missing}"
            )

    def _base_url(self) -> str:
        protocol = self.source.protocol or "https"
        port = self.source.port
        hostname = self.source.hostname
        default_port = 443 if protocol == "https" else 80
        if port and port != default_port:
            return f"{protocol}://{hostname}:{port}"
        return f"{protocol}://{hostname}"

    def _auth_headers(self) -> dict[str, str]:
        token = self.source.api_key
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    # -- shared JSON-RPC helper -----------------------------------------------

    async def _jsonrpc(
        self, method: str, params: list[dict[str, Any]], timeout: float = _TABLE_TIMEOUT
    ) -> dict[str, Any]:
        """Send a FortiManager JSON-RPC v1 request with retry logic.
        Credentials are not logged."""
        url = f"{self._base_url()}/jsonrpc"
        payload = {
            "id": 1,
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "session": self.source.password or "",   # session token when using user/pass auth
        }
        # When an API token is configured, session field is unused.
        if self.source.api_key:
            payload.pop("session", None)

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                headers = self._auth_headers()
                headers["Content-Type"] = "application/json"
                async with httpx.AsyncClient(
                    verify=self.source.verify_ssl, timeout=timeout
                ) as client:
                    response = await client.post(url, headers=headers, json=payload)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "FortiManager JSON-RPC failed: source_id=%s method=%s attempt=%s/%s error=%s",
                    self.source.id, method, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise
            else:
                if response.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                response.raise_for_status()
                logger.debug(
                    "FortiManager JSON-RPC: source_id=%s method=%s status=%s",
                    self.source.id, method, response.status_code,
                )
                return response.json()

        if last_exc:
            raise last_exc
        return {}  # pragma: no cover

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        try:
            result = await self._jsonrpc(
                "get",
                [{"url": "/sys/status"}],
                timeout=_STATUS_TIMEOUT,
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(
                success=False, message=f"Could not reach FortiManager: {exc}"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return ConnectionCheck(
                    success=False,
                    message="FortiManager: Unauthorized (invalid credentials).",
                )
            return ConnectionCheck(
                success=False,
                message=f"FortiManager responded with HTTP {exc.response.status_code}.",
            )

        # FortiManager JSON-RPC returns result codes per-param; code 0 = success
        results = result.get("result", [{}])
        if results and results[0].get("status", {}).get("code", -1) == 0:
            return ConnectionCheck(success=True, message="Connected to FortiManager.")
        return ConnectionCheck(
            success=False, message="FortiManager returned non-zero status code."
        )

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        raw: dict[str, Any] = {}

        # System status
        try:
            status_result = await self._jsonrpc(
                "get", [{"url": "/sys/status"}], timeout=_STATUS_TIMEOUT
            )
            raw["status"] = status_result.get("result", [{}])[0].get("data", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FortiManager collect(): /sys/status failed: source_id=%s error=%s",
                self.source.id, exc,
            )
            raw["status"] = {}

        # Managed device list from the default ADOM
        try:
            dev_result = await self._jsonrpc(
                "get",
                [{"url": "/dvmdb/adom/root/device", "option": ["get meta"]}],
                timeout=_TABLE_TIMEOUT,
            )
            raw["devices"] = dev_result.get("result", [{}])[0].get("data", []) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FortiManager collect(): device list failed: source_id=%s error=%s",
                self.source.id, exc,
            )
            raw["devices"] = []

        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        devices: list[NormalizedDevice] = []

        for dev in raw.get("devices", []):
            name = dev.get("name") or dev.get("sn") or "unknown"
            conn_status = dev.get("conn_status", 1)  # 0=up, 1=down in FMG API
            status = "up" if conn_status == 0 else "down"
            device = NormalizedDevice(
                external_id=dev.get("sn") or dev.get("name") or name,
                name=name,
                device_type="firewall",
                vendor="Fortinet",
                model=dev.get("platform_str"),
                os_version=dev.get("os_ver"),
                hostname=dev.get("ip"),
                ip_address=dev.get("ip"),
                status=status,
            )
            devices.append(device)

        return NormalizedBundle(devices=devices)
