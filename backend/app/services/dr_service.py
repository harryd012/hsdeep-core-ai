"""Disaster-recovery read model (master brief P3; docs/engineering/azure-backup-monitoring.md).

Tenant-scoped reads over `backup_replicated_items` and `backup_recovery_plans`
(Azure Site Recovery, collected by the Azure Backup collector): the DR
overview, replicated items and recovery plans, each with a DR-readiness
verdict computed in one place (`readiness`).

DR-readiness rule (platform rule, not an Azure verdict): a replicated item is
*ready* when Site Recovery reports its replication health as normal, its
failover health is not warning/critical, it is running in its primary
location, and it passed a test failover within `dr_test_max_age_days`. An
item running in the recovery location is *failed_over*. A recovery plan is
ready when every member item is ready and the plan itself was test-failed-over
within the same window. Nothing here starts a failover: this is read-only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.backup import BackupRecoveryPlan, BackupReplicatedItem, BackupVault
from app.models.device import Device
from app.schemas.backup import (
    RecoveryPlanOut,
    ReplicatedItemOut,
    ReplicationOverview,
    ReplicationRegionGroup,
)
from app.services.backup_service import NO_FILTERS, BackupFilters, backup_source_types


def readiness(item: BackupReplicatedItem, now: datetime, max_age_days: int) -> tuple[str, list[str]]:
    """(ready | not_ready | failed_over, reasons). Pure."""
    if item.active_location == "recovery":
        return "failed_over", ["Running in the recovery location (failed over)"]
    reasons: list[str] = []
    if item.replication_health is None:
        reasons.append("Replication health not reported")
    elif item.replication_health != "normal":
        reasons.append(f"Replication health is {item.replication_health}")
    if item.failover_health in ("warning", "critical"):
        reasons.append(f"Failover health is {item.failover_health}")
    if item.last_test_failover_at is None:
        reasons.append("No successful test failover reported")
    else:
        age = (now - item.last_test_failover_at).days
        if age > max_age_days:
            reasons.append(f"Last successful test failover {age} days ago (target {max_age_days})")
    return ("not_ready" if reasons else "ready"), reasons


class DisasterRecoveryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _vaults(self, tenant_id: uuid.UUID, filters: BackupFilters) -> dict[uuid.UUID, BackupVault]:
        rows = (
            await self.db.execute(select(BackupVault).where(BackupVault.tenant_id == tenant_id, *filters.vault_clauses()))
        ).scalars()
        return {v.id: v for v in rows}

    async def _items(self, tenant_id: uuid.UUID, vault_ids: list[uuid.UUID]) -> list[BackupReplicatedItem]:
        return list(
            (
                await self.db.execute(
                    select(BackupReplicatedItem)
                    .where(BackupReplicatedItem.tenant_id == tenant_id, BackupReplicatedItem.vault_id.in_(vault_ids))
                    .order_by(BackupReplicatedItem.name)
                )
            ).scalars()
        )

    async def _plans(self, tenant_id: uuid.UUID, vault_ids: list[uuid.UUID]) -> list[BackupRecoveryPlan]:
        return list(
            (
                await self.db.execute(
                    select(BackupRecoveryPlan)
                    .where(BackupRecoveryPlan.tenant_id == tenant_id, BackupRecoveryPlan.vault_id.in_(vault_ids))
                    .order_by(BackupRecoveryPlan.name)
                )
            ).scalars()
        )

    async def items(
        self, tenant_id: uuid.UUID, *, filters: BackupFilters = NO_FILTERS, now: datetime | None = None,
    ) -> list[ReplicatedItemOut]:
        now = now or datetime.now(timezone.utc)
        max_age = get_settings().dr_test_max_age_days
        vaults = await self._vaults(tenant_id, filters)
        items = await self._items(tenant_id, list(vaults))
        plans = await self._plans(tenant_id, list(vaults))
        plan_names: dict[str, list[str]] = {}
        for plan in plans:
            for ext in plan.item_external_ids or []:
                plan_names.setdefault(ext, []).append(plan.name)
        sources = {(i.source_resource_id or "").lower() for i in items if i.source_resource_id}
        devices = {}
        if sources:
            devices = {
                (d.external_id or "").lower(): d.id
                for d in (
                    await self.db.execute(
                        select(Device).where(
                            Device.tenant_id == tenant_id, Device.is_deleted.is_(False),
                            func.lower(Device.external_id).in_(sources),
                        )
                    )
                ).scalars()
            }
        out = []
        for i in items:
            verdict, reasons = readiness(i, now, max_age)
            out.append(
                ReplicatedItemOut(
                    id=i.id, vault_id=i.vault_id, vault_name=vaults[i.vault_id].name, name=i.name,
                    source_resource_id=i.source_resource_id, replication_type=i.replication_type,
                    protected_item_type=i.protected_item_type, source_location=i.source_location,
                    target_location=i.target_location, protection_state=i.protection_state,
                    protection_state_description=i.protection_state_description,
                    replication_health=i.replication_health, failover_health=i.failover_health,
                    active_location=i.active_location, test_failover_state=i.test_failover_state,
                    last_failover_at=i.last_failover_at, last_test_failover_at=i.last_test_failover_at,
                    rpo_seconds=i.rpo_seconds, rpo_calculated_at=i.rpo_calculated_at, policy_name=i.policy_name,
                    recovery_vm_name=i.recovery_vm_name, current_scenario=i.current_scenario,
                    allowed_operations=list(i.allowed_operations or []), health_errors=list(i.health_errors or []),
                    observed_at=i.observed_at, readiness=verdict, readiness_reasons=reasons,
                    recovery_plans=sorted(plan_names.get(i.external_id, [])),
                    device_id=devices.get((i.source_resource_id or "").lower()),
                )
            )
        return out

    async def item(self, tenant_id: uuid.UUID, item_id: uuid.UUID) -> ReplicatedItemOut | None:
        vault_id = (
            await self.db.execute(
                select(BackupReplicatedItem.vault_id).where(
                    BackupReplicatedItem.tenant_id == tenant_id, BackupReplicatedItem.id == item_id
                )
            )
        ).scalar()
        if vault_id is None:
            return None
        rows = await self.items(tenant_id, filters=BackupFilters(vault_id=vault_id))
        return next((r for r in rows if r.id == item_id), None)

    async def plans(
        self, tenant_id: uuid.UUID, *, filters: BackupFilters = NO_FILTERS, now: datetime | None = None,
    ) -> list[RecoveryPlanOut]:
        now = now or datetime.now(timezone.utc)
        max_age = get_settings().dr_test_max_age_days
        vaults = await self._vaults(tenant_id, filters)
        items = {i.external_id: i for i in await self._items(tenant_id, list(vaults))}
        out = []
        for p in await self._plans(tenant_id, list(vaults)):
            members = [items[e] for e in (p.item_external_ids or []) if e in items]
            ready = sum(1 for m in members if readiness(m, now, max_age)[0] == "ready")
            reasons: list[str] = []
            missing = len(p.item_external_ids or []) - len(members)
            if missing:
                reasons.append(f"{missing} member item(s) not found among the replicated items")
            if ready < len(members):
                reasons.append(f"{len(members) - ready} of {len(members)} member item(s) not DR-ready")
            if p.last_test_failover_at is None:
                reasons.append("No test failover of the plan reported")
            elif (now - p.last_test_failover_at).days > max_age:
                reasons.append(f"Last test failover {(now - p.last_test_failover_at).days} days ago (target {max_age})")
            if not p.item_external_ids:
                reasons.append("The plan has no items")
            out.append(
                RecoveryPlanOut(
                    id=p.id, vault_id=p.vault_id, vault_name=vaults[p.vault_id].name, name=p.name,
                    primary_location=p.primary_location, recovery_location=p.recovery_location,
                    failover_deployment_model=p.failover_deployment_model,
                    replication_providers=list(p.replication_providers or []),
                    allowed_operations=list(p.allowed_operations or []), current_scenario=p.current_scenario,
                    current_scenario_status=p.current_scenario_status,
                    last_planned_failover_at=p.last_planned_failover_at,
                    last_unplanned_failover_at=p.last_unplanned_failover_at,
                    last_test_failover_at=p.last_test_failover_at, groups=p.groups,
                    items_total=len(p.item_external_ids or []), items_found=len(members), items_ready=ready,
                    readiness="not_ready" if reasons else "ready", readiness_reasons=reasons,
                    observed_at=p.observed_at,
                )
            )
        return out

    async def overview(
        self, tenant_id: uuid.UUID, *, filters: BackupFilters = NO_FILTERS, now: datetime | None = None,
    ) -> ReplicationOverview:
        now = now or datetime.now(timezone.utc)
        max_age = get_settings().dr_test_max_age_days
        azure_types = list(backup_source_types("azure"))
        from app.models.monitoring_source import MonitoringSource

        configured = bool(
            (
                await self.db.execute(
                    select(func.count()).select_from(MonitoringSource).where(
                        MonitoringSource.tenant_id == tenant_id, MonitoringSource.source_type.in_(azure_types)
                    )
                )
            ).scalar()
        )
        vaults = await self._vaults(tenant_id, filters)
        statuses = [(v.table_status or {}).get("replication") for v in vaults.values() if v.provider == "azure"]
        status = (
            "failed" if "failed" in statuses
            else "collected" if "collected" in statuses
            else "empty" if "empty" in statuses
            else None
        )
        items = await self.items(tenant_id, filters=filters, now=now)
        plans = await self.plans(tenant_id, filters=filters, now=now)
        health = [i.replication_health for i in items]
        regions: dict[tuple, list[ReplicatedItemOut]] = {}
        for i in items:
            regions.setdefault((i.source_location, i.target_location), []).append(i)
        rpos = [i.rpo_seconds for i in items if i.rpo_seconds is not None]
        ready = sum(1 for i in items if i.readiness == "ready")
        return ReplicationOverview(
            configured=configured,
            replication_status=status,
            generated_at=now,
            dr_test_max_age_days=max_age,
            items_total=len(items),
            health_normal=health.count("normal"),
            health_warning=health.count("warning"),
            health_critical=health.count("critical"),
            health_not_reported=health.count(None),
            failover_health_warning=sum(1 for i in items if i.failover_health == "warning"),
            failover_health_critical=sum(1 for i in items if i.failover_health == "critical"),
            failed_over=sum(1 for i in items if i.readiness == "failed_over"),
            never_tested=sum(1 for i in items if i.last_test_failover_at is None),
            test_overdue=sum(
                1 for i in items if i.last_test_failover_at is not None and (now - i.last_test_failover_at).days > max_age
            ),
            ready=ready,
            readiness_pct=round(ready / len(items) * 100, 1) if items else None,
            max_rpo_seconds=max(rpos) if rpos else None,
            plans_total=len(plans),
            plans_ready=sum(1 for p in plans if p.readiness == "ready"),
            by_region=sorted(
                (
                    ReplicationRegionGroup(
                        source_location=src, target_location=dst, items=len(group),
                        ready=sum(1 for i in group if i.readiness == "ready"),
                        unhealthy=sum(1 for i in group if i.replication_health in ("warning", "critical")),
                    )
                    for (src, dst), group in regions.items()
                ),
                key=lambda g: -g.items,
            ),
        )


__all__ = ["DisasterRecoveryService", "readiness"]
