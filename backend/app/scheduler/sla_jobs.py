"""SLA scheduler jobs - periodic SLA breach detection for all tenants."""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.services.sla_service import SLAService

logger = logging.getLogger(__name__)


async def sla_breach_check_job() -> None:
    """Check all tenants for SLA breaches.

    Runs every 5 minutes. For each tenant, queries all non-breached,
    non-paused tickets with SLA policies and checks if their resolution
    due date has passed.
    """
    async with AsyncSessionLocal() as db:
        # Get all tenants
        stmt = select(Tenant.id)
        tenant_ids = list((await db.execute(stmt)).scalars().all())

        total_breaches = 0
        for tenant_id in tenant_ids:
            try:
                sla_service = SLAService(db)
                breaches = await sla_service.check_breaches_for_tenant(tenant_id)
                if breaches:
                    logger.warning("SLA breach check: %d new breach(es) for tenant %s", breaches, tenant_id)
                total_breaches += breaches
            except Exception as exc:
                logger.error("SLA breach check failed for tenant %s: %s", tenant_id, exc, exc_info=True)

        if total_breaches:
            await db.commit()
            logger.info("SLA breach check complete: %d total new breach(es) across %d tenants", total_breaches, len(tenant_ids))