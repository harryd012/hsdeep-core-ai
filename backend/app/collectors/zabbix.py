"""
Zabbix Enterprise monitoring collector.

Collector Framework implementation for Zabbix using Zabbix API.
Documented at https://www.zabbix.com/documentation/current/en/manual/api

Connection details from `MonitoringSourceConnectionInfo`:
    hostname/port/protocol -> compose the Zabbix base URL (e.g. http://zabbix.example.com)
    username                -> Zabbix user account
    password                -> Zabbix user password (encrypted at rest)
    api_key                 -> Alternative: Zabbix API token (if using token auth)
    verify_ssl              -> TLS verification (defaults to False for self-signed dev certs)

Single-file collector matching the PRTG, FortiGate, Meraki pattern. `authenticate()`/`collect()`/`normalize()`
satisfy `BaseCollector`'s abstract methods; `save()` is not overridden - it relies on
`BaseCollector`'s default via `BundleSaver`.

Logging discipline: logs via `logging.getLogger(__name__)` with source_id/path/outcome only,
never username/password/api_key (matching SecretCrypto no-log rule).
"""
from __future__ import annotations

import asyncio
import json
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
    NormalizedSensorReading,
    NormalizedMetric,
    NormalizedAlertSignal,
)
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_STATUS_TIMEOUT = 10.0
_TABLE_TIMEOUT = 30.0
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0


