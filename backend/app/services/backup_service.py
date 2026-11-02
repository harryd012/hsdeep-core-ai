"""Backup monitoring read model (docs/engineering/azure-backup-monitoring.md).

Tenant-scoped reads over backup_vaults / backup_items / backup_jobs /
backup_policies for the Backup page, the dashboard panel and the Cloud
Operations "Backup Readiness" card, plus backup coverage: which Azure VMs in
inventory have no backup. The RPO verdict is computed here once (``rpo_state``) so every surface
agrees, and it uses the same rule and the same tenant RPO as the alerting
readings in `app.services.monitoring.backup_saver`.

Provider-neutral: backup sources are every collector that declares a
``backup_provider``, so a new backup connector appears here without edits.
Coverage stays Azure-specific (it matches Azure VM resource ids).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.backup_vocabulary import ACTIVE_PROTECTION
from app.collectors.base import CollectorRegistry
from app.core.config import get_settings
from app.models.backup import BackupItem, BackupJob, BackupPolicy, BackupVault
from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.schemas.backup import (
    BackupCoverage,
    BackupCoverageResource,
    BackupDailyJobs,
    BackupItemDetail,
    BackupItemOut,
    BackupJobCounts,
    BackupJobOut,
    BackupOverview,
    BackupPolicyOut,
    BackupSettingsOut,
    BackupSourceSummary,
    BackupVaultOut,
)
from app.services.backup_settings import tenant_rpo_override

#: The Azure Backup collector's source type (coverage reads its subscriptions).
SOURCE_TYPE = "azure_backup"
_ACTIVE = ACTIVE_PROTECTION
_SUCCESS = ("completed", "completed_with_warnings")
_FINISHED = ("completed", "completed_with_warnings", "failed", "cancelled")
DAILY_DAYS = 14
#: Inventory resources Azure Backup can protect, by resource id (lower case).
_PROTECTABLE = "/providers/microsoft.compute/virtualmachines/"


def _subscription(resource_id: str | None) -> str | None:
    match = re.search(r"/subscriptions/([^/]+)", resource_id or "", re.IGNORECASE)
    return match.group(1).lower() if match else None


def backup_source_types(provider: str | None = None) -> dict[str, str]:
    """{source_type: provider} for every registered backup collector,
    optionally only one provider's."""
    import app.services.monitoring  # noqa: F401  (registers every collector)

    return {
        cls.source_type: cls.backup_provider
        for cls in CollectorRegistry.list_types()
        if cls.backup_provider and (provider is None or cls.backup_provider == provider)
    }


