"""
Integration API.

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.integration_repository import IntegrationRepository
from app.schemas.integration import (
    IntegrationCreate,
    IntegrationOut,
    IntegrationPagination,
    IntegrationProviderType,
    IntegrationStatus,
    IntegrationSummary,
    IntegrationUpdate,
)
from app.services.integration_service import IntegrationNotFound, IntegrationService

router = APIRouter(prefix="/integrations", tags=["integrations"])

_active_sub_guard = Depends(require_active_subscription)


def get_integration_service(db: AsyncSession = Depends(get_db)) -> IntegrationService:
    return IntegrationService(repo=IntegrationRepository(db))


# ---------------------------------------------------------------------------
# List / read
# ---------------------------------------------------------------------------


@router.get("", response_model=IntegrationPagination)
async def list_integrations(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    provider_type: IntegrationProviderType | None = None,
    provider: str | None = None,
    status: IntegrationStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: IntegrationService = Depends(get_integration_service),
):
    items = await service.list_integrations(
        tenant_id,
        provider_type=provider_type,
        provider=provider,
        status=status,
        limit=limit,
        offset=offset,
    )
    total = await service.count_integrations(
        tenant_id,
        provider_type=provider_type,
        provider=provider,
        status=status,
    )
    return IntegrationPagination(
        items=[
            IntegrationOut.model_validate(i).model_dump()
            for i in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=IntegrationSummary)
async def integration_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: IntegrationService = Depends(get_integration_service),
):
    data = await service.summary(tenant_id)
    return IntegrationSummary(**data)


@router.get("/{integration_id}", response_model=IntegrationOut)
async def get_integration(
    integration_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: IntegrationService = Depends(get_integration_service),
):
    try:
        return await service.get(integration_id, tenant_id)
    except IntegrationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Create / Update (write — active subscription required)
# ---------------------------------------------------------------------------


@router.post("", dependencies=[_active_sub_guard], response_model=IntegrationOut)
async def create_integration(
    payload: IntegrationCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IntegrationService = Depends(get_integration_service),
):
    integration = await service.create(
        tenant_id,
        name=payload.name,
        provider_type=payload.provider_type,
        provider=payload.provider,
        description=payload.description,
        capabilities=payload.capabilities,
        configuration=payload.configuration,
        credential_reference=payload.credential_reference,
    )
    await db.commit()
    return integration


@router.patch("/{integration_id}", dependencies=[_active_sub_guard], response_model=IntegrationOut)
async def update_integration(
    integration_id: uuid.UUID,
    payload: IntegrationUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IntegrationService = Depends(get_integration_service),
):
    try:
        integration = await service.update(
            integration_id,
            tenant_id,
            name=payload.name,
            description=payload.description,
            status=payload.status,
            capabilities=payload.capabilities,
            configuration=payload.configuration,
            credential_reference=payload.credential_reference,
        )
    except IntegrationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return integration
