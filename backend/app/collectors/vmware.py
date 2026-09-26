"""
VMware vSphere / vCenter collector.

Connects to vCenter Server via pyVmomi and discovers VMs, ESXi hosts, and
their power / connection state.

Connection details from `MonitoringSourceConnectionInfo`:
    hostname    -> vCenter hostname or IP
    port        -> vCenter port (default 443)
    username    -> vCenter admin username
    password    -> vCenter admin password (encrypted at rest)
    verify_ssl  -> TLS verification (default False for self-signed vCenter certs)

Dependency: pyvmomi==8.0.3.0.1 (pinned in requirements.txt)
"""
from __future__ import annotations

import logging
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


@CollectorRegistry.register("vmware")
class VMwareCollector(BaseCollector):
    display_name = "VMware vSphere / vCenter"
    implemented = True

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
                f"VMware config missing required field(s): {missing}"
            )

    def _connect(self) -> Any:
        """Create a pyVmomi SmartConnect session. Deferred import so the
        module loads cleanly when pyvmomi is absent."""
        try:
            from pyVim.connect import SmartConnect  # type: ignore[import]
            import ssl
        except ImportError as exc:
            raise CollectorConfigError(
                "pyvmomi is required for the VMware collector. "
                "Install it via: pip install pyvmomi==8.0.3.0.1"
            ) from exc

        ssl_context = None
        if not self.source.verify_ssl:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        return SmartConnect(
            host=self.source.hostname,
            port=self.source.port or 443,
            user=self.source.username,
            pwd=self.source.password,
            sslContext=ssl_context,
        )

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        try:
            si = self._connect()
            # If SmartConnect succeeds we have a valid session.
            try:
                from pyVim.connect import Disconnect  # type: ignore[import]
                Disconnect(si)
            except Exception:  # noqa: BLE001
                pass
            return ConnectionCheck(success=True, message="Connected to vCenter.")
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=f"vCenter authentication failed: {type(exc).__name__}: {exc}",
            )

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        raw: dict[str, Any] = {"vms": [], "hosts": []}

        try:
            from pyVmomi import vim  # type: ignore[import]
            from pyVim.connect import Disconnect  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError("pyvmomi is required.") from exc

        try:
            si = self._connect()
            content = si.RetrieveContent()
            container = content.rootFolder
            view_type = [vim.VirtualMachine, vim.HostSystem]
            view = content.viewManager.CreateContainerView(container, view_type, True)

            for obj in view.view:
                if isinstance(obj, vim.VirtualMachine):
                    summary = obj.summary
                    raw["vms"].append(
                        {
                            "moref": str(obj._moId),
                            "name": summary.config.name if summary.config else obj.name,
                            "power_state": summary.runtime.powerState if summary.runtime else "unknown",
                            "num_cpu": summary.config.numCpu if summary.config else None,
                            "memory_mb": summary.config.memorySizeMB if summary.config else None,
                            "guest_os": summary.config.guestFullName if summary.config else None,
                            "hostname": summary.guest.hostName if summary.guest else None,
                            "ip_address": summary.guest.ipAddress if summary.guest else None,
                            "cpu_usage_mhz": summary.quickStats.overallCpuUsage if summary.quickStats else None,
                            "memory_usage_mb": summary.quickStats.guestMemoryUsage if summary.quickStats else None,
                        }
                    )
                elif isinstance(obj, vim.HostSystem):
                    summary = obj.summary
                    raw["hosts"].append(
                        {
                            "moref": str(obj._moId),
                            "name": obj.name,
                            "connection_state": summary.runtime.connectionState if summary.runtime else "unknown",
                            "power_state": summary.runtime.powerState if summary.runtime else "unknown",
                            "cpu_usage_mhz": summary.quickStats.overallCpuUsage if summary.quickStats else None,
                            "memory_usage_mb": summary.quickStats.overallMemoryUsage if summary.quickStats else None,
                        }
                    )

            view.Destroy()
            Disconnect(si)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "VMware collect(): failed: source_id=%s error=%s",
                self.source.id, exc,
            )

        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        devices: list[NormalizedDevice] = []
        sensors: list[NormalizedSensorReading] = []

        # VMs
        for vm in raw.get("vms", []):
            power = (vm.get("power_state") or "").lower()
            status = "up" if power == "poweredon" else "down"
            device = NormalizedDevice(
                external_id=vm.get("moref") or vm.get("name") or "unknown",
                name=vm.get("name") or "unknown-vm",
                device_type="vm",
                vendor="VMware",
                model=None,
                os_version=vm.get("guest_os"),
                hostname=vm.get("hostname"),
                ip_address=vm.get("ip_address"),
                status=status,
            )
            devices.append(device)

            ext_id = device.external_id
            if vm.get("cpu_usage_mhz") is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"{ext_id}-cpu",
                    name="CPU Usage",
                    metric_type="cpu_usage_mhz",
                    value=float(vm["cpu_usage_mhz"]),
                    unit="MHz",
                    status=status,
                    device_external_id=ext_id,
                ))
            if vm.get("memory_usage_mb") is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"{ext_id}-mem",
                    name="Memory Usage",
                    metric_type="memory_usage_mb",
                    value=float(vm["memory_usage_mb"]),
                    unit="MB",
                    status=status,
                    device_external_id=ext_id,
                ))

        # ESXi hosts
        for host in raw.get("hosts", []):
            conn = (host.get("connection_state") or "").lower()
            power = (host.get("power_state") or "").lower()
            status = "up" if (conn == "connected" and power == "poweredon") else "down"
            device = NormalizedDevice(
                external_id=host.get("moref") or host.get("name") or "unknown",
                name=host.get("name") or "unknown-host",
                device_type="server",
                vendor="VMware",
                status=status,
            )
            devices.append(device)

        return NormalizedBundle(devices=devices, sensors=sensors)
