"""
Agent API routes — virtual company agent management.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id as get_current_tenant
from app.core.db import get_db
from app.schemas.agent import AgentCreate, AgentRead, AgentTreeNode, AgentUpdate
from app.services.agent_service import AgentNotFound, AgentService
from app.repositories.agent_repository import AgentRepository

router = APIRouter(prefix="/agents", tags=["agents"])


def get_agent_service_dep(db: AsyncSession = Depends(get_db)) -> AgentService:
    return AgentService(agent_repo=AgentRepository(db))


@router.get("", response_model=list[AgentRead])
async def list_agents(
    department: str | None = None,
    level: str | None = None,
    role: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    availability: str | None = None,
    is_active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """List all agents in the organization."""
    return await service.list_agents(
        tenant_id,
        department=department,
        level=level,
        role=role,
        status=status_filter,
        availability=availability,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.get("/tree", response_model=list[AgentTreeNode])
async def get_organization_tree(
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Get the full organization hierarchy tree."""
    return await service.get_organization_tree(tenant_id)


@router.get("/available", response_model=list[AgentRead])
async def list_available_agents(
    department: str | None = None,
    level: str | None = None,
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """List agents available for new work assignments."""
    return await service.list_available(tenant_id, department=department, level=level)


@router.get("/by-skills", response_model=list[AgentRead])
async def list_agents_by_skills(
    skills: list[str] = Query(...),
    department: str | None = None,
    level: str | None = None,
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Find agents matching specific skills."""
    return await service.list_by_skills(tenant_id, skills, department=department, level=level)


@router.get("/by-task-type", response_model=list[AgentRead])
async def list_agents_by_task_type(
    task_type: str = Query(...),
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Find agents that support a specific task type."""
    return await service.list_by_task_type(tenant_id, task_type)


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Register a new agent in the organization."""
    agent = await service.create_agent(tenant_id, **body.model_dump())
    # Committed like every other write in this API. Without it the new agent
    # existed only inside this request's session: the 201 was returned from the
    # in-session object and the row was rolled back when the session closed, so
    # every subsequent request for that agent (get, update, availability)
    # answered 404.
    await db.commit()
    return agent


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: uuid.UUID,
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Get a specific agent by ID."""
    try:
        return await service.get(agent_id, tenant_id)
    except AgentNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Update an agent's profile."""
    try:
        agent = await service.update_agent(
            agent_id, tenant_id, **body.model_dump(exclude_unset=True)
        )
    except AgentNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    # Commit the same way the other write paths do, so an update is durable
    # rather than a change that only appears to have been applied.
    await db.commit()
    return agent


@router.get("/{agent_id}/reports", response_model=list[AgentRead])
async def get_direct_reports(
    agent_id: uuid.UUID,
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Get direct reports for a manager agent."""
    try:
        await service.get(agent_id, tenant_id)
    except AgentNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return await service.list_by_manager(agent_id, tenant_id)


@router.post("/{agent_id}/availability", response_model=AgentRead)
async def set_agent_availability(
    agent_id: uuid.UUID,
    availability: str = Query(..., pattern="^(available|busy|away|offline)$"),
    service: AgentService = Depends(get_agent_service_dep),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Set an agent's availability status."""
    try:
        agent = await service.set_availability(agent_id, tenant_id, availability)
    except AgentNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    # Durability matters most here: availability drives routing eligibility, so
    # a change visible only inside this request would mislead the next routing
    # decision.
    await db.commit()
    return agent