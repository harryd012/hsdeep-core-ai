"""Per-tenant backup targets (docs/engineering/azure-backup-monitoring.md).

One place resolves a tenant's RPO, so the alert readings
(`app.services.monitoring.backup_saver`) and every read surface
(`app.services.backup_service`) judge an item against the same number.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.backup import BackupSettings

#: Bounds for a tenant RPO: one hour to 30 days.
RPO_MIN_HOURS = 1
RPO_MAX_HOURS = 720


async def tenant_rpo_override(db: AsyncSession, tenant_id: uuid.UUID) -> int | None:
    """The tenant's own RPO, or None when it uses the platform default."""
    return (
        await db.execute(select(BackupSettings.rpo_hours).where(BackupSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def effective_rpo_hours(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """The RPO every backup verdict for this tenant uses."""
    override = await tenant_rpo_override(db, tenant_id)
    return override if override is not None else get_settings().backup_rpo_hours


async def set_tenant_rpo(db: AsyncSession, tenant_id: uuid.UUID, rpo_hours: int | None) -> int | None:
    """Store the tenant's RPO (None returns it to the default); returns the
    previous override. The caller commits and audits."""
    row = (
        await db.execute(select(BackupSettings).where(BackupSettings.tenant_id == tenant_id))
    ).scalar_one_or_none()
    previous = row.rpo_hours if row else None
    if row is None:
        row = BackupSettings(tenant_id=tenant_id)
        db.add(row)
    row.rpo_hours = rpo_hours
    await db.flush()
    return previous


__all__ = ["RPO_MAX_HOURS", "RPO_MIN_HOURS", "effective_rpo_hours", "set_tenant_rpo", "tenant_rpo_override"]