@CollectorRegistry.register("zabbix")
class ZabbixCollector(BaseCollector):
    display_name = "Zabbix Enterprise"
    implemented = True

    # -- config / URL helpers -------------------------------------------------

    def _require_config(self) -> None:
        """Validate required configuration."""
        missing = []
        if not self.source.hostname:
            missing.append("hostname")
        if not (self.source.password or self.source.api_key):
            missing.append("password or api_key")
        if missing:
            raise CollectorConfigError(f"Zabbix config missing required field(s): {missing}")

    def _base_url(self) -> str:
        """Construct Zabbix API base URL from hostname/port/protocol."""
        protocol = self.source.protocol or "http"
        hostname = self.source.hostname
        port = self.source.port
        default_port = 443 if protocol == "https" else 80
        if port and port != default_port:
            return f"{protocol}://{hostname}:{port}"
        return f"{protocol}://{hostname}"

    # -- shared HTTP helper -------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any], timeout: float = _TABLE_TIMEOUT) -> dict[str, Any]:
        """POST to Zabbix JSON-RPC API with retries.
        
        Zabbix uses JSON-RPC 2.0 for all API calls. Each call is a POST with:
        {
            "jsonrpc": "2.0",
            "method": "host.get",
            "params": {...},
            "auth": "<auth_token>",
            "id": 1
        }
        
        Never logs credentials.
        """
        url = f"{self._base_url()}/api_jsonrpc.php"
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": 1,
                }
                
                # Add auth token if available (set during authenticate phase)
                if hasattr(self, "_auth_token") and self._auth_token:
                    payload["auth"] = self._auth_token
                
                headers = {"Content-Type": "application/json"}
                async with httpx.AsyncClient(verify=self.source.verify_ssl, timeout=timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Zabbix request failed: source_id=%s method=%s attempt=%s/%s error_type=%s",
                    self.source.id, method, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise
            else:
                if response.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "Zabbix request returned server error: source_id=%s method=%s attempt=%s/%s status=%s",
                        self.source.id, method, attempt, _MAX_ATTEMPTS, response.status_code,
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                logger.debug(
                    "Zabbix request completed: source_id=%s method=%s attempt=%s/%s status=%s",
                    self.source.id, method, attempt, _MAX_ATTEMPTS, response.status_code,
                )
                try:
                    return response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.error(
                        "Zabbix response invalid JSON: source_id=%s method=%s",
                        self.source.id, method,
                    )
                    raise

        if last_exc is not None:
            raise last_exc
        assert response is not None  # pragma: no cover
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {"result": []}

    # -- BaseCollector interface -------------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Authenticate with Zabbix API and store auth token.
        
        Zabbix requires either:
        1. Username/password login: user.login method
        2. API token (if available): used directly in subsequent calls
        """
        self._require_config()
        
        try:
            # Try API token auth first if available
            if self.source.api_key:
                # API token is used directly in future requests
                self._auth_token = self.source.api_key
                # Verify it works with a simple host.get call
                result = await self._request("host.get", {"limit": 1, "output": ["hostid"]}, timeout=_STATUS_TIMEOUT)
                if "result" in result or "error" not in result:
                    return ConnectionCheck(success=True, message="Connected to Zabbix (API token auth).")
                else:
                    return ConnectionCheck(success=False, message=f"Zabbix API token invalid: {result.get('error', 'unknown')}")
            
            # Fall back to username/password login
            result = await self._request("user.login", {
                "username": self.source.username,
                "password": self.source.password,
                "userData": True
            }, timeout=_STATUS_TIMEOUT)
            
            if "result" in result:
                self._auth_token = result["result"]
                return ConnectionCheck(success=True, message="Connected to Zabbix (user login).")
            else:
                error_msg = result.get("error", {}).get("data", "Unknown error")
                return ConnectionCheck(success=False, message=f"Zabbix authentication failed: {error_msg}")
        
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(success=False, message=f"Could not reach Zabbix: {exc}")
        except Exception as exc:
            return ConnectionCheck(success=False, message=f"Zabbix authentication error: {exc}")

    async def collect(self) -> dict[str, Any]:
        """Collect hosts, interfaces, items, triggers, and problems from Zabbix.
        
        Data collection sequence:
        1. Host groups (for organization)
        2. Hosts (with status)
        3. Interfaces (per host)
        4. Items (metrics, per host)
        5. Triggers (alerting rules, per host)
        6. Problems (active problems/incidents)
        7. History (recent values for key items)
        """
        self._require_config()
        result: dict[str, Any] = {}

        # Host groups (organizational structure)
        try:
            groups_result = await self._request("hostgroup.get", {
                "output": ["groupid", "name"],
                "sortfield": "name"
            }, timeout=_TABLE_TIMEOUT)
            result["host_groups"] = groups_result.get("result", [])
            logger.debug("Zabbix collect(): got %d host groups", len(result.get("host_groups", [])))
        except (httpx.TransportError, httpx.TimeoutException, Exception) as exc:
            logger.warning("Zabbix collect(): host groups failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["host_groups"] = []

        # Hosts (main inventory)
        try:
            hosts_result = await self._request("host.get", {
                "output": ["hostid", "host", "name", "status", "groups"],
                "selectInterfaces": ["interfaceid", "type", "ip", "port", "main"],
                "selectGroups": ["groupid", "name"],
                "sortfield": "name"
            }, timeout=_TABLE_TIMEOUT)
            result["hosts"] = hosts_result.get("result", [])
            logger.debug("Zabbix collect(): got %d hosts", len(result.get("hosts", [])))
        except (httpx.TransportError, httpx.TimeoutException, Exception) as exc:
            logger.warning("Zabbix collect(): hosts failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["hosts"] = []

        # Items (metrics per host) - best effort
        result["items"] = []
        for host in result.get("hosts", []):
            try:
                items_result = await self._request("item.get", {
                    "hostids": host["hostid"],
                    "output": ["itemid", "key_", "name", "type", "value_type", "lastvalue", "units"],
                    "filter": {"state": 0},  # Only enabled items
                    "limit": 100  # Prevent excessive data
                }, timeout=_TABLE_TIMEOUT)
                items = items_result.get("result", [])
                for item in items:
                    item["hostid"] = host["hostid"]  # Link back to host
                result["items"].extend(items)
            except (httpx.TransportError, httpx.TimeoutException, Exception) as exc:
                logger.warning("Zabbix collect(): items failed for host %s: source_id=%s error_type=%s",
                              host.get("hostid"), self.source.id, type(exc).__name__)
                continue

        # Triggers (alerting rules per host) - best effort
        result["triggers"] = []
        for host in result.get("hosts", []):
            try:
                triggers_result = await self._request("trigger.get", {
                    "hostids": host["hostid"],
                    "output": ["triggerid", "description", "priority", "state", "value"],
                    "sortfield": "priority",
                    "limit": 100
                }, timeout=_TABLE_TIMEOUT)
                triggers = triggers_result.get("result", [])
                for trigger in triggers:
                    trigger["hostid"] = host["hostid"]  # Link back to host
                result["triggers"].extend(triggers)
            except (httpx.TransportError, httpx.TimeoutException, Exception) as exc:
                logger.warning("Zabbix collect(): triggers failed for host %s: source_id=%s error_type=%s",
                              host.get("hostid"), self.source.id, type(exc).__name__)
                continue

        # Problems (active incidents/events)
        try:
            problems_result = await self._request("problem.get", {
                "output": ["eventid", "name", "severity", "status"],
                "search": {"name": ""},
                "searchWildcardsEnabled": True,
                "limit": 500
            }, timeout=_TABLE_TIMEOUT)
            result["problems"] = problems_result.get("result", [])
            logger.debug("Zabbix collect(): got %d problems", len(result.get("problems", [])))
        except (httpx.TransportError, httpx.TimeoutException, Exception) as exc:
            logger.warning("Zabbix collect(): problems failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["problems"] = []

        return result

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        """Convert raw Zabbix API response to NormalizedBundle.
        
        Maps Zabbix data model to platform model:
        - Zabbix Hosts → NormalizedDevice (with status, groups)
        - Zabbix Interfaces → NormalizedInterface (with IP, port)
        - Zabbix Items → NormalizedSensorReading (metrics, lastvalue)
        - Zabbix Problems → NormalizedAlertSignal (active incidents)
        - Zabbix Triggers → Alert metadata
        """
        devices: list[NormalizedDevice] = []
        interfaces: list[NormalizedInterface] = []
        sensors: list[NormalizedSensorReading] = []
        alerts: list[NormalizedAlertSignal] = []

        # Create device lookup for quick access
        devices_by_hostid: dict[str, NormalizedDevice] = {}

        # Normalize hosts to devices
        for host_row in raw.get("hosts", []):
            if not host_row.get("hostid"):
                continue

            hostid = str(host_row["hostid"])
            # Zabbix status: 0=monitored, 1=unmonitored
            host_status = "up" if host_row.get("status") == "0" else "down"

            # Extract groups for site_hint
            groups = host_row.get("groups", [])
            site_hint = " / ".join([g.get("name", "") for g in groups]) if groups else None

            device = NormalizedDevice(
                external_id=hostid,
                name=host_row.get("name") or host_row.get("host", f"zabbix-{hostid}"),
                device_type="other",  # Zabbix doesn't provide device type, assume generic
                vendor="Zabbix",
                model=None,
                os_version=None,
                hostname=host_row.get("host"),
                ip_address=None,  # Will be extracted from interfaces
                mac_address=None,
                status=host_status,
                site_hint=site_hint,
            )
            devices.append(device)
            devices_by_hostid[hostid] = device

        # Normalize interfaces
        for host_row in raw.get("hosts", []):
            hostid = str(host_row["hostid"])
            for iface_row in host_row.get("interfaces", []):
                if not iface_row.get("interfaceid"):
                    continue

                interface = NormalizedInterface(
                    device_external_id=hostid,
                    external_id=str(iface_row["interfaceid"]),
                    name=_get_interface_name(iface_row),
                    admin_status="up",
                    oper_status="up",
                    speed_mbps=None,  # Zabbix doesn't provide interface speed
                    mac_address=None,
                    in_octets=None,
                    out_octets=None,
                    in_errors=None,
                    out_errors=None,
                )
                interfaces.append(interface)
                
                # Set IP address on device if primary interface
                if iface_row.get("main") == "1" and iface_row.get("ip"):
                    if hostid in devices_by_hostid:
                        devices_by_hostid[hostid].ip_address = iface_row["ip"]

        # Normalize items to sensors
        for item_row in raw.get("items", []):
            if not item_row.get("itemid"):
                continue

            hostid = str(item_row.get("hostid", ""))
            
            # Parse Zabbix value types
            value_type = item_row.get("value_type", "")
            # 0=numeric, 1=string, 2=log, 3=counter, 4=text
            if value_type not in ["0", "3"]:  # Skip non-numeric types for now
                continue

            lastvalue = None
            try:
                lastvalue = float(item_row.get("lastvalue", 0)) if item_row.get("lastvalue") else None
            except (ValueError, TypeError):
                pass

            sensor = NormalizedSensorReading(
                external_id=str(item_row["itemid"]),
                name=item_row.get("name", item_row.get("key_", "unknown")),
                metric_type=_map_item_type(item_row.get("key_", "")),
                value=lastvalue,
                unit=item_row.get("units"),
                status="up",  # Item status would require additional lookup
                device_external_id=hostid,
            )
            sensors.append(sensor)

        # Normalize problems to alerts
        trigger_descriptions: dict[str, str] = {}
        for trigger_row in raw.get("triggers", []):
            trigger_descriptions[str(trigger_row.get("triggerid", ""))] = trigger_row.get("description", "")

        for problem_row in raw.get("problems", []):
            if not problem_row.get("eventid"):
                continue

            # Zabbix severity: 0=Not classified, 1=Information, 2=Warning, 3=Average, 4=High, 5=Disaster
            severity_map = {
                "0": "info",
                "1": "info",
                "2": "warning",
                "3": "warning",
                "4": "warning",
                "5": "critical",
            }
            severity = severity_map.get(problem_row.get("severity", "0"), "info")

            alert = NormalizedAlertSignal(
                external_id=str(problem_row["eventid"]),
                title=problem_row.get("name", "Zabbix Problem"),
                severity=severity,
                triggered_at=datetime.now(timezone.utc),  # Zabbix problem doesn't include timestamp in basic call
                description=problem_row.get("name"),
                device_external_id=None,  # Problems don't link to specific hosts in basic call
            )
            alerts.append(alert)

        return NormalizedBundle(
            devices=devices,
            interfaces=interfaces,
            sensors=sensors,
            alerts=alerts,
        )


def _get_interface_name(iface_row: dict[str, Any]) -> str:
    """Get human-readable name for Zabbix interface."""
    iface_type = iface_row.get("type", "0")
    type_names = {
        "1": "Agent",
        "2": "SNMP",
        "3": "IPMI",
        "4": "JMX",
    }
    type_name = type_names.get(iface_type, f"Type{iface_type}")
    ip = iface_row.get("ip", "unknown")
    port = iface_row.get("port", "")
    if port and port not in ["161", "10050"]:  # Hide default ports
        return f"{type_name} {ip}:{port}"
    return f"{type_name} {ip}"


def _map_item_type(key: str) -> str:
    """Map Zabbix item key to normalized metric type."""
    key_lower = key.lower()
    
    # CPU metrics
    if "cpu" in key_lower:
        if "load" in key_lower:
            return "cpu_load"
        return "cpu_utilization"
    
    # Memory metrics
    if "memory" in key_lower or "mem" in key_lower:
        if "available" in key_lower:
            return "memory_available"
        if "free" in key_lower:
            return "memory_free"
        if "used" in key_lower:
            return "memory_used"
        return "memory_utilization"
    
    # Disk metrics
    if "disk" in key_lower or "fs" in key_lower:
        if "used" in key_lower:
            return "disk_used"
        if "free" in key_lower:
            return "disk_free"
        if "util" in key_lower or "usage" in key_lower:
            return "disk_utilization"
        return "disk_io"
    
    # Network metrics
    if "net" in key_lower or "traffic" in key_lower:
        if "in" in key_lower:
            return "network_in"
        if "out" in key_lower or "tx" in key_lower:
            return "network_out"
        return "network_io"
    
    # System/temperature
    if "temp" in key_lower:
        return "environmental_temperature"
    if "humidity" in key_lower:
        return "environmental_humidity"
    
    # Default
    return "application_metric"
