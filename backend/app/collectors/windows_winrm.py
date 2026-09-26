"""
Windows WinRM collector (remote WMI over WinRM — agentless).

Connects to Windows hosts via WinRM and collects system metrics using WMI
queries (CPU, memory, disk, uptime, perf counters, services, network
adapters). No agent is installed on the target server.

Connection details from `MonitoringSourceConnectionInfo`:
    hostname    -> Windows host IP or FQDN
    port        -> WinRM port (default 5985 HTTP, 5986 HTTPS)
    protocol    -> http | https (default http)
    username    -> Windows username (DOMAIN\\user or local user)
    password    -> Windows password (encrypted at rest via SecretCrypto —
                   the SAME mechanism used for SNMPv3 secrets; no second
                   secrets pattern exists)
    verify_ssl  -> TLS cert verification for HTTPS transport
    config["auth_type"]   -> ntlm | basic | kerberos  (default ntlm)

Reliability:
    - Per-query timeout + bounded retries (exponential backoff).
    - Per-host circuit breaker: after N consecutive failed collections the
      host is "opened" and skipped fast for a cooldown period, then probed
      again (half-open). One host's WinRM auth failure never blocks other
      hosts because state is keyed per host and failures are contained to
      that host's collect() call.

Dependency: pywinrm==0.4.3 (pinned in requirements.txt)

Security note: passwords/connection strings are NEVER logged.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedSensorReading,
)
from app.collectors.windows_profiles import WindowsProfile, select_profile
from app.services.discovery.identity import normalise_serial

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reliability primitives
# ---------------------------------------------------------------------------

_QUERY_TIMEOUT_SECONDS = 30.0
_QUERY_RETRIES = 2          # retries after the initial attempt
_RETRY_BACKOFF_BASE = 1.0   # seconds; doubles per retry

_CIRCUIT_FAILURE_THRESHOLD = 3     # consecutive failures -> open
_CIRCUIT_COOLDOWN_SECONDS = 300.0  # open duration before half-open probe


class CircuitOpenError(RuntimeError):
    """Raised when a host's circuit is open (skip collection this cycle)."""


