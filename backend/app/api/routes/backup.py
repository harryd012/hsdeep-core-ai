"""
Backup monitoring API (docs/engineering/azure-backup-monitoring.md).

Read-only, tenant-scoped views over what the Azure Backup collector stored:
the overview (KPIs, job outcomes per day, RPO verdicts), vaults with their
security posture, protected items and job history. Every route needs
``backup.read``; the tenant always comes from the authenticated principal.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.schemas.backup import BackupItemOut, BackupJobOut, BackupOverview, BackupVaultOut
from app.services.backup_service import BackupService

router = APIRouter(
    prefix="/backup",
    tags=["backup"],
    dependencies=[Depends(require_active_subscription)],
)

_READ = [Depends(require_permission("backup.read"))]


@router.get("/overview", response_model=BackupOverview, dependencies=_READ)
async def backup_overview(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BackupOverview:
    """KPIs for the Backup page, the dashboard panel and Backup Readiness."""
    return await BackupService(db).overview(tenant_id)


@router.get("/vaults", response_model=list[BackupVaultOut], dependencies=_READ)
async def backup_vaults(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupVaultOut]:
    return await BackupService(db).vaults(tenant_id)


@router.get("/items", response_model=list[BackupItemOut], dependencies=_READ)
async def backup_items(
    vault_id: uuid.UUID | None = None,
    rpo_state: Literal["within", "breached", "never", "not_protected"] | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupItemOut]:
    return await BackupService(db).items(tenant_id, vault_id=vault_id, state=rpo_state)


@router.get("/jobs", response_model=list[BackupJobOut], dependencies=_READ)
async def backup_jobs(
    days: int = Query(7, ge=1, le=90),
    status: str | None = Query(None, max_length=30),
    limit: int = Query(200, ge=1, le=1000),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[BackupJobOut]:
    return await BackupService(db).jobs(tenant_id, days=days, status=status, limit=limit)
