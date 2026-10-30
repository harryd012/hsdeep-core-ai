"""
Azure Backup collector (docs/engineering/azure-backup-monitoring.md).

Reads Recovery Services vaults, their protected items and recent backup jobs
through the Azure Resource Manager REST API (read-only), using the same
service-principal credentials as the Azure Monitor collector:

    config["tenant_id"]       -> Azure AD (Entra ID) tenant ID
    config["subscription_id"] -> subscription to read vaults from
    config["client_id"]       -> service principal / app registration client ID
    password or api_key       -> client secret (encrypted at rest)

The service principal needs only the built-in "Backup Reader" role on the
subscription (or on each vault).

Calls (one subscription, then per vault), following ``nextLink`` paging:
    GET /subscriptions/{sub}/providers/Microsoft.RecoveryServices/vaults
    GET {vault}/backupProtectedItems
    GET {vault}/backupJobs?$filter=startTime eq '...' and endTime eq '...'

Each table reports collected | empty | failed in ``bundle.backup_status``, so
a failed read never wipes what an earlier poll stored. Nothing is inferred: a
setting Azure did not return stays None.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.collectors.azure import AzureCollector
from app.collectors.base import (
    CollectorConfigError,
    CollectorRegistry,
    NormalizedBackupItem,
    NormalizedBackupJob,
    NormalizedBackupVault,
    NormalizedBundle,
    NormalizedDevice,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
#: Recovery Services vault, protected item and job APIs.
VAULTS_API = "2023-04-01"
BACKUP_API = "2023-04-01"
#: Safety cap on pages followed per listing (a runaway nextLink loop must end).
MAX_PAGES = 50


def snake(value: str | None) -> str | None:
    """'CompletedWithWarnings' -> 'completed_with_warnings'; None stays None."""
    if not value:
        return None
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value.strip()).replace(" ", "_").lower()


_PROTECTION = {
    "protected": "protected",
    "protection_stopped": "protection_stopped",
    "protection_paused": "protection_paused",
    "protection_error": "protection_error",
    "i_r_pending": "ir_pending",
    "irpending": "ir_pending",
}
_SOFT_DELETE = {"enabled": "enabled", "disabled": "disabled", "always_o_n": "always_on", "always_on": "always_on"}


def _ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year < 1900:  # Azure reports "0001-01-01T00:00:00Z" for "never"
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _filter_time(dt: datetime) -> str:
    """The job filter's documented time format: '2024-01-31 11:59:59 PM' (UTC)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %I:%M:%S %p")


def _resource_group(resource_id: str) -> str | None:
    match = re.search(r"/resourceGroups/([^/]+)", resource_id, re.IGNORECASE)
    return match.group(1) if match else None


