"""
Tenant privacy settings (enterprise security programme S5): AI data use,
retention and the offboarding state. `load_privacy` returns the defaults for a
tenant that never changed them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant_privacy import TenantPrivacySetting


@dataclass(frozen=True)
class Privacy:
    ai_mode: str = "on"
    ai_redact_emails: bool = True
    ai_redact_ips: bool = False
    metrics_retention_days: int | None = None
    events_retention_days: int | None = None
    deletion_requested_at: datetime | None = None
    deletion_scheduled_for: datetime | None = None
    deletion_requested_by: str | None = None
    saved: bool = False


async def load_privacy(db: AsyncSession, tenant_id: uuid.UUID) -> Privacy:
    row = await db.get(TenantPrivacySetting, tenant_id)
    if row is None:
        return Privacy()
    return Privacy(
        ai_mode=row.ai_mode,
        ai_redact_emails=row.ai_redact_emails,
        ai_redact_ips=row.ai_redact_ips,
        metrics_retention_days=row.metrics_retention_days,
        events_retention_days=row.events_retention_days,
        deletion_requested_at=row.deletion_requested_at,
        deletion_scheduled_for=row.deletion_scheduled_for,
        deletion_requested_by=row.deletion_requested_by,
        saved=True,
    )


async def privacy_row(db: AsyncSession, tenant_id: uuid.UUID) -> TenantPrivacySetting:
    """The tenant's row, created with defaults if missing (caller commits)."""
    row = await db.get(TenantPrivacySetting, tenant_id)
    if row is None:
        row = TenantPrivacySetting(tenant_id=tenant_id, ai_mode="on", ai_redact_emails=True, ai_redact_ips=False)
        db.add(row)
        await db.flush()
    return row


async def tenant_ids_with_settings(db: AsyncSession) -> list[uuid.UUID]:
    return list((await db.execute(select(TenantPrivacySetting.tenant_id))).scalars())
