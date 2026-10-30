"""Backup monitoring read model (docs/engineering/azure-backup-monitoring.md).

Tenant-scoped reads over backup_vaults / backup_items / backup_jobs for the
Backup page, the dashboard panel and the Cloud Operations "Backup Readiness"
card. The RPO verdict is computed here once (``rpo_state``) so every surface
agrees, and it uses the same rule as the alerting readings in
`app.services.monitoring.backup_saver`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.backup import BackupItem, BackupJob, BackupVault
from app.models.monitoring_source import MonitoringSource
from app.schemas.backup import (
    BackupDailyJobs,
    BackupItemOut,
    BackupJobCounts,
    BackupJobOut,
    BackupOverview,
    BackupSourceSummary,
    BackupVaultOut,
)

SOURCE_TYPE = "azure_backup"
_ACTIVE = ("protected", "protection_error", "ir_pending")
_SUCCESS = ("completed", "completed_with_warnings")
_FINISHED = ("completed", "completed_with_warnings", "failed", "cancelled")
DAILY_DAYS = 14


def rpo_state(item: BackupItem, now: datetime, rpo_hours: int) -> tuple[str, float | None]:
    """(within | breached | never | not_protected, age in hours or None)."""
    age = (now - item.last_backup_at).total_seconds() / 3600 if item.last_backup_at else None
    if item.protection_state not in _ACTIVE:
        return "not_protected", age
    if age is None:
        return "never", None
    return ("breached" if age > rpo_hours else "within"), age


def _counts(rows) -> BackupJobCounts:
    out = BackupJobCounts()
    for status, n in rows:
        out.total += n
        if status in ("completed", "completed_with_warnings", "failed", "in_progress"):
            setattr(out, status, getattr(out, status) + n)
        else:
            out.other += n
    return out


class BackupService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _sources(self, tenant_id: uuid.UUID) -> list[MonitoringSource]:
        return list(
            (
                await self.db.execute(
                    select(MonitoringSource)
                    .where(MonitoringSource.tenant_id == tenant_id, MonitoringSource.source_type == SOURCE_TYPE)
                    .order_by(MonitoringSource.name)
                )
            ).scalars()
        )

    async def _job_counts(self, tenant_id: uuid.UUID, since: datetime) -> BackupJobCounts:
        rows = (
            await self.db.execute(
                select(BackupJob.status, func.count())
                .where(BackupJob.tenant_id == tenant_id, BackupJob.started_at >= since)
                .group_by(BackupJob.status)
            )
        ).all()
        return _counts(rows)

    async def overview(self, tenant_id: uuid.UUID, now: datetime | None = None) -> BackupOverview:
        now = now or datetime.now(timezone.utc)
        rpo_hours = get_settings().backup_rpo_hours
        sources = await self._sources(tenant_id)
        vaults = list((await self.db.execute(select(BackupVault).where(BackupVault.tenant_id == tenant_id))).scalars())
        items = list((await self.db.execute(select(BackupItem).where(BackupItem.tenant_id == tenant_id))).scalars())

        states = [rpo_state(i, now, rpo_hours)[0] for i in items]
        jobs_24h = await self._job_counts(tenant_id, now - timedelta(hours=24))
        finished_24h = jobs_24h.completed + jobs_24h.completed_with_warnings + jobs_24h.failed
        success_24h = (
            round((jobs_24h.completed + jobs_24h.completed_with_warnings) / finished_24h * 100, 1) if finished_24h else None
        )

        start_day = (now - timedelta(days=DAILY_DAYS - 1)).date()
        day_col = func.date_trunc("day", BackupJob.started_at)
        daily_rows = (
            await self.db.execute(
                select(day_col, BackupJob.status, func.count())
                .where(
                    BackupJob.tenant_id == tenant_id,
                    BackupJob.started_at >= datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc),
                )
                .group_by(day_col, BackupJob.status)
            )
        ).all()
        days = {start_day + timedelta(days=i): BackupDailyJobs(day=start_day + timedelta(days=i)) for i in range(DAILY_DAYS)}
        for day, status, n in daily_rows:
            bucket = days.get(day.date() if isinstance(day, datetime) else day)
            if bucket is None:
                continue
            key = status if status in ("completed", "completed_with_warnings", "failed") else "other"
            setattr(bucket, key, getattr(bucket, key) + n)

        last_job_at = (
            await self.db.execute(select(func.max(BackupJob.started_at)).where(BackupJob.tenant_id == tenant_id))
        ).scalar()

        return BackupOverview(
            configured=bool(sources),
            generated_at=now,
            rpo_hours=rpo_hours,
            sources=[
                BackupSourceSummary(
                    id=s.id, name=s.name, status=s.status, is_active=s.is_active,
                    last_sync_at=s.last_sync_at, last_error=s.last_error,
                )
                for s in sources
            ],
            vaults=len(vaults),
            vaults_soft_delete_off=sum(1 for v in vaults if v.soft_delete_state == "disabled"),
            items_total=len(items),
            items_protected=sum(1 for i in items if i.protection_state in _ACTIVE),
            items_within_rpo=states.count("within"),
            items_breaching_rpo=states.count("breached"),
            items_never_backed_up=states.count("never"),
            items_not_protected=states.count("not_protected"),
            items_last_failed=sum(1 for i in items if i.last_backup_status == "failed"),
            success_rate_24h=success_24h,
            jobs_24h=jobs_24h,
            jobs_7d=await self._job_counts(tenant_id, now - timedelta(days=7)),
            daily=[days[d] for d in sorted(days)],
            last_job_at=last_job_at,
            observed_at=max((v.observed_at for v in vaults if v.observed_at), default=None),
        )

    async def vaults(self, tenant_id: uuid.UUID, now: datetime | None = None) -> list[BackupVaultOut]:
        now = now or datetime.now(timezone.utc)
        rpo_hours = get_settings().backup_rpo_hours
        vaults = list(
            (await self.db.execute(select(BackupVault).where(BackupVault.tenant_id == tenant_id).order_by(BackupVault.name))).scalars()
        )
        items = list((await self.db.execute(select(BackupItem).where(BackupItem.tenant_id == tenant_id))).scalars())
        out = []
        for v in vaults:
            mine = [i for i in items if i.vault_id == v.id]
            out.append(
                BackupVaultOut(
                    id=v.id, device_id=v.device_id, name=v.name, provider=v.provider, location=v.location,
                    resource_group=v.resource_group, subscription_id=v.subscription_id,
                    soft_delete_state=v.soft_delete_state, immutability_state=v.immutability_state,
                    storage_redundancy=v.storage_redundancy, observed_at=v.observed_at,
                    items_total=len(mine),
                    items_breaching_rpo=sum(1 for i in mine if rpo_state(i, now, rpo_hours)[0] in ("breached", "never")),
                )
            )
        return out

    async def items(
        self, tenant_id: uuid.UUID, *, vault_id: uuid.UUID | None = None, state: str | None = None,
        now: datetime | None = None,
    ) -> list[BackupItemOut]:
        now = now or datetime.now(timezone.utc)
        rpo_hours = get_settings().backup_rpo_hours
        stmt = (
            select(BackupItem, BackupVault.name)
            .join(BackupVault, BackupVault.id == BackupItem.vault_id)
            .where(BackupItem.tenant_id == tenant_id)
            .order_by(BackupItem.name)
        )
        if vault_id:
            stmt = stmt.where(BackupItem.vault_id == vault_id)
        out = []
        for item, vault_name in (await self.db.execute(stmt)).all():
            verdict, age = rpo_state(item, now, rpo_hours)
            if state and verdict != state:
                continue
            out.append(
                BackupItemOut(
                    id=item.id, vault_id=item.vault_id, vault_name=vault_name, name=item.name,
                    workload_type=item.workload_type, protection_state=item.protection_state,
                    last_backup_status=item.last_backup_status, last_backup_at=item.last_backup_at,
                    age_hours=round(age, 1) if age is not None else None, rpo_state=verdict,
                    policy_name=item.policy_name, health_status=item.health_status,
                    source_resource_id=item.source_resource_id,
                )
            )
        return out

    async def jobs(
        self, tenant_id: uuid.UUID, *, days: int = 7, status: str | None = None, limit: int = 200,
        now: datetime | None = None,
    ) -> list[BackupJobOut]:
        now = now or datetime.now(timezone.utc)
        stmt = (
            select(BackupJob, BackupVault.name)
            .join(BackupVault, BackupVault.id == BackupJob.vault_id)
            .where(BackupJob.tenant_id == tenant_id, BackupJob.started_at >= now - timedelta(days=days))
            .order_by(BackupJob.started_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(BackupJob.status == status)
        return [
            BackupJobOut(
                id=j.id, vault_id=j.vault_id, vault_name=vault_name, item_name=j.item_name, operation=j.operation,
                status=j.status, started_at=j.started_at, ended_at=j.ended_at, duration_seconds=j.duration_seconds,
                error_code=j.error_code, error_message=j.error_message,
            )
            for j, vault_name in (await self.db.execute(stmt)).all()
        ]


def readiness_from_overview(o: BackupOverview) -> tuple[int | None, dict]:
    """Backup Readiness card: % of protected items inside the RPO."""
    measured = o.items_protected
    value = round(o.items_within_rpo / measured * 100) if measured else None
    return value, {
        "protected": o.items_protected,
        "within_rpo": o.items_within_rpo,
        "breaching_rpo": o.items_breaching_rpo,
        "never_backed_up": o.items_never_backed_up,
        "last_failed": o.items_last_failed,
        "vaults": o.vaults,
        "rpo_hours": o.rpo_hours,
    }


__all__ = ["BackupService", "readiness_from_overview", "rpo_state", "SOURCE_TYPE"]
