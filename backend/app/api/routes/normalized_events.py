"""
Normalized Event API.

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.normalized_event_repository import NormalizedEventRepository
from app.schemas.normalized_event import (
    NormalizedEventCreate,
    NormalizedEventOut,
    NormalizedEventPagination,
    NormalizedEventSeverity,
    NormalizedEventCategory,
    NormalizedEventSummary,
    NormalizedEventUpdate,
)
from app.services.normalized_event_service import NormalizedEventNotFound, NormalizedEventService

router = APIRouter(prefix="/normalized-events", tags=["normalized-events"])

_active_sub_guard = Depends(require_active_subscription)


def get_normalized_event_service(db: AsyncSession = Depends(get_db)) -> NormalizedEventService:
    return NormalizedEventService(repo=NormalizedEventRepository(db))


# ---------------------------------------------------------------------------
# List / read
# ---------------------------------------------------------------------------


@router.get("", response_model=NormalizedEventPagination)
async def list_normalized_events(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    integration_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    severity: NormalizedEventSeverity | None = None,
    event_category: NormalizedEventCategory | None = None,
    event_type: str | None = None,
    telemetry_type: str | None = None,
    is_suppressed: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: NormalizedEventService = Depends(get_normalized_event_service),
):
    items = await service.list_events(
        tenant_id,
        integration_id=integration_id,
        site_id=site_id,
        device_id=device_id,
        severity=severity,
        event_category=event_category,
        event_type=event_type,
        telemetry_type=telemetry_type,
        is_suppressed=is_suppressed,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    total = await service.count_events(
        tenant_id,
        integration_id=integration_id,
        site_id=site_id,
        device_id=device_id,
        severity=severity,
        event_category=event_category,
        event_type=event_type,
        telemetry_type=telemetry_type,
        is_suppressed=is_suppressed,
    )
    return NormalizedEventPagination(
        items=[
            NormalizedEventOut.model_validate(e)
            for e in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=NormalizedEventSummary)
async def normalized_event_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: NormalizedEventService = Depends(get_normalized_event_service),
):
    data = await service.summary(tenant_id)
    return NormalizedEventSummary(**data)


@router.get("/{event_id}", response_model=NormalizedEventOut)
async def get_normalized_event(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: NormalizedEventService = Depends(get_normalized_event_service),
):
    try:
        return await service.get_with_joins(event_id, tenant_id)
    except NormalizedEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

# ---------------------------------------------------------------------------
# Create / Update (write — active subscription required)
# ---------------------------------------------------------------------------


@router.post("", dependencies=[_active_sub_guard], response_model=NormalizedEventOut)
async def create_normalized_event(
    payload: NormalizedEventCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NormalizedEventService = Depends(get_normalized_event_service),
):
    event, _created = await service.ingest(
        tenant_id,
        title=payload.title,
        severity=payload.severity,
        event_category=payload.event_category,
        event_type=payload.event_type,
        telemetry_type=payload.telemetry_type,
        event_timestamp=payload.event_timestamp,
        integration_id=payload.integration_id,
        site_id=payload.site_id,
        device_id=payload.device_id,
        source_event_id=payload.source_event_id,
        event_signature=payload.event_signature,
        description=payload.description,
        attributes=payload.attributes,
        confidence_score=payload.confidence_score,
        raw_reference=payload.raw_reference,
    )
    await db.commit()
    return event


@router.patch("/{event_id}", dependencies=[_active_sub_guard], response_model=NormalizedEventOut)
async def update_normalized_event(
    event_id: uuid.UUID,
    payload: NormalizedEventUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: NormalizedEventService = Depends(get_normalized_event_service),
):
    try:
        event = await service.update(
            event_id,
            tenant_id,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            event_category=payload.event_category,
            event_type=payload.event_type,
            is_suppressed=payload.is_suppressed,
            attributes=payload.attributes,
        )
    except NormalizedEventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return event