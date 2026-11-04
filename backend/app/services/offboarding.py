"""
Tenant offboarding (enterprise security programme S5).

An Owner (tenant.delete) asks for the whole tenant to be deleted, confirming
with the organization's name. Nothing is removed at once: the deletion is
scheduled DELETION_GRACE_DAYS later and can be cancelled until then. On the
scheduled date the purge job deletes the tenant row; every table that belongs
to the tenant references it with ON DELETE CASCADE, so all of its data goes
with it, and any export files are removed. The purge is logged (the tenant's
own audit log is part of what is deleted).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.tenant_export import TenantExport
from app.models.tenant_privacy import DELETION_GRACE_DAYS, TenantPrivacySetting

logger = logging.getLogger(__name__)


async def purge_due_tenants(db: AsyncSession, now: datetime | None = None) -> list[str]:
    """Delete every tenant whose scheduled deletion date has passed. Returns their ids."""
    now = now or datetime.now(timezone.utc)
    due = (
        await db.execute(
            select(TenantPrivacySetting).where(
                TenantPrivacySetting.deletion_scheduled_for.is_not(None),
                TenantPrivacySetting.deletion_scheduled_for <= now,
            )
        )
    ).scalars().all()
    purged: list[str] = []
    for row in due:
        tenant_id = row.tenant_id
        files = (await db.execute(select(TenantExport.file_path).where(TenantExport.tenant_id == tenant_id))).scalars().all()
        try:
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        except Exception:  # noqa: BLE001 - leave it for the next run, keep going
            await db.rollback()
            logger.exception("Tenant purge failed for %s; will retry", tenant_id)
            continue
        for f in files:
            if f and Path(f).exists():
                Path(f).unlink()
        logger.warning("Tenant %s deleted on schedule (requested by %s at %s).",
                       tenant_id, row.deletion_requested_by, row.deletion_requested_at)
        purged.append(str(tenant_id))
    return purged


def scheduled_date(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(days=DELETION_GRACE_DAYS)


async def tenant_purge_job() -> None:
    from app.core.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await purge_due_tenants(db)
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the scheduler
        logger.exception("Tenant purge job failed")