def _policy_out(p: BackupPolicy) -> BackupPolicyOut:
    return BackupPolicyOut(
        name=p.name, workload_type=p.workload_type, schedule_frequency=p.schedule_frequency,
        schedule_times=list(p.schedule_times or []), daily_retention_days=p.daily_retention_days,
        weekly_retention_weeks=p.weekly_retention_weeks, monthly_retention_months=p.monthly_retention_months,
        yearly_retention_years=p.yearly_retention_years, protected_items_count=p.protected_items_count,
    )


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

    async def _sources(self, tenant_id: uuid.UUID, provider: str | None = None) -> list[MonitoringSource]:
        """The tenant's backup sources (all providers, or one)."""
        types = list(backup_source_types(provider))
        return list(
            (
                await self.db.execute(
                    select(MonitoringSource)
                    .where(MonitoringSource.tenant_id == tenant_id, MonitoringSource.source_type.in_(types))
                    .order_by(MonitoringSource.name)
                )
            ).scalars()
        )

    async def _rpo(self, tenant_id: uuid.UUID) -> tuple[int, bool]:
        """(RPO hours, True when it is the platform default)."""
        override = await tenant_rpo_override(self.db, tenant_id)
        if override is None:
            return get_settings().backup_rpo_hours, True
        return override, False

    async def settings(self, tenant_id: uuid.UUID) -> BackupSettingsOut:
        rpo_hours, is_default = await self._rpo(tenant_id)
        return BackupSettingsOut(
            rpo_hours=rpo_hours, rpo_is_default=is_default, default_rpo_hours=get_settings().backup_rpo_hours,
        )

    async def _protectable_devices(self, tenant_id: uuid.UUID) -> list[Device]:
        """Azure VMs in inventory (the resources Azure Backup protects)."""
        return list(
            (
                await self.db.execute(
                    select(Device)
                    .where(
                        Device.tenant_id == tenant_id,
                        Device.is_deleted.is_(False),
                        func.lower(Device.external_id).contains(_PROTECTABLE),
                    )
                    .order_by(Device.name)
                )
            ).scalars()
        )

    async def _policies(self, tenant_id: uuid.UUID) -> dict[tuple[uuid.UUID, str], BackupPolicy]:
        rows = (await self.db.execute(select(BackupPolicy).where(BackupPolicy.tenant_id == tenant_id))).scalars()
        return {(p.vault_id, p.name): p for p in rows}

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
        rpo_hours, rpo_is_default = await self._rpo(tenant_id)
        sources = await self._sources(tenant_id)
        provider_of = backup_source_types()
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
        storage = [v.storage_used_bytes for v in vaults if v.storage_used_bytes is not None]
        points = [i.recovery_point_count for i in items if i.recovery_point_count is not None]
        coverage = await self.coverage(tenant_id) if any(s.source_type == SOURCE_TYPE for s in sources) else None

        return BackupOverview(
            configured=bool(sources),
            generated_at=now,
            rpo_hours=rpo_hours,
            rpo_is_default=rpo_is_default,
            sources=[
                BackupSourceSummary(
                    id=s.id, name=s.name, status=s.status, is_active=s.is_active,
                    last_sync_at=s.last_sync_at, last_error=s.last_error,
                    provider=provider_of.get(s.source_type),
                )
                for s in sources
            ],
            providers=sorted({v.provider for v in vaults}),
            vaults=len(vaults),
            vaults_offsite=sum(1 for v in vaults if v.offsite),
            vaults_immutable=sum(1 for v in vaults if v.immutable),
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
            storage_used_bytes=sum(storage) if storage else None,
            recovery_points_total=sum(points) if points else None,
            unprotected_resources=coverage.unprotected if coverage else None,
        )

    async def vaults(self, tenant_id: uuid.UUID, now: datetime | None = None) -> list[BackupVaultOut]:
        now = now or datetime.now(timezone.utc)
        rpo_hours, _ = await self._rpo(tenant_id)
        vaults = list(
            (await self.db.execute(select(BackupVault).where(BackupVault.tenant_id == tenant_id).order_by(BackupVault.name))).scalars()
        )
        items = list((await self.db.execute(select(BackupItem).where(BackupItem.tenant_id == tenant_id))).scalars())
        policies = await self._policies(tenant_id)
        out = []
        for v in vaults:
            mine = [i for i in items if i.vault_id == v.id]
            out.append(
                BackupVaultOut(
                    id=v.id, device_id=v.device_id, name=v.name, provider=v.provider, kind=v.kind,
                    offsite=v.offsite, immutable=v.immutable, location=v.location,
                    resource_group=v.resource_group, subscription_id=v.subscription_id,
                    soft_delete_state=v.soft_delete_state, immutability_state=v.immutability_state,
                    storage_redundancy=v.storage_redundancy, observed_at=v.observed_at,
                    items_total=len(mine),
                    items_breaching_rpo=sum(1 for i in mine if rpo_state(i, now, rpo_hours)[0] in ("breached", "never")),
                    storage_used_bytes=v.storage_used_bytes,
                    policies=sum(1 for vault_id, _ in policies if vault_id == v.id),
                )
            )
        return out

    async def items(
        self, tenant_id: uuid.UUID, *, vault_id: uuid.UUID | None = None, state: str | None = None,
        site_id: uuid.UUID | None = None, now: datetime | None = None,
    ) -> list[BackupItemOut]:
        now = now or datetime.now(timezone.utc)
        rpo_hours, _ = await self._rpo(tenant_id)
        stmt = (
            select(BackupItem, BackupVault.name)
            .join(BackupVault, BackupVault.id == BackupItem.vault_id)
            .where(BackupItem.tenant_id == tenant_id)
            .order_by(BackupItem.name)
        )
        if vault_id:
            stmt = stmt.where(BackupItem.vault_id == vault_id)
        policies = await self._policies(tenant_id)
        devices = {(d.external_id or "").lower(): d for d in await self._protectable_devices(tenant_id)}
        out = []
        for item, vault_name in (await self.db.execute(stmt)).all():
            verdict, age = rpo_state(item, now, rpo_hours)
            if state and verdict != state:
                continue
            row = self._item_out(item, vault_name, verdict, age, policies, devices)
            if site_id and row.site_id != site_id:
                continue
            out.append(row)
        return out

    @staticmethod
    def _item_out(item: BackupItem, vault_name: str, verdict: str, age: float | None,
                  policies: dict, devices: dict) -> BackupItemOut:
        policy = policies.get((item.vault_id, item.policy_name)) if item.policy_name else None
        device = devices.get((item.source_resource_id or "").lower())
        return BackupItemOut(
            id=item.id, vault_id=item.vault_id, vault_name=vault_name, name=item.name,
            workload_type=item.workload_type, protection_state=item.protection_state,
            last_backup_status=item.last_backup_status, last_backup_at=item.last_backup_at,
            age_hours=round(age, 1) if age is not None else None, rpo_state=verdict,
            policy_name=item.policy_name, health_status=item.health_status,
            source_resource_id=item.source_resource_id,
            recovery_point_count=item.recovery_point_count,
            oldest_recovery_point_at=item.oldest_recovery_point_at,
            size_bytes=item.size_bytes,
            retention_days=policy.daily_retention_days if policy else None,
            device_id=device.id if device else None,
            site_id=device.site_id if device else None,
        )

    async def item_detail(
        self, tenant_id: uuid.UUID, item_id: uuid.UUID, *, job_limit: int = 50, now: datetime | None = None,
    ) -> BackupItemDetail | None:
        """One item with its policy and recent jobs; None if not this tenant's."""
        now = now or datetime.now(timezone.utc)
        found = (
            await self.db.execute(
                select(BackupItem, BackupVault.name)
                .join(BackupVault, BackupVault.id == BackupItem.vault_id)
                .where(BackupItem.tenant_id == tenant_id, BackupItem.id == item_id)
            )
        ).first()
        if found is None:
            return None
        item, vault_name = found
        verdict, age = rpo_state(item, now, (await self._rpo(tenant_id))[0])
        policies = await self._policies(tenant_id)
        devices = {(d.external_id or "").lower(): d for d in await self._protectable_devices(tenant_id)}
        policy = policies.get((item.vault_id, item.policy_name)) if item.policy_name else None
        # Jobs carry the item's display name, not its id: match within the vault.
        jobs = (
            await self.db.execute(
                select(BackupJob)
                .where(
                    BackupJob.tenant_id == tenant_id,
                    BackupJob.vault_id == item.vault_id,
                    BackupJob.item_name == item.name,
                )
                .order_by(BackupJob.started_at.desc().nulls_last())
                .limit(job_limit)
            )
        ).scalars()
        return BackupItemDetail(
            item=self._item_out(item, vault_name, verdict, age, policies, devices),
            policy=_policy_out(policy) if policy else None,
            jobs=[
                BackupJobOut(
                    id=j.id, vault_id=j.vault_id, vault_name=vault_name, item_name=j.item_name,
                    operation=j.operation, status=j.status, started_at=j.started_at, ended_at=j.ended_at,
                    duration_seconds=j.duration_seconds, error_code=j.error_code, error_message=j.error_message,
                )
                for j in jobs
            ],
        )

    async def coverage(self, tenant_id: uuid.UUID) -> BackupCoverage:
        """Every Azure VM in inventory against the backup items that protect it.

        A VM is only called unprotected when its subscription has an active
        backup source (an Azure vault backs up VMs in its own subscription);
        otherwise it is not monitored - we cannot see its backups."""
        sources = [s for s in await self._sources(tenant_id, "azure") if s.is_active and not s.is_deleted]
        monitored = sorted(
            {str((s.config or {}).get("subscription_id") or "").lower() for s in sources} - {""}
        )
        items = (
            await self.db.execute(
                select(BackupItem.source_resource_id, BackupItem.protection_state).where(BackupItem.tenant_id == tenant_id)
            )
        ).all()
        protection: dict[str, str] = {}
        for resource_id, state in items:
            key = (resource_id or "").lower()
            if not key:
                continue
            # An active item wins over a stopped one for the same resource.
            if state in _ACTIVE or key not in protection:
                protection[key] = "protected" if state in _ACTIVE else "protection_stopped"
        resources = []
        for device in await self._protectable_devices(tenant_id):
            resource_id = device.external_id or ""
            sub = _subscription(resource_id)
            if resource_id.lower() in protection:
                state = protection[resource_id.lower()]
            elif sub in monitored:
                state = "unprotected"
            else:
                state = "not_monitored"
            resources.append(
                BackupCoverageResource(
                    device_id=device.id, name=device.name, resource_id=resource_id,
                    subscription_id=sub, site_id=device.site_id, state=state,
                )
            )
        count = lambda s: sum(1 for r in resources if r.state == s)  # noqa: E731
        return BackupCoverage(
            configured=bool(sources),
            monitored_subscriptions=monitored,
            total=len(resources),
            protected=count("protected"),
            protection_stopped=count("protection_stopped"),
            unprotected=count("unprotected"),
            not_monitored=count("not_monitored"),
            resources=resources,
        )

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


__all__ = ["BackupService", "backup_source_types", "readiness_from_overview", "rpo_state", "SOURCE_TYPE"]
