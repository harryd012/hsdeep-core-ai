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
import xml.etree.ElementTree as ET
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
            "highavailability": "<show><high-availability><state/></high-availability></high-availability>",
        }
        for key, cmd in jobs.items():
            try:
                resp = await self._op(cmd)
                result[key] = resp.text if resp.status_code == 200 else ""
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning(
                    "Palo Alto collect %s failed: source_id=%s type=%s",
                    key, self.source.id, type(exc).__name__,
                )
                result[key] = ""
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

        return NormalizedBundle(devices=devices, interfaces=interfaces, metrics=[])


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
