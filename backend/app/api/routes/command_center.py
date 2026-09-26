"""
AI Operations Command Center API.

Exposes the CommandCenterService aggregate read model (metrics, org tree,
agent cards, task queue, department workload, activity feed). Everything
returned is assembled from real Agent/Task rows by the service layer - no
mock data, no fabricated counts.

NOTE(auth): same pattern as every other router in this codebase - tenant
is derived from the authenticated principal via get_tenant_id.

The command-intake endpoint (POST) that turns a free-text owner command
into a real routed Task is added by the Copilot wiring (Step 3) alongside
the LLM gateway; this router ships the read surface first.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.schemas.command_center import CommandCenterOverview
from app.services.command_center_service import CommandCenterService

router = APIRouter(prefix="/command-center", tags=["command-center"])


def get_command_center_service(db: AsyncSession = Depends(get_db)) -> CommandCenterService:
    return CommandCenterService(db)


@router.get("/overview", response_model=CommandCenterOverview)
async def command_center_overview(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: CommandCenterService = Depends(get_command_center_service),
):
    """Full Command Center view: metrics, org tree, agents, tasks,
    per-department workload and the live activity feed - all derived
    from the tenant's real Agent and Task rows."""
    return await service.overview(tenant_id)
