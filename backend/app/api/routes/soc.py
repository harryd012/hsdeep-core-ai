"""
SOC Analyst API.

SOC analysts are Agent rows (department="soc", role="soc_analyst"). This
router exposes the SOC roster with live NOC assignment counts, and the
per-analyst detail view. Read-only — analyst CRUD goes through the
existing /api/agents router.

NOTE(auth): same interim pattern as every other router.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.agent_repository import AgentRepository
from app.repositories.noc_event_repository import NocEventRepository
from app.schemas.agent import AgentRead
from app.schemas.soc import (
    SocAnalystDetailOut,
    SocAnalystOut,
    SocAnalystSummary,
)
from app.services.soc_service import SocAnalystNotFound, SocService

router = APIRouter(prefix="/soc", tags=["soc"])

_active_sub_guard = Depends(require_active_subscription)


def get_soc_service(db: AsyncSession = Depends(get_db)) -> SocService:
    return SocService(
        agent_repo=AgentRepository(db),
        noc_repo=NocEventRepository(db),
    )


def _analyst_to_out(analyst, event_counts: dict | None = None) -> SocAnalystOut:
    """Convert an Agent ORM row + event counts to SocAnalystOut."""
    counts = event_counts or {}
    data = {
        **AgentRead.model_validate(analyst).model_dump(),
        "open_noc_events": counts.get("open", 0),
        "acknowledged_noc_events": counts.get("acknowledged", 0),
        "total_assigned_events": counts.get("total", 0),
        "resolved_today": counts.get("resolved", 0),
        "last_assigned_at": None,
    }
    return SocAnalystOut(**data)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


@router.get("/analysts", response_model=list[SocAnalystOut])
async def list_soc_analysts(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    availability: str | None = None,
    level: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: SocService = Depends(get_soc_service),
):
    analysts = await service.list_analysts(
        tenant_id,
        availability=availability,
        level=level,
        limit=limit,
        offset=offset,
    )
    # Batch-load event counts for the roster
    from app.repositories.noc_event_repository import NocEventRepository
    noc_repo = NocEventRepository(service.noc_repo.db)
    event_counts = await noc_repo.count_by_analyst(tenant_id)
    return [
        _analyst_to_out(a, event_counts.get(a.id, {}))
        for a in analysts
    ]


@router.get("/analysts/summary", response_model=SocAnalystSummary)
async def soc_analyst_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: SocService = Depends(get_soc_service),
):
    data = await service.summary(tenant_id)
    return SocAnalystSummary(**data)


@router.get("/analysts/{analyst_id}", response_model=SocAnalystDetailOut)
async def get_soc_analyst(
    analyst_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: SocService = Depends(get_soc_service),
):
    try:
        analyst, events = await service.get_analyst_detail(analyst_id, tenant_id)
    except SocAnalystNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from app.repositories.noc_event_repository import NocEventRepository
    noc_repo = NocEventRepository(service.noc_repo.db)
    event_counts = await noc_repo.count_by_analyst(tenant_id)
    counts = event_counts.get(analyst.id, {})

    base_data = AgentRead.model_validate(analyst).model_dump()
    detail_data = {
        **base_data,
        "open_noc_events": counts.get("open", 0),
        "acknowledged_noc_events": counts.get("acknowledged", 0),
        "total_assigned_events": counts.get("total", 0),
        "resolved_today": counts.get("resolved", 0),
        "last_assigned_at": None,
        "assigned_events": [
            {
                "id": str(e.id),
                "title": e.title,
                "severity": e.severity,
                "status": e.status,
                "triggered_at": e.triggered_at.isoformat() if e.triggered_at else None,
            }
            for e in events
        ],
    }
    return SocAnalystDetailOut(**detail_data)
