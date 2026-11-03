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

Backup Command Center (BR-1): the overview, items, storage locations,
failure analysis, security posture and storage history all take the same
optional filters (``BackupFilters``): provider, subscription, resource group,
region and storage location narrow the storage locations; workload type
narrows items, and jobs through the items they belong to.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.backup_vocabulary import ACTIVE_PROTECTION
from app.collectors.base import CollectorRegistry
from app.core.config import get_settings
from app.models.backup import BackupItem, BackupJob, BackupPolicy, BackupStorageSnapshot, BackupVault
from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.schemas.backup import (
    BackupCoverage,
    BackupCoverageResource,
    BackupAgeBucket,
    BackupDailyJobs,
    BackupFailureAnalysis,
    BackupFailureGroup,
    BackupFilterOption,
    BackupFilterOptions,
    BackupItemDetail,
    BackupJobDetail,
    BackupItemOut,
    BackupJobCounts,
    BackupJobOut,
    BackupOverview,
    BackupPolicyOut,
    BackupPolicyRow,
    BackupSecurityCheck,
    BackupSecurityCheckSummary,
    BackupSecurityLocation,
    BackupSecurityPosture,
    BackupSettingsOut,
    BackupSourceSummary,
    BackupStatusGroup,
    BackupStorageHistory,
    BackupStoragePoint,
    BackupVaultOut,
    BackupWorkloadSummary,
)
from app.services.backup_settings import tenant_rpo_override

#: The Azure Backup collector's source type (coverage reads its subscriptions).
SOURCE_TYPE = "azure_backup"
_ACTIVE = ACTIVE_PROTECTION
_SUCCESS = ("completed", "completed_with_warnings")
_FINISHED = ("completed", "completed_with_warnings", "failed", "cancelled")
DAILY_DAYS = 14
#: Longest time range the overview takes (job history is kept 90 days).
MAX_RANGE_DAYS = 90
#: Recovery-point age buckets: (key, label, upper bound in hours; None = no bound).
AGE_BUCKETS = (
    ("lt_24h", "Under 24 h", 24),
    ("1_3d", "1–3 days", 72),
    ("3_7d", "3–7 days", 168),
    ("7_30d", "7–30 days", 720),
    ("gt_30d", "Over 30 days", None),
)
#: Inventory resources Azure Backup can protect, by resource id (lower case).
_PROTECTABLE = "/providers/microsoft.compute/virtualmachines/"
#: Days of storage history a growth estimate needs.
MIN_GROWTH_HISTORY_DAYS = 14
#: Item names listed per failure group.
_FAILURE_ITEMS = 10


@dataclass(frozen=True)
class BackupFilters:
    """Optional Command Center filters; None = not filtered."""

    provider: str | None = None
    subscription_id: str | None = None
    resource_group: str | None = None
    location: str | None = None
    vault_id: uuid.UUID | None = None
    workload_type: str | None = None

    @property
    def active(self) -> bool:
        return any(v is not None for v in (
            self.provider, self.subscription_id, self.resource_group, self.location, self.vault_id, self.workload_type,
        ))

    def vault_clauses(self) -> list:
        """WHERE clauses on BackupVault (case-insensitive for Azure names)."""
        out = []
        if self.provider:
            out.append(BackupVault.provider == self.provider)
        if self.subscription_id:
            out.append(func.lower(BackupVault.subscription_id) == self.subscription_id.lower())
        if self.resource_group:
            out.append(func.lower(BackupVault.resource_group) == self.resource_group.lower())
        if self.location:
            out.append(func.lower(BackupVault.location) == self.location.lower())
        if self.vault_id:
            out.append(BackupVault.id == self.vault_id)
        return out


