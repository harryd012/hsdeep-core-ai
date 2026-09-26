"""Enterprise SLA Engine - API Routes.

Provides endpoints for managing SLA policies, business hours, holidays,
and viewing SLA status on tickets.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.ticket import Ticket
from app.repositories.sla_repository import SLARepository
from app.schemas.sla import (
    BusinessHoursCreate,
    BusinessHoursOut,
    BusinessHoursUpdate,
    HolidayCreate,
    HolidayOut,
    SLADashboardStats,
    SLAPolicyCreate,
    SLAPolicyOut,
    SLAPolicyUpdate,
    SLAStatusOut,
)
from app.services.sla_service import SLAService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sla", tags=["sla"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
async def get_sla_repo(db: AsyncSession = Depends(get_db)) -> SLARepository:
    return SLARepository(db)


# ---------------------------------------------------------------------------
# SLA Policies
# ---------------------------------------------------------------------------

@router.get("/policies", response_model=list[SLAPolicyOut])
async def list_policies(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: SLARepository = Depends(get_sla_repo),
    is_active: bool | None = None,
):
    policies = await repo.list_policies(tenant_id, is_active=is_active)
    return [SLAPolicyOut.model_validate(p) for p in policies]


@router.post("/policies", dependencies=[_active_sub_guard], response_model=SLAPolicyOut, status_code=201)
async def create_policy(
    payload: SLAPolicyCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    policy = await repo.create_policy(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        priority=payload.priority,
        business_service_id=payload.business_service_id,
        response_time_minutes=payload.response_time_minutes,
        resolution_time_minutes=payload.resolution_time_minutes,
        warning_threshold_percent=payload.warning_threshold_percent,
        use_business_hours=payload.use_business_hours,
        business_hours_id=payload.business_hours_id,
        is_active=payload.is_active,
    )
    await db.commit()
    await db.refresh(policy)
    return SLAPolicyOut.model_validate(policy)


@router.get("/policies/{policy_id}", response_model=SLAPolicyOut)
async def get_policy(
    policy_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: SLARepository = Depends(get_sla_repo),
):
    policy = await repo.get_policy(policy_id, tenant_id)
    if not policy:
        raise HTTPException(status_code=404, detail="SLA policy not found.")
    return SLAPolicyOut.model_validate(policy)


@router.patch("/policies/{policy_id}", dependencies=[_active_sub_guard], response_model=SLAPolicyOut)
async def update_policy(
    policy_id: uuid.UUID,
    payload: SLAPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    policy = await repo.get_policy(policy_id, tenant_id)
    if not policy:
        raise HTTPException(status_code=404, detail="SLA policy not found.")
    updates = payload.model_dump(exclude_unset=True)
    updated = await repo.update_policy(policy, **updates)
    await db.commit()
    await db.refresh(updated)
    return SLAPolicyOut.model_validate(updated)


@router.delete("/policies/{policy_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_policy(
    policy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    policy = await repo.get_policy(policy_id, tenant_id)
    if not policy:
        raise HTTPException(status_code=404, detail="SLA policy not found.")
    await repo.soft_delete_policy(policy)
    await db.commit()


# ---------------------------------------------------------------------------
# Business Hours
# ---------------------------------------------------------------------------

@router.get("/business-hours", response_model=list[BusinessHoursOut])
async def list_business_hours(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: SLARepository = Depends(get_sla_repo),
):
    items = await repo.list_business_hours(tenant_id)
    return [BusinessHoursOut.model_validate(b) for b in items]


@router.post("/business-hours", dependencies=[_active_sub_guard], response_model=BusinessHoursOut, status_code=201)
async def create_business_hours(
    payload: BusinessHoursCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    bh = await repo.create_business_hours(tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    await db.refresh(bh)
    return BusinessHoursOut.model_validate(bh)


@router.patch("/business-hours/{bh_id}", dependencies=[_active_sub_guard], response_model=BusinessHoursOut)
async def update_business_hours(
    bh_id: uuid.UUID,
    payload: BusinessHoursUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    bh = await repo.get_business_hours(bh_id, tenant_id)
    if not bh:
        raise HTTPException(status_code=404, detail="Business hours not found.")
    updates = payload.model_dump(exclude_unset=True)
    updated = await repo.update_business_hours(bh, **updates)
    await db.commit()
    await db.refresh(updated)
    return BusinessHoursOut.model_validate(updated)


@router.delete("/business-hours/{bh_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_business_hours(
    bh_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    bh = await repo.get_business_hours(bh_id, tenant_id)
    if not bh:
        raise HTTPException(status_code=404, detail="Business hours not found.")
    await repo.soft_delete_business_hours(bh)
    await db.commit()


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------

@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: SLARepository = Depends(get_sla_repo),
):
    items = await repo.list_holidays(tenant_id)
    return [HolidayOut.model_validate(h) for h in items]


@router.post("/holidays", dependencies=[_active_sub_guard], response_model=HolidayOut, status_code=201)
async def create_holiday(
    payload: HolidayCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    holiday = await repo.create_holiday(tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    await db.refresh(holiday)
    return HolidayOut.model_validate(holiday)


@router.delete("/holidays/{holiday_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_holiday(
    holiday_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    repo: SLARepository = Depends(get_sla_repo),
):
    holidays = await repo.list_holidays(tenant_id)
    holiday = next((h for h in holidays if h.id == holiday_id), None)
    if not holiday:
        raise HTTPException(status_code=404, detail="Holiday not found.")
    await repo.soft_delete_holiday(holiday)
    await db.commit()


# ---------------------------------------------------------------------------
# SLA Status & Dashboard
# ---------------------------------------------------------------------------

@router.get("/tickets/{ticket_id}/status", response_model=SLAStatusOut)
async def get_ticket_sla_status(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Get the SLA status for a specific ticket."""
    stmt = select(Ticket).where(
        Ticket.id == ticket_id, Ticket.tenant_id == tenant_id, Ticket.is_deleted.is_(False)
    )
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    sla_service = SLAService(db)
    status = await sla_service.get_sla_status(ticket)
    return SLAStatusOut(**status)


@router.get("/dashboard", response_model=SLADashboardStats)
async def get_sla_dashboard(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Get SLA dashboard statistics for the tenant."""
    base = select(func.count()).select_from(Ticket).where(
        Ticket.tenant_id == tenant_id, Ticket.is_deleted.is_(False)
    )

    total = (await db.execute(base.where(Ticket.sla_policy_id.is_not(None)))).scalar_one()
    breached = (await db.execute(base.where(Ticket.sla_breached.is_(True)))).scalar_one()
    paused = (await db.execute(base.where(Ticket.sla_paused_at.is_not(None)))).scalar_one()
    on_track = total - breached - paused

    return SLADashboardStats(
        total_tickets_with_sla=total,
        breached_count=breached,
        warning_count=0,
        paused_count=paused,
        on_track_count=on_track,
    )