@CollectorRegistry.register("azure_backup")
class AzureBackupCollector(AzureCollector):
    display_name = "Azure Backup (Recovery Services vaults)"
    implemented = True
    category = "cloud"

    #: Injected by tests (httpx.MockTransport); None uses the real network.
    transport: httpx.AsyncBaseTransport | None = None

    async def _bearer_token(self) -> str:
        credential = self._get_credential()
        token = await asyncio.to_thread(credential.get_token, ARM_SCOPE)
        if not token or not token.token:
            raise CollectorConfigError("Azure: token acquisition returned an empty token.")
        return token.token

    async def _list(self, client: httpx.AsyncClient, url: str, params: dict[str, str] | None) -> list[dict]:
        """Every `value` row across `nextLink` pages."""
        rows: list[dict] = []
        next_url: str | None = url
        next_params = params
        for _ in range(MAX_PAGES):
            if not next_url:
                break
            resp = await client.get(next_url, params=next_params)
            resp.raise_for_status()
            body = resp.json()
            rows.extend(body.get("value") or [])
            next_url = body.get("nextLink")
            next_params = None  # nextLink already carries the query string
        return rows

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        cfg = self.source.config or {}
        subscription_id = str(cfg["subscription_id"])
        settings = get_settings()
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=settings.backup_job_lookback_hours)
        raw: dict[str, Any] = {"subscription_id": subscription_id, "vaults": [], "status": {}}

        token = await self._bearer_token()
        async with httpx.AsyncClient(
            base_url=ARM,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0),
            transport=self.transport,
        ) as client:
            # The vault listing is the base of everything: if it fails, the run
            # fails (raise) instead of reporting an empty estate.
            vaults = await self._list(
                client,
                f"/subscriptions/{subscription_id}/providers/Microsoft.RecoveryServices/vaults",
                {"api-version": VAULTS_API},
            )
            raw["status"]["vaults"] = "collected" if vaults else "empty"
            items_failed = jobs_failed = False
            for vault in vaults:
                vault_id = vault.get("id") or ""
                entry: dict[str, Any] = {"vault": vault, "items": None, "jobs": None}
                try:
                    entry["items"] = await self._list(client, f"{vault_id}/backupProtectedItems", {"api-version": BACKUP_API})
                except httpx.HTTPError as exc:
                    items_failed = True
                    logger.warning("Azure Backup: items read failed for %s: %s", vault_id, exc)
                try:
                    entry["jobs"] = await self._list(
                        client,
                        f"{vault_id}/backupJobs",
                        {
                            "api-version": BACKUP_API,
                            "$filter": f"startTime eq '{_filter_time(start)}' and endTime eq '{_filter_time(now)}'",
                        },
                    )
                except httpx.HTTPError as exc:
                    jobs_failed = True
                    logger.warning("Azure Backup: jobs read failed for %s: %s", vault_id, exc)
                raw["vaults"].append(entry)
            if vaults:
                any_items = any(e["items"] for e in raw["vaults"])
                any_jobs = any(e["jobs"] for e in raw["vaults"])
                raw["status"]["items"] = "failed" if items_failed else ("collected" if any_items else "empty")
                raw["status"]["jobs"] = "failed" if jobs_failed else ("collected" if any_jobs else "empty")
            else:
                raw["status"]["items"] = raw["status"]["jobs"] = "empty"
        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        bundle = NormalizedBundle(backup_status=dict(raw.get("status") or {}))
        subscription_id = raw.get("subscription_id")
        for entry in raw.get("vaults") or []:
            vault = entry.get("vault") or {}
            vault_id = (vault.get("id") or "").lower()
            if not vault_id:
                continue
            props = vault.get("properties") or {}
            security = props.get("securitySettings") or {}
            redundancy = (props.get("redundancySettings") or {}).get("standardTierStorageRedundancy")
            provisioning = (props.get("provisioningState") or "").lower()
            name = vault.get("name") or vault_id.rsplit("/", 1)[-1]

            bundle.devices.append(
                NormalizedDevice(
                    external_id=vault_id,
                    name=name,
                    device_type="cloud_resource",
                    vendor="Microsoft",
                    model="Recovery Services vault",
                    status="up" if provisioning in ("", "succeeded") else "warning",
                    custom_metadata={
                        "provider": "azure",
                        "resource_type": "Microsoft.RecoveryServices/vaults",
                        "location": vault.get("location"),
                        "subscription_id": subscription_id,
                    },
                )
            )
            soft = snake((security.get("softDeleteSettings") or {}).get("softDeleteState"))
            bundle.backup_vaults.append(
                NormalizedBackupVault(
                    external_id=vault_id,
                    name=name,
                    location=vault.get("location"),
                    resource_group=_resource_group(vault_id),
                    subscription_id=subscription_id,
                    soft_delete_state=_SOFT_DELETE.get(soft or "", soft),
                    immutability_state=snake((security.get("immutabilitySettings") or {}).get("state")),
                    storage_redundancy=snake(redundancy),
                )
            )

            for item in entry.get("items") or []:
                ip = item.get("properties") or {}
                item_id = (item.get("id") or "").lower()
                if not item_id:
                    continue
                state = snake(ip.get("protectionState"))
                policy_id = ip.get("policyId") or ""
                bundle.backup_items.append(
                    NormalizedBackupItem(
                        external_id=item_id,
                        vault_external_id=vault_id,
                        name=ip.get("friendlyName") or item.get("name") or item_id.rsplit("/", 1)[-1],
                        workload_type=ip.get("workloadType"),
                        protection_state=_PROTECTION.get(state or "", state),
                        last_backup_status=snake(ip.get("lastBackupStatus")),
                        last_backup_at=_ts(ip.get("lastBackupTime")),
                        source_resource_id=ip.get("sourceResourceId"),
                        policy_name=ip.get("policyName") or (policy_id.rsplit("/", 1)[-1] or None),
                        health_status=snake(ip.get("healthStatus")),
                    )
                )

            for job in entry.get("jobs") or []:
                jp = job.get("properties") or {}
                job_id = (job.get("id") or job.get("name") or "").lower()
                if not job_id:
                    continue
                started, ended = _ts(jp.get("startTime")), _ts(jp.get("endTime"))
                errors = jp.get("errorDetails") or (jp.get("extendedInfo") or {}).get("errorDetails") or []
                first_error = errors[0] if errors else {}
                status = snake(jp.get("status")) or "other"
                bundle.backup_jobs.append(
                    NormalizedBackupJob(
                        external_id=job_id,
                        vault_external_id=vault_id,
                        item_name=jp.get("entityFriendlyName"),
                        operation=jp.get("operation"),
                        status="cancelled" if status == "cancelling" else status,
                        started_at=started,
                        ended_at=ended,
                        duration_seconds=(ended - started).total_seconds() if started and ended else None,
                        error_code=first_error.get("errorCode"),
                        error_message=first_error.get("errorString") or first_error.get("errorTitle"),
                    )
                )
        return bundle
