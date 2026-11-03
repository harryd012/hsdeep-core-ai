"""
Backup monitoring API (docs/engineering/azure-backup-monitoring.md).

Read-only, tenant-scoped views over what the Azure Backup collector stored:
the overview (KPIs, job outcomes per day, RPO verdicts), vaults with their
security posture, protected items and job history, for every backup
provider. Every read needs ``backup.read``; changing the tenant's backup
targets (``PUT /settings``) needs ``backup.write`` and is audited. The tenant
always comes from the authenticated principal.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.schemas.backup import (
    BackupCoverage,
    BackupFailureAnalysis,
    BackupItemDetail,
    BackupItemOut,
    BackupJobDetail,
    BackupJobOut,
    BackupOverview,
    BackupPolicyRow,
    BackupSecurityPosture,
    BackupSettingsOut,
    BackupSettingsUpdate,
    BackupStorageHistory,
    BackupVaultOut,
)
from app.services.audit_service import AuditService
from app.services.backup_service import BackupFilters, BackupService
from app.services.backup_settings import set_tenant_rpo

router = APIRouter(
    prefix="/backup",
    tags=["backup"],
    dependencies=[Depends(require_active_subscription)],
)

_READ = [Depends(require_permission("backup.read"))]


def backup_filters(
    provider: str | None = Query(None, max_length=30),
    subscription_id: str | None = Query(None, max_length=100),
    resource_group: str | None = Query(None, max_length=255),
    location: str | None = Query(None, max_length=100),
    storage_id: uuid.UUID | None = Query(None, description="One storage location (vault / repository)"),
    workload_type: str | None = Query(None, max_length=50),
) -> BackupFilters:
    """The Command Center filters, shared by every read that takes them."""
    return BackupFilters(
        provider=provider or None, subscription_id=subscription_id or None, resource_group=resource_group or None,
        location=location or None, vault_id=storage_id, workload_type=workload_type or None,
    )


@router.get("/overview", response_model=BackupOverview, dependencies=_READ)
async def backup_overview(
    days: int = Query(14, ge=1, le=90, description="Time range for job trend and counts"),
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupOverview:
    """KPIs for the Backup page, the dashboard panel and Backup Readiness;
    the optional filters narrow every figure (``filtered`` is then true)."""
    return await BackupService(db).overview(tenant_id, filters=filters, range_days=days)


@router.get("/vaults", response_model=list[BackupVaultOut], dependencies=_READ)
async def backup_vaults(
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupVaultOut]:
    return await BackupService(db).vaults(tenant_id, filters=filters)


@router.get("/vaults/{vault_id}", response_model=BackupVaultOut, dependencies=_READ)
async def backup_vault(
    vault_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupVaultOut:
    """One storage location; 404 for another tenant's."""
    vault = await BackupService(db).vault(tenant_id, vault_id)
    if vault is None:
        raise HTTPException(status_code=404, detail="Backup storage not found")
    return vault


@router.get("/policies", response_model=list[BackupPolicyRow], dependencies=_READ)
async def backup_policies(
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupPolicyRow]:
    """Backup policies (schedule, retention per tier) and the items stored under each."""
    return await BackupService(db).policies(tenant_id, filters=filters)


@router.get("/items", response_model=list[BackupItemOut], dependencies=_READ)
async def backup_items(
    vault_id: uuid.UUID | None = None,
    rpo_state: Literal["within", "breached", "never", "not_protected"] | None = None,
    site_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200, description="Name or resource id contains (case-insensitive)"),
    protection_state: str | None = Query(None, max_length=30),
    last_backup_status: str | None = Query(None, max_length=30),
    policy_name: str | None = Query(None, max_length=255),
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupItemOut]:
    return await BackupService(db).items(
        tenant_id, vault_id=vault_id, state=rpo_state, site_id=site_id, filters=filters, q=q,
        protection_state=protection_state, last_backup_status=last_backup_status, policy_name=policy_name,
    )


@router.get("/items/{item_id}", response_model=BackupItemDetail, dependencies=_READ)
async def backup_item_detail(
    item_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupItemDetail:
    """One protected item with its policy (schedule, retention) and recent jobs."""
    detail = await BackupService(db).item_detail(tenant_id, item_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Backup item not found")
    return detail


@router.get("/coverage", response_model=BackupCoverage, dependencies=_READ)
async def backup_coverage(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupCoverage:
    """Azure VMs in inventory by backup state: protected, stopped, unprotected, not monitored."""
    return await BackupService(db).coverage(tenant_id)


@router.get("/jobs", response_model=list[BackupJobOut], dependencies=_READ)
async def backup_jobs(
    days: int = Query(7, ge=1, le=90),
    status: str | None = Query(None, max_length=30),
    limit: int = Query(200, ge=1, le=1000),
    operation: str | None = Query(None, max_length=50),
    q: str | None = Query(None, max_length=200, description="Item name, error code or message contains"),
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupJobOut]:
    return await BackupService(db).jobs(
        tenant_id, days=days, status=status, limit=limit, filters=filters, operation=operation, q=q,
    )


@router.get("/jobs/{job_id}", response_model=BackupJobDetail, dependencies=_READ)
async def backup_job_detail(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupJobDetail:
    """One job, the item it ran for and that item's recent jobs; 404 for another tenant's."""
    detail = await BackupService(db).job_detail(tenant_id, job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Backup job not found")
    return detail


@router.get("/failures", response_model=BackupFailureAnalysis, dependencies=_READ)
async def backup_failures(
    days: int = Query(7, ge=1, le=90),
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupFailureAnalysis:
    """Failed jobs grouped by error code: how many, which items, when."""
    return await BackupService(db).failures(tenant_id, days=days, filters=filters)


@router.get("/security", response_model=BackupSecurityPosture, dependencies=_READ)
async def backup_security(
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupSecurityPosture:
    """Security settings of each storage location, checked one by one."""
    return await BackupService(db).security(tenant_id, filters=filters)


@router.get("/storage/history", response_model=BackupStorageHistory, dependencies=_READ)
async def backup_storage_history(
    days: int = Query(30, ge=7, le=365),
    filters: BackupFilters = Depends(backup_filters),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupStorageHistory:
    """Daily storage used / capacity / free, and a growth estimate after 14 days of history."""
    return await BackupService(db).storage_history(tenant_id, days=days, filters=filters)


@router.get("/settings", response_model=BackupSettingsOut, dependencies=_READ)
async def backup_settings(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupSettingsOut:
    """The tenant's RPO and the platform default it falls back to."""
    return await BackupService(db).settings(tenant_id)


@router.put("/settings", response_model=BackupSettingsOut)
async def update_backup_settings(
    payload: BackupSettingsUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("backup.write")),
    db: AsyncSession = Depends(get_db),
) -> BackupSettingsOut:
    """Set the tenant's RPO (null = platform default). Alerts use it from the next poll."""
    previous = await set_tenant_rpo(db, tenant_id, payload.rpo_hours)
    if previous != payload.rpo_hours:
        await AuditService(db).record_entity(
            tenant_id=tenant_id,
            action="backup.settings.updated",
            entity_type="backup_settings",
            entity_id=None,
            actor_id=principal.user_id,
            actor_email=principal.email,
            description="Backup RPO changed",
            old_values={"rpo_hours": previous},
            new_values={"rpo_hours": payload.rpo_hours},
        )
    await db.commit()
    return await BackupService(db).settings(tenant_id)
