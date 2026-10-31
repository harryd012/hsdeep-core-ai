"""
Backup monitoring persistence (docs/engineering/azure-backup-monitoring.md).

Called by `BundleSaver.save()` for bundles that carry backup tables
(`NormalizedBundle.backup_status`). It:

  - upserts one `backup_vaults` row per vault (linked to the vault's
    cloud_resource Device), overwriting posture only when the vault table was
    read this poll;
  - syncs `backup_items`, deleting items a successful read no longer lists;
  - upserts `backup_jobs` by the provider's job id (history accumulates);
  - turns item and vault state into sensor readings, so backup alerts open and
    auto-resolve through the existing sensor path (AlertService.evaluate_sensor),
    exactly like firewall state - no second alerting mechanism.

Rules (all from what the provider reported; nothing is inferred):
  * RPO        - a protected item with no backup inside `backup_rpo_hours`.
  * last backup - the provider's last backup verdict (failed / with warnings).
  * protection - protection stopped, paused or in error.
  * soft delete - vault soft delete disabled.
Items whose protection was stopped get an "unknown" RPO reading, which
resolves an RPO alert instead of paging about an item nobody protects.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import (
    NormalizedBackupItem,
    NormalizedBackupVault,
    NormalizedBundle,
    NormalizedSensorReading,
)
from app.core.config import get_settings
from app.models.backup import BackupItem, BackupJob, BackupVault

_READ_OK = ("collected", "empty")
#: Prefix of every sensor external_id this module produces.
SENSOR_PREFIX = "bk:"

_ITEM_FIELDS = (
    "name", "workload_type", "protection_state", "last_backup_status", "last_backup_at",
    "source_resource_id", "policy_name", "health_status",
)
_ACTIVE_PROTECTION = ("protected", "protection_error", "ir_pending")
_STOPPED_PROTECTION = ("protection_stopped", "protection_paused")


@dataclass
class BackupSaveResult:
    vaults_updated: int = 0
    items_upserted: int = 0
    items_retired: int = 0
    jobs_upserted: int = 0
    readings: list[NormalizedSensorReading] = field(default_factory=list)


def _reading(vault_ext: str, key: str, name: str, metric: str, value: float | None, status: str,
             unit: str | None = None, warning: float | None = None, critical: float | None = None) -> NormalizedSensorReading:
    return NormalizedSensorReading(
        external_id=f"{SENSOR_PREFIX}{vault_ext}:{key}", name=name, metric_type=metric, value=value,
        unit=unit, status=status, warning_threshold=warning, critical_threshold=critical,
        device_external_id=vault_ext,
    )


def vault_readings(vault: NormalizedBackupVault) -> list[NormalizedSensorReading]:
    """Posture readings for one vault. Pure."""
    if vault.soft_delete_state is None:
        return []
    on = vault.soft_delete_state in ("enabled", "always_on")
    return [
        _reading(vault.external_id, "soft_delete", f"{vault.name} soft delete", "backup_soft_delete",
                 1.0 if on else 0.0, "up" if on else "warning")
    ]


def item_readings(item: NormalizedBackupItem, vault_name: str, now: datetime, rpo_hours: int) -> list[NormalizedSensorReading]:
    """RPO, last-backup and protection readings for one protected item. Pure."""
    label = f"{item.name} ({vault_name})"
    key = f"item:{item.external_id.rsplit('/', 1)[-1]}"
    out: list[NormalizedSensorReading] = []

    if item.protection_state in _ACTIVE_PROTECTION:
        age = (now - item.last_backup_at).total_seconds() / 3600 if item.last_backup_at else None
        breached = age is None or age > rpo_hours
        out.append(_reading(item.vault_external_id, f"{key}:rpo", f"{label} backup age", "backup_age_hours",
                            round(age, 1) if age is not None else None, "down" if breached else "up",
                            "h", critical=float(rpo_hours)))
    elif item.protection_state in _STOPPED_PROTECTION:
        # Nobody protects it any more: resolve an RPO alert rather than page.
        out.append(_reading(item.vault_external_id, f"{key}:rpo", f"{label} backup age", "backup_age_hours",
                            None, "unknown", "h", critical=float(rpo_hours)))

    verdict = {"completed": ("up", 1.0), "completed_with_warnings": ("warning", 0.5), "failed": ("down", 0.0)}
    if item.last_backup_status in verdict:
        status, value = verdict[item.last_backup_status]
        out.append(_reading(item.vault_external_id, f"{key}:last", f"{label} last backup", "backup_last_result",
                            value, status))

    if item.protection_state is not None:
        status = (
            "down" if item.protection_state == "protection_error"
            else "warning" if item.protection_state in _STOPPED_PROTECTION
            else "up"
        )
        out.append(_reading(item.vault_external_id, f"{key}:protection", f"{label} protection",
                            "backup_protection", 1.0 if status == "up" else 0.0, status))
    return out


def _resolve_item(vault_ext: str, external_id: str, name: str, vault_name: str) -> list[NormalizedSensorReading]:
    """'unknown' readings that resolve every alert of an item that disappeared."""
    key = f"item:{external_id.rsplit('/', 1)[-1]}"
    label = f"{name} ({vault_name})"
    return [
        _reading(vault_ext, f"{key}:rpo", f"{label} backup age", "backup_age_hours", None, "unknown", "h"),
        _reading(vault_ext, f"{key}:last", f"{label} last backup", "backup_last_result", None, "unknown"),
        _reading(vault_ext, f"{key}:protection", f"{label} protection", "backup_protection", None, "unknown"),
    ]


async def save_backup(
    db: AsyncSession,
    *,
    tenant_id,
    source_id,
    bundle: NormalizedBundle,
    device_id_by_external: dict[str, object],
    now: datetime,
) -> BackupSaveResult:
    result = BackupSaveResult()
    tables = bundle.backup_status
    if not tables or tables.get("vaults") not in _READ_OK:
        return result
    rpo_hours = get_settings().backup_rpo_hours

    items_by_vault: dict[str, list[NormalizedBackupItem]] = {}
    for item in bundle.backup_items:
        items_by_vault.setdefault(item.vault_external_id, []).append(item)
    jobs_by_vault: dict[str, list] = {}
    for job in bundle.backup_jobs:
        jobs_by_vault.setdefault(job.vault_external_id, []).append(job)

    for nv in bundle.backup_vaults:
        vault = (
            await db.execute(
                select(BackupVault).where(BackupVault.tenant_id == tenant_id, BackupVault.external_id == nv.external_id)
            )
        ).scalar_one_or_none()
        if vault is None:
            vault = BackupVault(tenant_id=tenant_id, external_id=nv.external_id, name=nv.name, table_status={})
            db.add(vault)
        vault.device_id = device_id_by_external.get(nv.external_id)
        vault.monitoring_source_id = source_id
        vault.name = nv.name
        vault.provider = nv.provider
        vault.location = nv.location
        vault.resource_group = nv.resource_group
        vault.subscription_id = nv.subscription_id
        vault.soft_delete_state = nv.soft_delete_state
        vault.immutability_state = nv.immutability_state
        vault.storage_redundancy = nv.storage_redundancy
        vault.table_status = dict(tables)
        vault.observed_at = now
        await db.flush()
        result.vaults_updated += 1
        result.readings.extend(vault_readings(nv))

        if tables.get("items") in _READ_OK:
            await _sync_items(db, tenant_id, vault, nv, items_by_vault.get(nv.external_id, []), now, rpo_hours, result)
        if tables.get("jobs") in _READ_OK:
            await _upsert_jobs(db, tenant_id, vault, jobs_by_vault.get(nv.external_id, []), result)

    await db.flush()
    return result


async def _sync_items(db, tenant_id, vault: BackupVault, nv: NormalizedBackupVault, items, now, rpo_hours, result) -> None:
    existing = {
        row.external_id: row
        for row in (await db.execute(select(BackupItem).where(BackupItem.vault_id == vault.id))).scalars()
    }
    seen: set[str] = set()
    for item in items:
        if item.external_id in seen:
            continue
        seen.add(item.external_id)
        row = existing.get(item.external_id)
        if row is None:
            row = BackupItem(tenant_id=tenant_id, vault_id=vault.id, external_id=item.external_id, name=item.name)
            db.add(row)
        for f in _ITEM_FIELDS:
            setattr(row, f, getattr(item, f))
        row.observed_at = now
        result.items_upserted += 1
        result.readings.extend(item_readings(item, nv.name, now, rpo_hours))

    for ext, row in existing.items():
        if ext in seen:
            continue
        result.readings.extend(_resolve_item(nv.external_id, ext, row.name, nv.name))
        await db.delete(row)
        result.items_retired += 1


async def _upsert_jobs(db, tenant_id, vault: BackupVault, jobs, result) -> None:
    if not jobs:
        return
    rows = {}
    for job in jobs:
        rows[job.external_id] = {
            "tenant_id": tenant_id,
            "vault_id": vault.id,
            "external_id": job.external_id,
            "item_name": job.item_name,
            "operation": job.operation,
            "status": job.status,
            "started_at": job.started_at,
            "ended_at": job.ended_at,
            "duration_seconds": job.duration_seconds,
            "error_code": job.error_code,
            "error_message": job.error_message,
        }
    values = [{"id": uuid.uuid4(), **r} for r in rows.values()]
    stmt = pg_insert(BackupJob).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_backup_jobs_vault_external",
        set_={
            c: stmt.excluded[c]
            for c in ("item_name", "operation", "status", "started_at", "ended_at",
                      "duration_seconds", "error_code", "error_message")
        },
    )
    await db.execute(stmt)
    result.jobs_upserted += len(values)
