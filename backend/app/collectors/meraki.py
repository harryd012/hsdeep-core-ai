"""
Cisco Meraki Dashboard collector.

Collector Framework implementation for Cisco Meraki using Dashboard API v1.
Documented at https://developer.cisco.com/meraki/api/

Connection details from `MonitoringSourceConnectionInfo`:
    hostname/port/protocol -> not used (Meraki is cloud-only, uses api.meraki.com)
    api_key                 -> Meraki API key (required)
    verify_ssl              -> TLS verification (defaults to True for cloud services)

Single-file collector matching the PRTG/FortiGate pattern. `authenticate()`/`collect()`/`normalize()`
satisfy `BaseCollector`'s abstract methods; `save()` is not overridden - it relies on
`BaseCollector`'s default via `BundleSaver`.

Logging discipline: logs via `logging.getLogger(__name__)` with source_id/path/outcome only,
never api_key (matching SecretCrypto no-log rule).
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
    NormalizedSensorReading,
)

logger = logging.getLogger(__name__)

_MERAKI_API_BASE = "https://api.meraki.com/api/v1"
_STATUS_TIMEOUT = 10.0
_TABLE_TIMEOUT = 20.0
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


@CollectorRegistry.register("meraki")
class MerakiCollector(BaseCollector):
    display_name = "Cisco Meraki Dashboard"
    implemented = True
    category = "network"

    # -- config / validation -------------------------------------------------

    def _require_config(self) -> None:
        """Validate required configuration. Meraki uses API key in headers."""
        if not self.source.password:
            raise CollectorConfigError("Meraki requires api_key in password field")

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers for Meraki API."""
        return {
            "X-Cisco-Meraki-API-Key": self.source.password,
            "Content-Type": "application/json",
        }

    # -- shared HTTP helper -------------------------------------------------

    async def _request(self, path: str, params: dict[str, Any] | None = None, timeout: float = _TABLE_TIMEOUT) -> httpx.Response:
        """GET `path` from Meraki cloud API with retries.
        Never logs the API key."""
        url = f"{_MERAKI_API_BASE}{path}"
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                headers = self._auth_headers()
                async with httpx.AsyncClient(verify=True, timeout=timeout) as client:
                    response = await client.get(url, headers=headers, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Meraki request failed: source_id=%s path=%s attempt=%s/%s error_type=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise
            else:
                if response.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "Meraki request returned server error: source_id=%s path=%s attempt=%s/%s status=%s",
                        self.source.id, path, attempt, _MAX_ATTEMPTS, response.status_code,
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                logger.debug(
                    "Meraki request completed: source_id=%s path=%s attempt=%s/%s status=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, response.status_code,
                )
                return response

        if last_exc is not None:
            raise last_exc
        assert response is not None  # pragma: no cover
        return response

    # -- BaseCollector interface -------------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Test authentication against Meraki /organizations endpoint."""
        self._require_config()
        try:
            resp = await self._request("/organizations", timeout=_STATUS_TIMEOUT)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(success=False, message=f"Could not reach Meraki: {exc}")

        if resp.status_code == 200:
            return ConnectionCheck(success=True, message="Connected to Meraki Dashboard.")
        if resp.status_code == 401:
            return ConnectionCheck(success=False, message="Meraki: Unauthorized (invalid API key).")
        return ConnectionCheck(success=False, message=f"Meraki responded with HTTP {resp.status_code}.")

    async def collect(self) -> dict[str, Any]:
        """Collect organizations, networks, and devices from Meraki Dashboard."""
        self._require_config()
        result: dict[str, Any] = {}

        # Organizations (top-level)
        try:
            orgs_resp = await self._request("/organizations", timeout=_TABLE_TIMEOUT)
            orgs_resp.raise_for_status()
            result["organizations"] = orgs_resp.json()
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning("Meraki collect(): organizations failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["organizations"] = []

        # Networks (per organization) - best-effort
        result["networks"] = []
        for org in result.get("organizations", []):
            try:
                nets_resp = await self._request(f"/organizations/{org['id']}/networks", timeout=_TABLE_TIMEOUT)
                nets_resp.raise_for_status()
                result["networks"].extend(nets_resp.json())
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                logger.warning("Meraki collect(): networks failed for org %s: source_id=%s error_type=%s",
                              org['id'], self.source.id, type(exc).__name__)
                continue

        # Devices (per network) - best-effort
        result["devices"] = []
        result["device_statuses"] = {}
        for network in result.get("networks", []):
            try:
                devs_resp = await self._request(f"/networks/{network['id']}/devices", timeout=_TABLE_TIMEOUT)
                devs_resp.raise_for_status()
                devices = devs_resp.json()
                result["devices"].extend(devices)

                # Collect device statuses
                for device in devices:
                    try:
                        status_resp = await self._request(f"/networks/{network['id']}/devices/{device['serial']}/status",
                                                        timeout=_STATUS_TIMEOUT)
                        if status_resp.status_code == 200:
                            result["device_statuses"][device["serial"]] = status_resp.json()
                    except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError):
                        continue  # Best-effort per device

            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                logger.warning("Meraki collect(): devices failed for network %s: source_id=%s error_type=%s",
                              network['id'], self.source.id, type(exc).__name__)
                continue

        return result

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Convert raw Meraki API response to NormalizedBundle.

        Meraki devices are cloud resources. One device per Meraki device in the dashboard.
        Each device gets a normalized Device entry with its status.
        """
        devices: list[NormalizedDevice] = []
        sensors: list[NormalizedSensorReading] = []

        devices_by_serial: dict[str, dict[str, Any]] = {d["serial"]: d for d in raw.get("devices", [])}
        device_statuses = raw.get("device_statuses", {})

        for device_row in raw.get("devices", []):
            if not device_row.get("serial"):
                continue

            serial = device_row["serial"]
            status_data = device_statuses.get(serial, {})

            # Derive device status from the per-device /status payload
            # collected in collect(). The /networks/{id}/devices listing does
            # NOT carry a status field, so device_row.get("status") is always
            # None - use status_data (falling back to device_row only if a
            # status somehow appears there).
            raw_status = status_data.get("status") or device_row.get("status")
            device_status = "up" if raw_status == "online" else "down"

            device = NormalizedDevice(
                external_id=serial,
                name=device_row.get("name") or f"meraki-{device_row.get('model', 'device')}",
                device_type="cloud_resource",
                vendor="Cisco",
                model=device_row.get("model"),
                hostname=None,  # Meraki devices don't have hostnames, only serial numbers
                mac_address=device_row.get("mac"),
                status=device_status,
            )
            devices.append(device)

            # Extract sensor readings if available (some Meraki devices have environmental sensors)
            if device_row.get("temperature") is not None:
                sensor = NormalizedSensorReading(
                    external_id=f"{serial}-temperature",
                    name="Temperature",
                    metric_type="environmental_temperature",
                    value=device_row.get("temperature"),
                    unit="C",
                    status=device_status,
                    device_external_id=serial,
                )
                sensors.append(sensor)

            if device_row.get("humidity") is not None:
                sensor = NormalizedSensorReading(
                    external_id=f"{serial}-humidity",
                    name="Humidity",
                    metric_type="environmental_humidity",
                    value=device_row.get("humidity"),
                    unit="%",
                    status=device_status,
                    device_external_id=serial,
                )
                sensors.append(sensor)

        return NormalizedBundle(devices=devices, sensors=sensors)