NO_FILTERS = BackupFilters()


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

    async def _scoped_vaults(self, tenant_id: uuid.UUID, filters: BackupFilters) -> list[BackupVault]:
        return list(
            (
                await self.db.execute(
                    select(BackupVault)
                    .where(BackupVault.tenant_id == tenant_id, *filters.vault_clauses())
                    .order_by(BackupVault.name)
                )
            ).scalars()
        )

    async def _scoped_items(self, tenant_id: uuid.UUID, filters: BackupFilters, vault_ids: list[uuid.UUID]) -> list[BackupItem]:
        stmt = select(BackupItem).where(BackupItem.tenant_id == tenant_id, BackupItem.vault_id.in_(vault_ids))
        if filters.workload_type:
            stmt = stmt.where(BackupItem.workload_type == filters.workload_type)
        return list((await self.db.execute(stmt)).scalars())

    @staticmethod
    def _job_clauses(filters: BackupFilters, vault_ids: list[uuid.UUID] | None) -> list:
        """WHERE clauses on BackupJob: its vault in scope and, with a workload
        filter, an item of that workload with the job's name in the same vault."""
        out = []
        if vault_ids is not None:
            out.append(BackupJob.vault_id.in_(vault_ids))
        if filters.workload_type:
            out.append(
                exists().where(and_(
                    BackupItem.vault_id == BackupJob.vault_id,
                    BackupItem.name == BackupJob.item_name,
                    BackupItem.workload_type == filters.workload_type,
                ))
            )
        return out

    async def filter_options(self, tenant_id: uuid.UUID) -> BackupFilterOptions:
        vaults = await self._scoped_vaults(tenant_id, NO_FILTERS)
        workloads = (
            await self.db.execute(
                select(BackupItem.workload_type)
                .where(BackupItem.tenant_id == tenant_id, BackupItem.workload_type.is_not(None))
                .distinct()
            )
        ).scalars()
        distinct = lambda attr: sorted({getattr(v, attr) for v in vaults if getattr(v, attr)})  # noqa: E731
        return BackupFilterOptions(
            providers=distinct("provider"),
            subscriptions=distinct("subscription_id"),
            resource_groups=distinct("resource_group"),
            locations=distinct("location"),
            workload_types=sorted(workloads),
            vaults=[BackupFilterOption(value=str(v.id), label=v.name) for v in vaults],
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

    async def _job_counts(self, tenant_id: uuid.UUID, since: datetime, scope: list | None = None) -> BackupJobCounts:
        rows = (
            await self.db.execute(
                select(BackupJob.status, func.count())
                .where(BackupJob.tenant_id == tenant_id, BackupJob.started_at >= since, *(scope or []))
                .group_by(BackupJob.status)
            )
        ).all()
        return _counts(rows)

    async def overview(
        self, tenant_id: uuid.UUID, now: datetime | None = None, filters: BackupFilters = NO_FILTERS,
        range_days: int = DAILY_DAYS,
    ) -> BackupOverview:
        now = now or datetime.now(timezone.utc)
        rpo_hours, rpo_is_default = await self._rpo(tenant_id)
        sources = await self._sources(tenant_id)
        provider_of = backup_source_types()
        vaults = await self._scoped_vaults(tenant_id, filters)
        vault_ids = [v.id for v in vaults]
        items = await self._scoped_items(tenant_id, filters, vault_ids)
        scope = self._job_clauses(filters, vault_ids if filters.active else None)

        states = [rpo_state(i, now, rpo_hours)[0] for i in items]
        jobs_24h = await self._job_counts(tenant_id, now - timedelta(hours=24), scope)
        finished_24h = jobs_24h.completed + jobs_24h.completed_with_warnings + jobs_24h.failed
        success_24h = (
            round((jobs_24h.completed + jobs_24h.completed_with_warnings) / finished_24h * 100, 1) if finished_24h else None
        )

        range_days = max(1, min(range_days, MAX_RANGE_DAYS))
        start_day = (now - timedelta(days=range_days - 1)).date()
        day_col = func.date_trunc("day", BackupJob.started_at)
        daily_rows = (
            await self.db.execute(
                select(day_col, BackupJob.status, func.count())
                .where(
                    BackupJob.tenant_id == tenant_id,
                    BackupJob.started_at >= datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc),
                    *scope,
                )
                .group_by(day_col, BackupJob.status)
            )
        ).all()
        days = {start_day + timedelta(days=i): BackupDailyJobs(day=start_day + timedelta(days=i)) for i in range(range_days)}
        for day, status, n in daily_rows:
            bucket = days.get(day.date() if isinstance(day, datetime) else day)
            if bucket is None:
                continue
            key = status if status in ("completed", "completed_with_warnings", "failed") else "other"
            setattr(bucket, key, getattr(bucket, key) + n)

        last_job_at = (
            await self.db.execute(select(func.max(BackupJob.started_at)).where(BackupJob.tenant_id == tenant_id, *scope))
        ).scalar()
        storage = [v.storage_used_bytes for v in vaults if v.storage_used_bytes is not None]
        points = [i.recovery_point_count for i in items if i.recovery_point_count is not None]
        # Coverage matches inventory VMs, not backup items: it cannot be
        # narrowed by storage location or workload, so it is left out then.
        coverage = (
            await self.coverage(tenant_id)
            if any(s.source_type == SOURCE_TYPE for s in sources) and not filters.active
            else None
        )

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
            jobs_7d=await self._job_counts(tenant_id, now - timedelta(days=7), scope),
            daily=[days[d] for d in sorted(days)],
            last_job_at=last_job_at,
            observed_at=max((v.observed_at for v in vaults if v.observed_at), default=None),
            storage_used_bytes=sum(storage) if storage else None,
            recovery_points_total=sum(points) if points else None,
            unprotected_resources=coverage.unprotected if coverage else None,
            range_days=range_days,
            jobs_range=await self._job_counts(tenant_id, now - timedelta(days=range_days), scope),
            workloads=_workloads(items, now, rpo_hours),
            by_subscription=_status_groups(items, {v.id: v.subscription_id for v in vaults}, now, rpo_hours),
            by_region=_status_groups(items, {v.id: v.location for v in vaults}, now, rpo_hours),
            recovery_point_age=_age_buckets(items, now),
            filtered=filters.active,
            filter_options=await self.filter_options(tenant_id),
        )

    async def vaults(
        self, tenant_id: uuid.UUID, now: datetime | None = None, filters: BackupFilters = NO_FILTERS,
    ) -> list[BackupVaultOut]:
        now = now or datetime.now(timezone.utc)
        rpo_hours, _ = await self._rpo(tenant_id)
        vaults = await self._scoped_vaults(tenant_id, filters)
        items = await self._scoped_items(tenant_id, filters, [v.id for v in vaults])
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
                    capacity_bytes=v.capacity_bytes,
                    free_bytes=v.free_bytes,
                    multi_user_authorization=v.multi_user_authorization,
                    encryption=v.encryption,
                    cross_region_restore=v.cross_region_restore,
                    public_network_access=v.public_network_access,
                    policies=sum(1 for vault_id, _ in policies if vault_id == v.id),
                )
            )
        return out

    async def items(
        self, tenant_id: uuid.UUID, *, vault_id: uuid.UUID | None = None, state: str | None = None,
        site_id: uuid.UUID | None = None, now: datetime | None = None, filters: BackupFilters = NO_FILTERS,
        q: str | None = None, protection_state: str | None = None, last_backup_status: str | None = None,
        policy_name: str | None = None,
    ) -> list[BackupItemOut]:
        now = now or datetime.now(timezone.utc)
        rpo_hours, _ = await self._rpo(tenant_id)
        stmt = (
            select(BackupItem, BackupVault.name)
            .join(BackupVault, BackupVault.id == BackupItem.vault_id)
            .where(BackupItem.tenant_id == tenant_id, *filters.vault_clauses())
            .order_by(BackupItem.name)
        )
        if vault_id:
            stmt = stmt.where(BackupItem.vault_id == vault_id)
        if filters.workload_type:
            stmt = stmt.where(BackupItem.workload_type == filters.workload_type)
        if q:
            like = f"%{_escape_like(q.lower())}%"
            stmt = stmt.where(
                func.lower(BackupItem.name).like(like, escape="\\")
                | func.lower(func.coalesce(BackupItem.source_resource_id, "")).like(like, escape="\\")
            )
        if protection_state:
            stmt = stmt.where(BackupItem.protection_state == protection_state)
        if last_backup_status:
            stmt = stmt.where(BackupItem.last_backup_status == last_backup_status)
        if policy_name:
            stmt = stmt.where(BackupItem.policy_name == policy_name)
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
        now: datetime | None = None, filters: BackupFilters = NO_FILTERS,
        operation: str | None = None, q: str | None = None,
    ) -> list[BackupJobOut]:
        now = now or datetime.now(timezone.utc)
        stmt = (
            select(BackupJob, BackupVault.name)
            .join(BackupVault, BackupVault.id == BackupJob.vault_id)
            .where(
                BackupJob.tenant_id == tenant_id, BackupJob.started_at >= now - timedelta(days=days),
                *filters.vault_clauses(), *self._job_clauses(filters, None),
            )
            .order_by(BackupJob.started_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(BackupJob.status == status)
        if operation:
            stmt = stmt.where(func.lower(BackupJob.operation) == operation.lower())
        if q:
            like = f"%{_escape_like(q.lower())}%"
            stmt = stmt.where(
                func.lower(func.coalesce(BackupJob.item_name, "")).like(like, escape="\\")
                | func.lower(func.coalesce(BackupJob.error_code, "")).like(like, escape="\\")
                | func.lower(func.coalesce(BackupJob.error_message, "")).like(like, escape="\\")
            )
        return [_job_out(j, vault_name) for j, vault_name in (await self.db.execute(stmt)).all()]

    async def job_detail(self, tenant_id: uuid.UUID, job_id: uuid.UUID, *, item_jobs: int = 10) -> BackupJobDetail | None:
        """One job; None if not this tenant's."""
        found = (
            await self.db.execute(
                select(BackupJob, BackupVault)
                .join(BackupVault, BackupVault.id == BackupJob.vault_id)
                .where(BackupJob.tenant_id == tenant_id, BackupJob.id == job_id)
            )
        ).first()
        if found is None:
            return None
        job, vault = found
        item_id = None
        others: list[BackupJobOut] = []
        if job.item_name:
            item_id = (
                await self.db.execute(
                    select(BackupItem.id).where(BackupItem.vault_id == vault.id, BackupItem.name == job.item_name).limit(1)
                )
            ).scalar()
            rows = (
                await self.db.execute(
                    select(BackupJob)
                    .where(
                        BackupJob.tenant_id == tenant_id, BackupJob.vault_id == vault.id,
                        BackupJob.item_name == job.item_name, BackupJob.id != job.id,
                    )
                    .order_by(BackupJob.started_at.desc().nulls_last())
                    .limit(item_jobs)
                )
            ).scalars()
            others = [_job_out(j, vault.name) for j in rows]
        return BackupJobDetail(job=_job_out(job, vault.name), provider=vault.provider, item_id=item_id, item_jobs=others)

    async def vault(self, tenant_id: uuid.UUID, vault_id: uuid.UUID) -> BackupVaultOut | None:
        """One storage location; None if not this tenant's."""
        rows = await self.vaults(tenant_id, filters=BackupFilters(vault_id=vault_id))
        return rows[0] if rows else None

    async def policies(self, tenant_id: uuid.UUID, filters: BackupFilters = NO_FILTERS) -> list[BackupPolicyRow]:
        """Every policy in the selected storage, with the items stored under it."""
        vaults = {v.id: v for v in await self._scoped_vaults(tenant_id, filters)}
        if not vaults:
            return []
        stmt = select(BackupPolicy).where(BackupPolicy.tenant_id == tenant_id, BackupPolicy.vault_id.in_(list(vaults)))
        if filters.workload_type:
            stmt = stmt.where(BackupPolicy.workload_type == filters.workload_type)
        assigned = dict(
            ((vault_id, name), n)
            for vault_id, name, n in (
                await self.db.execute(
                    select(BackupItem.vault_id, BackupItem.policy_name, func.count())
                    .where(BackupItem.tenant_id == tenant_id, BackupItem.vault_id.in_(list(vaults)))
                    .group_by(BackupItem.vault_id, BackupItem.policy_name)
                )
            ).all()
        )
        out = []
        for p in (await self.db.execute(stmt.order_by(BackupPolicy.name))).scalars():
            v = vaults[p.vault_id]
            out.append(
                BackupPolicyRow(
                    **_policy_out(p).model_dump(), id=p.id, vault_id=v.id, vault_name=v.name, provider=v.provider,
                    items_assigned=assigned.get((p.vault_id, p.name), 0), observed_at=p.observed_at,
                )
            )
        return out

    async def failures(
        self, tenant_id: uuid.UUID, *, days: int = 7, now: datetime | None = None, filters: BackupFilters = NO_FILTERS,
    ) -> BackupFailureAnalysis:
        """Failed jobs in the window, grouped by the provider's error code."""
        now = now or datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(BackupJob, BackupVault.name)
                .join(BackupVault, BackupVault.id == BackupJob.vault_id)
                .where(
                    BackupJob.tenant_id == tenant_id,
                    BackupJob.status == "failed",
                    BackupJob.started_at >= now - timedelta(days=days),
                    *filters.vault_clauses(), *self._job_clauses(filters, None),
                )
                .order_by(BackupJob.started_at.desc())
            )
        ).all()
        groups: dict[str | None, list] = {}
        for job, vault_name in rows:
            groups.setdefault(job.error_code, []).append((job, vault_name))
        out = []
        for code, jobs in groups.items():
            items = list(dict.fromkeys(j.item_name for j, _ in jobs if j.item_name))
            starts = [j.started_at for j, _ in jobs if j.started_at]
            out.append(
                BackupFailureGroup(
                    error_code=code,
                    failed_jobs=len(jobs),
                    items_affected=len(items),
                    items=items[:_FAILURE_ITEMS],
                    vaults=sorted({name for _, name in jobs}),
                    operations=sorted({j.operation for j, _ in jobs if j.operation}),
                    first_seen=min(starts, default=None),
                    last_seen=max(starts, default=None),
                    sample_message=jobs[0][0].error_message,
                )
            )
        out.sort(key=lambda g: (-g.failed_jobs, g.error_code or "~"))
        return BackupFailureAnalysis(
            days=days,
            failed_jobs=len(rows),
            items_affected=len({(j.vault_id, j.item_name) for j, _ in rows if j.item_name}),
            groups=out,
        )

    async def security(self, tenant_id: uuid.UUID, filters: BackupFilters = NO_FILTERS) -> BackupSecurityPosture:
        """Every storage location's security settings, checked one by one.
        A setting the provider does not report is ``not_reported``, never a pass."""
        locations = []
        summary: dict[str, BackupSecurityCheckSummary] = {}
        for v in await self._scoped_vaults(tenant_id, filters):
            checks = security_checks(v)
            for c in checks:
                s = summary.setdefault(c.key, BackupSecurityCheckSummary(key=c.key, label=c.label))
                field_name = {"pass": "passed", "warn": "warned", "fail": "failed"}.get(c.status, c.status)
                setattr(s, field_name, getattr(s, field_name) + 1)
            locations.append(
                BackupSecurityLocation(
                    vault_id=v.id, name=v.name, provider=v.provider, kind=v.kind, location=v.location, checks=checks,
                    passed=sum(1 for c in checks if c.status == "pass"),
                    evaluated=sum(1 for c in checks if c.status in ("pass", "warn", "fail")),
                )
            )
        evaluated = sum(loc.evaluated for loc in locations)
        return BackupSecurityPosture(
            locations=locations,
            summary=list(summary.values()),
            score=round(sum(loc.passed for loc in locations) / evaluated * 100, 1) if evaluated else None,
        )

    async def storage_history(
        self, tenant_id: uuid.UUID, *, days: int = 30, now: datetime | None = None, filters: BackupFilters = NO_FILTERS,
    ) -> BackupStorageHistory:
        """Daily storage across the selected locations, and a growth estimate
        once ``MIN_GROWTH_HISTORY_DAYS`` days are recorded."""
        now = now or datetime.now(timezone.utc)
        start = (now - timedelta(days=days - 1)).date()
        vault_ids = [v.id for v in await self._scoped_vaults(tenant_id, filters)]
        rows = (
            await self.db.execute(
                select(
                    BackupStorageSnapshot.day,
                    func.sum(BackupStorageSnapshot.storage_used_bytes),
                    func.sum(BackupStorageSnapshot.capacity_bytes),
                    func.sum(BackupStorageSnapshot.free_bytes),
                    func.count(),
                )
                .where(
                    BackupStorageSnapshot.tenant_id == tenant_id,
                    BackupStorageSnapshot.vault_id.in_(vault_ids),
                    BackupStorageSnapshot.day >= start,
                )
                .group_by(BackupStorageSnapshot.day)
                .order_by(BackupStorageSnapshot.day)
            )
        ).all()
        points = [
            BackupStoragePoint(
                day=day, storage_used_bytes=_int(used), capacity_bytes=_int(cap), free_bytes=_int(free),
                locations_reporting=n,
            )
            for day, used, cap, free, n in rows
        ]
        measured = [(p.day, p.storage_used_bytes) for p in points if p.storage_used_bytes is not None]
        growth = _growth_per_day(measured) if len(measured) >= MIN_GROWTH_HISTORY_DAYS else None
        return BackupStorageHistory(
            days=days,
            points=points,
            history_days=len(measured),
            min_history_days=MIN_GROWTH_HISTORY_DAYS,
            growth_bytes_per_day=round(growth, 1) if growth is not None else None,
            projected_used_bytes_30d=(
                max(0, int(measured[-1][1] + growth * 30)) if growth is not None else None
            ),
        )


