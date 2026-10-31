"""
NOC Event API.

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.noc_event_repository import NocEventRepository
from app.repositories.agent_repository import AgentRepository
from app.schemas.noc import (
    NocEventAcknowledge,
    NocEventAssign,
    NocEventCreate,
    NocEventOut,
    NocEventPagination,
    NocEventResolve,
    NocEventSeverity,
    NocEventStatistics,
    NocEventStatus,
    NocEventSummary,
    NocEventUpdate,
)
from app.services.noc_service import NocEventNotFound, NocEventService, NocEventInvalidTransition

router = APIRouter(prefix="/noc/events", tags=["noc-events"])

_active_sub_guard = Depends(require_active_subscription)


def get_noc_service(db: AsyncSession = Depends(get_db)) -> NocEventService:
    return NocEventService(
        noc_repo=NocEventRepository(db),
        agent_repo=AgentRepository(db),
    )


# ---------------------------------------------------------------------------
# List / read
# ---------------------------------------------------------------------------


@router.get("", response_model=NocEventPagination)
async def list_noc_events(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    severity: NocEventSeverity | None = None,
    status: NocEventStatus | None = None,
    category: str | None = None,
    source: str | None = None,
    assigned_analyst_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: NocEventService = Depends(get_noc_service),
):
    events = await service.list_events(
        tenant_id,
        site_id=site_id,
        device_id=device_id,
        severity=severity,
        status=status,
        category=category,
        source=source,
        assigned_analyst_id=assigned_analyst_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    total = await service.count_events(
        tenant_id,
        site_id=site_id,
        device_id=device_id,
        severity=severity,
        status=status,
        category=category,
        source=source,
        assigned_analyst_id=assigned_analyst_id,
    )
    return NocEventPagination(
        items=[NocEventOut.model_validate(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=NocEventSummary)
async def noc_event_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: NocEventService = Depends(get_noc_service),
):
    data = await service.summary(tenant_id)
    return NocEventSummary(**data)


@router.get("/statistics", response_model=NocEventStatistics)
async def noc_event_statistics(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: NocEventService = Depends(get_noc_service),
):
    data = await service.statistics(tenant_id)
    return NocEventStatistics(**data)


@router.get("/{event_id}", response_model=NocEventOut)
async def get_noc_event(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: NocEventService = Depends(get_noc_service),
):
    try:
        event = await service.get_with_joins(event_id, tenant_id)
    except NocEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return event


# ---------------------------------------------------------------------------
# Create / Update (write — active subscription required)
# ---------------------------------------------------------------------------


@router.post("", dependencies=[Depends(require_permission("noc.write")), _active_sub_guard], response_model=NocEventOut, status_code=status.HTTP_201_CREATED)
async def create_noc_event(
    payload: NocEventCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NocEventService = Depends(get_noc_service),
):
    try:
        event = await service.create(
            tenant_id,
            title=payload.title,
            severity=payload.severity,
            source=payload.source,
            category=payload.category,
            description=payload.description,
            event_signature=payload.event_signature,
            triggered_at=payload.triggered_at,
            site_id=payload.site_id,
            device_id=payload.device_id,
            source_agent_id=payload.source_agent_id,
            assigned_analyst_id=payload.assigned_analyst_id,
            confidence_score=payload.confidence_score,
        )
    except NocEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return event


@router.patch("/{event_id}", dependencies=[Depends(require_permission("noc.write")), _active_sub_guard], response_model=NocEventOut)
async def update_noc_event(
    event_id: uuid.UUID,
    payload: NocEventUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NocEventService = Depends(get_noc_service),
):
    try:
        event = await service.update(
            event_id,
            tenant_id,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            category=payload.category,
            assigned_analyst_id=payload.assigned_analyst_id,
            status=payload.status,
        )
    except NocEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NocEventInvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return event


# ---------------------------------------------------------------------------
# Operator actions (write — active subscription required)
# ---------------------------------------------------------------------------


@router.post("/{event_id}/acknowledge", dependencies=[Depends(require_permission("noc.write")), _active_sub_guard], response_model=NocEventOut)
async def acknowledge_noc_event(
    event_id: uuid.UUID,
    payload: NocEventAcknowledge,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NocEventService = Depends(get_noc_service),
):
    try:
        event = await service.acknowledge(event_id, tenant_id, payload.acknowledged_by)
    except NocEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return event


@router.post("/{event_id}/resolve", dependencies=[Depends(require_permission("noc.write")), _active_sub_guard], response_model=NocEventOut)
async def resolve_noc_event(
    event_id: uuid.UUID,
    payload: NocEventResolve,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NocEventService = Depends(get_noc_service),
):
    try:
        event = await service.resolve(event_id, tenant_id, payload.resolved_by)
    except NocEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return event


@router.post("/{event_id}/assign", dependencies=[Depends(require_permission("noc.write")), _active_sub_guard], response_model=NocEventOut)
async def assign_noc_event(
    event_id: uuid.UUID,
    payload: NocEventAssign,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NocEventService = Depends(get_noc_service),
):
    try:
        event = await service.assign_analyst(event_id, tenant_id, payload.analyst_id)
    except NocEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return event


# ---------------------------------------------------------------------------
# Phase 5 - topology-aware NOC correlation (additive router)
# ---------------------------------------------------------------------------

from app.schemas.correlation import (  # noqa: E402
    CorrelationGroupSummary,
    CorrelationResult,
    CorrelationRunRequest,
)
from app.services.noc_correlation import NocCorrelationEngine  # noqa: E402

correlation_router = APIRouter(prefix="/noc/correlation", tags=["noc-correlation"])


def get_correlation_engine(db: AsyncSession = Depends(get_db)) -> NocCorrelationEngine:
    return NocCorrelationEngine(db)


@correlation_router.post(
    "/run",
    response_model=list[CorrelationResult],
    dependencies=[Depends(require_permission("noc.write")), _active_sub_guard],
)
async def run_correlation(
    payload: CorrelationRunRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    engine: NocCorrelationEngine = Depends(get_correlation_engine),
):
    """Correlate recent uncorrelated normalized events into NOC groups.

    Idempotent: already-correlated events are skipped, so re-running on
    unchanged data returns [] rather than duplicating groups.
    """
    return await engine.run(tenant_id, payload)


@correlation_router.get("/groups", response_model=list[CorrelationGroupSummary])
async def list_correlation_groups(
    limit: int = Query(50, ge=1, le=500),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    engine: NocCorrelationEngine = Depends(get_correlation_engine),
):
    """Read-side listing of active correlation groups for this tenant."""
    return await engine.group_summaries(tenant_id, limit=limit)
