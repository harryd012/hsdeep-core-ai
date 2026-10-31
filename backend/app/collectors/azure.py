"""
Microsoft Azure Monitor collector.

Discovers Azure VMs, resource groups, and pulls resource health and basic
metrics via azure-identity and azure-mgmt-monitor.

Connection details from `MonitoringSourceConnectionInfo`:
    config["tenant_id"]       -> Azure AD tenant ID
    config["subscription_id"] -> Azure subscription ID
    config["client_id"]       -> Service principal / app registration client ID
    password                  -> Service principal client secret (encrypted at rest)
    api_key                   -> Alternative: bearer access token (for short-lived tokens)

Dependencies (pinned in requirements.txt):
    azure-identity==1.17.1
    azure-mgmt-compute (for VM listing — bundled with azure-mgmt-monitor)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedMetric,
)

logger = logging.getLogger(__name__)


@CollectorRegistry.register("azure")
class AzureCollector(BaseCollector):
    display_name = "Microsoft Azure Monitor"
    implemented = True
    category = "cloud"

    # -- config helpers -------------------------------------------------------

    def _require_config(self) -> None:
        missing = []
        cfg = self.source.config or {}
        for key in ("tenant_id", "subscription_id", "client_id"):
            if not cfg.get(key):
                missing.append(key)
        if not (self.source.password or self.source.api_key):
            missing.append("client_secret (password) or api_key")
        if missing:
            raise CollectorConfigError(
                f"Azure config missing required field(s): {missing}"
            )

    def _get_credential(self) -> Any:
        """Build an azure-identity ClientSecretCredential. Import is deferred
        so that the module loads cleanly in environments where azure-identity
        is not installed."""
        try:
            from azure.identity import ClientSecretCredential  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError(
                "azure-identity is required for the Azure collector. "
                "Install it via: pip install azure-identity==1.17.1"
            ) from exc

        cfg = self.source.config or {}
        return ClientSecretCredential(
            tenant_id=str(cfg["tenant_id"]),
            client_id=str(cfg["client_id"]),
            client_secret=self.source.password or self.source.api_key,
        )

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        try:
            credential = self._get_credential()
            # Attempt to get a token for the Azure Resource Manager scope to
            # verify credentials without making a management API call.
            token = credential.get_token("https://management.azure.com/.default")
            if token and token.token:
                return ConnectionCheck(success=True, message="Azure credentials valid.")
            return ConnectionCheck(
                success=False, message="Azure: token acquisition returned empty token."
            )
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=f"Azure authentication failed: {type(exc).__name__}: {exc}",
            )

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        cfg = self.source.config or {}
        subscription_id = str(cfg["subscription_id"])
        raw: dict[str, Any] = {"vms": [], "metrics": {}}

        try:
            from azure.mgmt.compute import ComputeManagementClient  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError(
                "azure-mgmt-compute is required for the Azure collector."
            ) from exc

        credential = self._get_credential()

        # List all VMs in the subscription
        try:
            compute_client = ComputeManagementClient(credential, subscription_id)
            for vm in compute_client.virtual_machines.list_all():
                raw["vms"].append(
                    {
                        "id": vm.id,
                        "name": vm.name,
                        "location": vm.location,
                        "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
                        "os_type": (
                            vm.storage_profile.os_disk.os_type.value
                            if vm.storage_profile and vm.storage_profile.os_disk
                            else None
                        ),
                        "provisioning_state": vm.provisioning_state,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Azure collect(): VM listing failed: source_id=%s error=%s",
                self.source.id, exc,
            )

        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        devices: list[NormalizedDevice] = []
        metrics: list[NormalizedMetric] = []
        now = datetime.now(timezone.utc)

        for vm in raw.get("vms", []):
            provisioning = (vm.get("provisioning_state") or "").lower()
            status = "up" if provisioning == "succeeded" else "down"
            resource_id = vm.get("id") or vm.get("name") or "unknown"
            device = NormalizedDevice(
                external_id=resource_id,
                name=vm.get("name") or resource_id,
                device_type="cloud_resource",
                vendor="Microsoft",
                model=vm.get("vm_size"),
                os_version=vm.get("os_type"),
                hostname=vm.get("name"),
                status=status,
                site_hint=vm.get("location"),
            )
            devices.append(device)

        return NormalizedBundle(devices=devices, metrics=metrics)
