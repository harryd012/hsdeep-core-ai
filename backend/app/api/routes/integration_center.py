"""
Integration Center (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P12 slice 1).

  GET /api/integration-center                     every monitoring source and integration with
                                                  one derived state, 24 h run statistics, counts
  GET /api/integration-center/sources/{id}        one source with its last 50 collector runs and
                                                  health-state events (sync history)

Read-only, `monitoring.read`, tenant-scoped; another tenant's source is a 404.
Syncing a source stays on the existing POST /api/monitoring/sources/{id}/sync
(`monitoring.update`).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.services.monitoring.integration_center import IntegrationCenter, IntegrationSourceNotFound

router = APIRouter(prefix="/integration-center", tags=["integration-center"],
                   dependencies=[Depends(require_permission("monitoring.read"))])


@router.get("")
async def integration_center_overview(tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)) -> dict:
    return await IntegrationCenter(db).overview(tenant_id)


@router.get("/sources/{source_id}")
async def integration_center_source(source_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id),
                                    db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await IntegrationCenter(db).source_detail(tenant_id, source_id)
    except IntegrationSourceNotFound as exc:
        raise HTTPException(status_code=404, detail="Source not found") from exc