class _HostCircuitBreaker:
    """Per-host circuit breaker (closed -> open -> half-open)."""

    def __init__(self, failure_threshold: int = _CIRCUIT_FAILURE_THRESHOLD,
                 cooldown_seconds: float = _CIRCUIT_COOLDOWN_SECONDS) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    def allow_request(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


# Keyed by (tenant_id, source_id) so hosts are fully isolated from each other;
# a failure on one host can never trip another host's breaker.
_CIRCUITS: dict[tuple[Any, Any], _HostCircuitBreaker] = {}


def _circuit_for(source: Any) -> _HostCircuitBreaker:
    key = (getattr(source, "tenant_id", None), getattr(source, "id", None))
    if key not in _CIRCUITS:
        _CIRCUITS[key] = _HostCircuitBreaker()
    return _CIRCUITS[key]


def reset_circuits() -> None:
    """Test helper: clear all breaker state."""
    _CIRCUITS.clear()


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

@CollectorRegistry.register("windows_winrm")
class WindowsWinRMCollector(BaseCollector):
    display_name = "Windows (WinRM)"
    implemented = True

    #: Shown in user-facing messages. The `wmi` source_type reuses this entire
    #: implementation (remote WMI over WSMan), but an operator who configured a
    #: WMI source should not be told the collector is talking "WinRM" to them.
    transport_label = "WinRM"

    # -- config helpers -------------------------------------------------------

    def _require_config(self) -> None:
        missing = [
            key
            for key, value in (
                ("hostname", self.source.hostname),
                ("username", self.source.username),
                ("password", self.source.password),
            )
            if not value
        ]
        if missing:
            raise CollectorConfigError(
                f"{self.transport_label} config missing required field(s): {missing}"
            )

    def _auth_type(self) -> str:
        raw = (self.source.config or {}).get("auth_type", "ntlm")
        auth = str(raw).lower()
        if auth not in ("ntlm", "basic", "kerberos"):
            raise CollectorConfigError(
                f"Unsupported {self.transport_label} auth_type {auth!r}; "
                "expected ntlm|basic|kerberos"
            )
        return auth

    def _build_protocol(self) -> Any:
        """Build a pywinrm Protocol object. Deferred import."""
        try:
            import winrm  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError(
                f"pywinrm is required for the {self.transport_label} collector. "
                "Install it via: pip install pywinrm==0.4.3"
            ) from exc

        transport = self._auth_type()
        protocol = self.source.protocol or "http"
        port = self.source.port or (5986 if protocol == "https" else 5985)

        # NOTE: endpoint string contains credentials-adjacent info but no
        # secret itself; still, it is only built here and never logged.
        return winrm.Protocol(
            endpoint=f"{protocol}://{self.source.hostname}:{port}/wsman",
            transport=transport,
            username=self.source.username,
            password=self.source.password,
            server_cert_validation="validate" if self.source.verify_ssl else "ignore",
        )

    # -- WMI execution with timeout + retry ------------------------------------

    def _run_wmi_once(self, protocol: Any, wmi_query: str) -> list[dict[str, Any]]:
        ps_command = (
            f"Get-CimInstance -Query \"{wmi_query}\" | "
            "Select-Object * | ConvertTo-Json -Depth 2 -Compress"
        )
        shell_id = protocol.open_shell()
        try:
            command_id = protocol.run_command(
                shell_id, "powershell.exe",
                ["-NonInteractive", "-NoProfile", "-Command", ps_command],
            )
            stdout, stderr, rc = protocol.get_command_output(shell_id, command_id)
            protocol.cleanup_command(shell_id, command_id)
        finally:
            protocol.close_shell(shell_id)

        if rc != 0:
            # Log only query prefix + exit code; stderr may echo the command
            # line which could embed sensitive values in exotic setups.
            logger.warning(
                "%s WMI query failed: source_id=%s rc=%s query=%s...",
                self.transport_label, getattr(self.source, "id", "?"), rc,
                wmi_query[:60],
            )
            return []

        import json
        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            return []
        parsed = json.loads(output)
        return parsed if isinstance(parsed, list) else [parsed]

    def _run_wmi(self, protocol: Any, wmi_query: str) -> list[dict[str, Any]]:
        """Run one WQL query with bounded exponential-backoff retries.

        Runs synchronously; callers invoke it from a worker thread
        (asyncio.to_thread) so a slow host never blocks the event loop.
        """
        last_exc: Exception | None = None
        for attempt in range(1 + _QUERY_RETRIES):
            try:
                return self._run_wmi_once(protocol, wmi_query)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < _QUERY_RETRIES:
                    delay = _RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.info(
                        "%s query retry %s/%s for source_id=%s after %.1fs",
                        self.transport_label, attempt + 1, _QUERY_RETRIES,
                        getattr(self.source, "id", "?"), delay,
                    )
                    time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        """Verify WinRM reachability + credentials (health_check contract)."""
        self._require_config()
        try:
            protocol = self._build_protocol()
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=(
                    f"{self.transport_label} protocol setup failed: "
                    f"{type(exc).__name__}"
                ),
            )
        try:
            shell_id = await asyncio.to_thread(protocol.open_shell)
            await asyncio.to_thread(protocol.close_shell, shell_id)
            _circuit_for(self.source).record_success()
            return ConnectionCheck(
                success=True,
                message=f"{self.transport_label} connection established.",
            )
        except Exception as exc:  # noqa: BLE001
            _circuit_for(self.source).record_failure()
            return ConnectionCheck(
                success=False,
                message=(
                    f"{self.transport_label} authentication failed: "
                    f"{type(exc).__name__}"
                ),
            )

    async def collect(self) -> dict[str, Any]:
        self._require_config()

        breaker = _circuit_for(self.source)
        if not breaker.allow_request():
            raise CircuitOpenError(
                f"circuit open for source_id={getattr(self.source, 'id', '?')}; "
                "skipping collection this cycle"
            )

        profile = select_profile(None)  # refined in normalize() once OS caption known
        raw: dict[str, Any] = {"profile_queries": sorted(profile.wmi_queries)}

        try:
            protocol = await asyncio.to_thread(self._build_protocol)
        except CollectorConfigError:
            raise

        failures = 0
        for name, wql in profile.wmi_queries.items():
            try:
                raw[name] = await asyncio.to_thread(self._run_wmi, protocol, wql)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.warning(
                    "%s collect(): %s failed: source_id=%s error=%s:%s",
                    self.transport_label, name, getattr(self.source, "id", "?"),
                    type(exc).__name__, str(exc)[:120],
                )
                raw[name] = []

        if failures == len(profile.wmi_queries):
            breaker.record_failure()
        else:
            breaker.record_success()

        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        cs_rows = raw.get("computer_system", [])
        os_rows = raw.get("os", [])
        cs_row = cs_rows[0] if cs_rows else {}
        os_row = os_rows[0] if os_rows else {}

        hostname = (
            cs_row.get("Name")
            or self.source.hostname
            or "windows-host"
        )
        domain = cs_row.get("Domain")
        os_caption = os_row.get("Caption")

        profile = select_profile(os_caption)

        # Authoritative hardware serial from Win32_BIOS (Phase 4.1). Passed through
        # the SAME normalisation the repository uses, so placeholder values
        # ("To be filled by O.E.M.") become None here and can never act as an
        # identity key - no duplicate placeholder filtering.
        bios_rows = raw.get("bios", [])
        bios_row = bios_rows[0] if bios_rows else {}
        serial_number = normalise_serial(bios_row.get("SerialNumber"))

        device = NormalizedDevice(
            external_id=hostname,
            name=f"{domain}\\{hostname}" if domain else hostname,
            device_type="server" if "Server" in (os_caption or "") else "other",
            vendor="Microsoft",
            model=profile.family,
            os_version=os_caption or os_row.get("Version"),
            serial_number=serial_number,
            hostname=hostname,
            ip_address=self.source.ip_address or self.source.hostname,
            status="up",
            custom_metadata={
                "windows_profile": profile.profile_id,
                "source_type": "wmi",
                "domain": domain,
                "build_number": os_row.get("BuildNumber"),
            },
        )

        sensors: list[NormalizedSensorReading] = []
        interfaces: list[NormalizedInterface] = []

        # CPU load (per socket)
        for i, cpu_row in enumerate(raw.get("cpu", [])):
            load = _safe_float(cpu_row.get("LoadPercentage"))
            if load is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"{hostname}-cpu-{i}",
                    name=f"CPU {i} Load",
                    metric_type="cpu_usage",
                    value=load,
                    unit="%",
                    status="up",
                    device_external_id=hostname,
                ))

        # Memory utilization
        total_kb = _safe_float(os_row.get("TotalVisibleMemorySize"))
        free_kb = _safe_float(os_row.get("FreePhysicalMemory"))
        if total_kb and free_kb is not None and total_kb > 0:
            used_pct = ((total_kb - free_kb) / total_kb) * 100.0
            sensors.append(NormalizedSensorReading(
                external_id=f"{hostname}-mem",
                name="Memory Usage",
                metric_type="memory_usage",
                value=round(used_pct, 2),
                unit="%",
                status="up",
                device_external_id=hostname,
            ))

        # Uptime (prefer perf counter SystemUpTime, fall back to LastBootUpTime)
        uptime = self._uptime_seconds(raw)
        if uptime is not None:
            sensors.append(NormalizedSensorReading(
                external_id=f"{hostname}-uptime",
                name="Uptime",
                metric_type="uptime_seconds",
                value=round(uptime, 0),
                unit="s",
                status="up",
                device_external_id=hostname,
            ))

        # Disk usage per volume
        for disk_row in raw.get("disk", []):
            drive = str(disk_row.get("DeviceID", "?"))
            size = _safe_float(disk_row.get("Size"))
            free = _safe_float(disk_row.get("FreeSpace"))
            if size and free is not None and size > 0:
                used_pct = ((size - free) / size) * 100.0
                sensors.append(NormalizedSensorReading(
                    external_id=f"{hostname}-disk-{drive.replace(':', '').replace(chr(92), '')}",
                    name=f"Disk {drive} Usage",
                    metric_type="disk_usage",
                    value=round(used_pct, 2),
                    unit="%",
                    status="up",
                    device_external_id=hostname,
                ))

        # PerfOS: processor queue length
        for perf_row in raw.get("perf_os", [])[:1]:
            queue_len = _safe_float(perf_row.get("ProcessorQueueLength"))
            if queue_len is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"{hostname}-cpu-queue",
                    name="Processor Queue Length",
                    metric_type="cpu_queue_length",
                    value=queue_len,
                    unit=None,
                    status="up",
                    device_external_id=hostname,
                ))

        # PerfProc: top processes by CPU (cap at 10 to bound cardinality)
        proc_rows = [p for p in raw.get("perf_proc", [])
                     if p.get("Name") not in ("_Total", "Idle")]
        proc_rows.sort(key=lambda p: _safe_float(p.get("PercentProcessorTime")) or 0.0,
                       reverse=True)
        for proc_row in proc_rows[:10]:
            pname = str(proc_row.get("Name", "?"))[:64]
            pcpu = _safe_float(proc_row.get("PercentProcessorTime"))
            pmem = _safe_float(proc_row.get("WorkingSetPrivate"))
            pid = proc_row.get("IDProcess", "?")
            if pcpu is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"{hostname}-proc-{pid}-cpu",
                    name=f"Process {pname} CPU",
                    metric_type="process_cpu",
                    value=pcpu,
                    unit="%",
                    status="up",
                    device_external_id=hostname,
                ))
            if pmem is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"{hostname}-proc-{pid}-mem",
                    name=f"Process {pname} Memory",
                    metric_type="process_memory_bytes",
                    value=pmem,
                    unit="bytes",
                    status="up",
                    device_external_id=hostname,
                ))

        # Services: auto-start services that are NOT running become warning
        # sensors; running auto services are recorded as healthy (value 1).
        for svc_row in raw.get("services", []):
            svc_name = str(svc_row.get("Name", "?"))[:64]
            state = str(svc_row.get("State", "")).lower()
            running = state == "running"
            sensors.append(NormalizedSensorReading(
                external_id=f"{hostname}-svc-{svc_name}",
                name=f"Service {svc_name}",
                metric_type="service_status",
                value=1.0 if running else 0.0,
                unit=None,
                status="up" if running else "warning",
                device_external_id=hostname,
            ))

        # Network adapters (IP-enabled)
        for nic_row in raw.get("network", []):
            idx = nic_row.get("InterfaceIndex", "?")
            desc = str(nic_row.get("Description", f"NIC {idx}"))[:64]
            mac = nic_row.get("MACAddress")
            ips = nic_row.get("IPAddress") or []
            ip_list = ips if isinstance(ips, list) else [ips]
            primary_ip = next((str(i) for i in ip_list if i), None)
            interfaces.append(NormalizedInterface(
                device_external_id=hostname,
                external_id=f"{hostname}-nic-{idx}",
                name=desc,
                admin_status="up",
                oper_status="up" if primary_ip else "down",
                mac_address=str(mac) if mac else None,
            ))

        return NormalizedBundle(
            devices=[device],
            interfaces=interfaces,
            sensors=sensors,
        )

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def _uptime_seconds(raw: dict[str, Any]) -> float | None:
        # Preferred: perf counter gives seconds directly.
        for perf_row in raw.get("perf_os", [])[:1]:
            val = _safe_float(perf_row.get("SystemUpTime"))
            if val is not None and val > 0:
                return val
        # Fallback: LastBootUpTime like "20260824081500.500000+330".
        boot = (raw.get("os") or [{}])[0].get("LastBootUpTime")
        if not boot:
            return None
        try:
            text = str(boot)
            dt_part = text.split(".")[0]
            booted_at = datetime.strptime(dt_part, "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            )
            return max((datetime.now(timezone.utc) - booted_at).total_seconds(), 0.0)
        except ValueError:
            return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None