def _escape_like(text: str) -> str:
    """Make LIKE wildcards in user text literal (escape character: backslash)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _job_out(j: BackupJob, vault_name: str) -> BackupJobOut:
    return BackupJobOut(
        id=j.id, vault_id=j.vault_id, vault_name=vault_name, item_name=j.item_name, operation=j.operation,
        status=j.status, started_at=j.started_at, ended_at=j.ended_at, duration_seconds=j.duration_seconds,
        error_code=j.error_code, error_message=j.error_message,
    )


def _int(value) -> int | None:
    return int(value) if value is not None else None


def _growth_per_day(points: list[tuple[date, int]]) -> float | None:
    """Least-squares slope of (day, bytes); None when the days do not vary."""
    x0 = points[0][0]
    xs = [(d - x0).days for d, _ in points]
    ys = [float(v) for _, v in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mean_x) ** 2 for x in xs)
    if not var:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var


def _status_groups(items: list[BackupItem], key_of_vault: dict, now: datetime, rpo_hours: int) -> list[BackupStatusGroup]:
    """Items grouped by a property of their storage (subscription, region)."""
    groups: dict[str | None, list[BackupItem]] = {}
    for item in items:
        groups.setdefault(key_of_vault.get(item.vault_id), []).append(item)
    out = []
    for key, mine in groups.items():
        states = [rpo_state(i, now, rpo_hours)[0] for i in mine]
        out.append(
            BackupStatusGroup(
                key=key,
                items_total=len(mine),
                items_protected=sum(1 for i in mine if i.protection_state in _ACTIVE),
                items_within_rpo=states.count("within"),
                items_breaching_rpo=states.count("breached"),
                items_never_backed_up=states.count("never"),
                items_not_protected=states.count("not_protected"),
                items_last_failed=sum(1 for i in mine if i.last_backup_status == "failed"),
            )
        )
    out.sort(key=lambda g: (-g.items_total, g.key or "~"))
    return out


def _age_buckets(items: list[BackupItem], now: datetime) -> list[BackupAgeBucket]:
    """Protected items by the age of their newest backup (restore point)."""
    counts = {key: 0 for key, _, _ in AGE_BUCKETS}
    never = 0
    for item in items:
        if item.protection_state not in _ACTIVE:
            continue
        if item.last_backup_at is None:
            never += 1
            continue
        age = (now - item.last_backup_at).total_seconds() / 3600
        for key, _, bound in AGE_BUCKETS:
            if bound is None or age < bound:
                counts[key] += 1
                break
    return [BackupAgeBucket(key=key, label=label, items=counts[key]) for key, label, _ in AGE_BUCKETS] + [
        BackupAgeBucket(key="never", label="Never backed up", items=never)
    ]


def _workloads(items: list[BackupItem], now: datetime, rpo_hours: int) -> list[BackupWorkloadSummary]:
    """Items grouped by workload type, largest group first."""
    groups: dict[str | None, list[BackupItem]] = {}
    for item in items:
        groups.setdefault(item.workload_type, []).append(item)
    out = []
    for workload, mine in groups.items():
        states = [rpo_state(i, now, rpo_hours)[0] for i in mine]
        sizes = [i.size_bytes for i in mine if i.size_bytes is not None]
        points = [i.recovery_point_count for i in mine if i.recovery_point_count is not None]
        out.append(
            BackupWorkloadSummary(
                workload_type=workload,
                items_total=len(mine),
                items_protected=sum(1 for i in mine if i.protection_state in _ACTIVE),
                items_within_rpo=states.count("within"),
                items_breaching_rpo=states.count("breached"),
                items_never_backed_up=states.count("never"),
                items_not_protected=states.count("not_protected"),
                items_last_failed=sum(1 for i in mine if i.last_backup_status == "failed"),
                size_bytes=sum(sizes) if sizes else None,
                recovery_points_total=sum(points) if points else None,
            )
        )
    out.sort(key=lambda w: (-w.items_total, w.workload_type or "~"))
    return out


def _check(key: str, label: str, status: str, value: str | None) -> BackupSecurityCheck:
    return BackupSecurityCheck(key=key, label=label, status=status, value=value)


def security_checks(v: BackupVault) -> list[BackupSecurityCheck]:
    """The security checks of one storage location. Pure; nothing inferred."""
    soft = v.soft_delete_state
    soft_status = "not_reported" if soft is None else "pass" if soft in ("enabled", "always_on") else "fail"

    if v.immutable:
        imm_status, imm_value = "pass", "Locked" if v.immutability_state == "locked" else "Immutable"
    elif v.immutability_state == "unlocked":
        imm_status, imm_value = "warn", "Enabled, not locked (can be switched off)"
    elif v.immutability_state == "disabled" or v.immutable is False:
        imm_status, imm_value = "fail", "Disabled"
    else:
        imm_status, imm_value = "not_reported", None

    offsite_status = "not_reported" if v.offsite is None else "pass" if v.offsite else "warn"
    offsite_value = None if v.offsite is None else (
        f"Yes ({_words(v.storage_redundancy)})" if v.offsite and v.storage_redundancy else "Yes" if v.offsite
        else f"No ({_words(v.storage_redundancy)})" if v.storage_redundancy else "No"
    )

    mua = v.multi_user_authorization
    mua_status = "not_reported" if mua is None else "pass" if mua == "enabled" else "warn"

    # Cross-region restore exists only for geo-redundant storage.
    if v.offsite is False and v.provider == "azure":
        crr_status, crr_value = "not_applicable", "Single-region storage"
    elif v.cross_region_restore is None:
        crr_status, crr_value = "not_reported", None
    else:
        crr_status, crr_value = ("pass", "Enabled") if v.cross_region_restore else ("warn", "Disabled")

    enc = v.encryption
    enc_value = {"customer_managed": "Customer-managed key", "platform_managed": "Platform-managed key"}.get(enc or "")
    pna = v.public_network_access
    pna_status = "not_reported" if pna is None else "pass" if pna == "disabled" else "warn"

    return [
        _check("soft_delete", "Soft delete", soft_status, _words(soft)),
        _check("immutability", "Immutability", imm_status, imm_value),
        _check("offsite", "Off-site copy", offsite_status, offsite_value),
        _check("multi_user_authorization", "Multi-user authorization", mua_status, _words(mua)),
        _check("cross_region_restore", "Cross-region restore", crr_status, crr_value),
        # Encrypted either way; the key type is reported, not judged.
        _check("encryption", "Encryption at rest", "pass" if enc else "not_reported", enc_value),
        _check("public_network_access", "Public network access", pna_status, _words(pna)),
    ]


def _words(value: str | None) -> str | None:
    """'geo_redundant' -> 'Geo redundant'."""
    return value.replace("_", " ").capitalize() if value else None


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


__all__ = [
    "BackupFilters",
    "BackupService",
    "MIN_GROWTH_HISTORY_DAYS",
    "backup_source_types",
    "readiness_from_overview",
    "rpo_state",
    "security_checks",
    "SOURCE_TYPE",
]
