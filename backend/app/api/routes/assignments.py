"""Enterprise Assignment Engine - API Routes.

Provides endpoints for managing assignment groups, group members, and
smart assignment of tickets.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.assignment_repository import AssignmentRepository
from app.schemas.assignment import (
    AssignmentGroupCreate,
    AssignmentGroupMemberCreate,
    AssignmentGroupMemberOut,
    AssignmentGroupMemberUpdate,
    AssignmentGroupOut,
    AssignmentGroupUpdate,
    AssignmentMetrics,
)
from app.services.assignment_service import AssignmentError, AssignmentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assignment", tags=["assignment"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
async def get_assignment_repo(db: AsyncSession = Depends(get_db)) -> AssignmentRepository:
    return AssignmentRepository(db)


# ---------------------------------------------------------------------------
# Assignment Groups
# ---------------------------------------------------------------------------

@router.get("/groups", response_model=list[AssignmentGroupOut])
async def list_groups(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: AssignmentRepository = Depends(get_assignment_repo),
    is_active: bool | None = None,
):
    """List all assignment groups for the tenant."""
    groups = await repo.list_groups(tenant_id, is_active=is_active)
    return [AssignmentGroupOut.model_validate(g) for g in groups]


@router.post("/groups", dependencies=[_active_sub_guard], response_model=AssignmentGroupOut, status_code=201)
async def create_group(
    payload: AssignmentGroupCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Create a new assignment group."""
    existing = await repo.get_group_by_name(tenant_id, payload.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Group '{payload.name}' already exists.")
    group = await repo.create_group(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        strategy=payload.strategy,
        is_active=payload.is_active,
    )
    await db.commit()
    await db.refresh(group)
    return AssignmentGroupOut.model_validate(group)


@router.get("/groups/{group_id}", response_model=AssignmentGroupOut)
async def get_group(
    group_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Get a single assignment group."""
    group = await repo.get_group(group_id, tenant_id)
    if not group:
        raise HTTPException(status_code=404, detail="Assignment group not found.")
    return AssignmentGroupOut.model_validate(group)


@router.patch("/groups/{group_id}", dependencies=[_active_sub_guard], response_model=AssignmentGroupOut)
async def update_group(
    group_id: uuid.UUID,
    payload: AssignmentGroupUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Update an assignment group."""
    group = await repo.get_group(group_id, tenant_id)
    if not group:
        raise HTTPException(status_code=404, detail="Assignment group not found.")
    updates = payload.model_dump(exclude_unset=True)
    updated = await repo.update_group(group, **updates)
    await db.commit()
    await db.refresh(updated)
    return AssignmentGroupOut.model_validate(updated)


@router.delete("/groups/{group_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Soft-delete an assignment group."""
    group = await repo.get_group(group_id, tenant_id)
    if not group:
        raise HTTPException(status_code=404, detail="Assignment group not found.")
    await repo.soft_delete_group(group)


# ---------------------------------------------------------------------------
# Group Members
# ---------------------------------------------------------------------------

@router.get("/groups/{group_id}/members", response_model=list[AssignmentGroupMemberOut])
async def list_members(
    group_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """List all members of an assignment group."""
    group = await repo.get_group(group_id, tenant_id)
    if not group:
        raise HTTPException(status_code=404, detail="Assignment group not found.")
    members = await repo.list_members(group_id, tenant_id)
    return [AssignmentGroupMemberOut.model_validate(m) for m in members]


@router.post("/groups/{group_id}/members", dependencies=[_active_sub_guard], response_model=AssignmentGroupMemberOut, status_code=201)
async def add_member(
    group_id: uuid.UUID,
    payload: AssignmentGroupMemberCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Add a member to an assignment group."""
    group = await repo.get_group(group_id, tenant_id)
    if not group:
        raise HTTPException(status_code=404, detail="Assignment group not found.")
    existing = await repo.get_member_by_group_and_user(group_id, payload.user_id, tenant_id)
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member of this group.")
    member = await repo.add_member(
        tenant_id=tenant_id,
        group_id=group_id,
        user_id=payload.user_id,
        skills_json=payload.skills_json,
        capacity_weight=payload.capacity_weight,
        is_active=payload.is_active,
    )
    await db.commit()
    await db.refresh(member)
    return AssignmentGroupMemberOut.model_validate(member)


@router.patch("/groups/{group_id}/members/{member_id}", dependencies=[_active_sub_guard], response_model=AssignmentGroupMemberOut)
async def update_member(
    group_id: uuid.UUID,
    member_id: uuid.UUID,
    payload: AssignmentGroupMemberUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Update a group member."""
    member = await repo.get_member(member_id, tenant_id)
    if not member or member.group_id != group_id:
        raise HTTPException(status_code=404, detail="Group member not found.")
    updates = payload.model_dump(exclude_unset=True)
    updated = await repo.update_member(member, **updates)
    await db.commit()
    await db.refresh(updated)
    return AssignmentGroupMemberOut.model_validate(updated)


@router.delete("/groups/{group_id}/members/{member_id}", dependencies=[_active_sub_guard], status_code=204)
async def remove_member(
    group_id: uuid.UUID,
    member_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.execute")),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Remove a member from an assignment group."""
    member = await repo.get_member(member_id, tenant_id)
    if not member or member.group_id != group_id:
        raise HTTPException(status_code=404, detail="Group member not found.")
    await repo.remove_member(member)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics", response_model=list[AssignmentMetrics])
async def get_metrics(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: AssignmentRepository = Depends(get_assignment_repo),
):
    """Get assignment metrics for all groups."""
    groups = await repo.list_groups(tenant_id)
    result = []
    for group in groups:
        members = await repo.list_members(group.id, tenant_id)
        active = [m for m in members if m.is_active]
        open_count = await repo.count_open_tickets_for_group(group.id, tenant_id)
        result.append(
            AssignmentMetrics(
                group_id=group.id,
                group_name=group.name,
                total_members=len(members),
                active_members=len(active),
                open_tickets=open_count,
            )
        )
    return result