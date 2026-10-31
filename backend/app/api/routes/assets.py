"""
Asset Intelligence / CMDB API.

All endpoints are tenant-scoped. tenant_id is derived from the authenticated
principal via Depends(get_tenant_id), matching the pattern in priorities.py
and runbooks.py. The /tenants/{tenant_id}/health endpoint validates the path
param against the session's tenant_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.asset_relationship import AssetRelationship
from app.models.device import Device
from app.models.site import Site
from app.models.business_service import BusinessService
from app.repositories.device_repository import DeviceRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.asset import (
    AssetHealthSummary,
    AssetRelationshipOut,
    AssetSearchParams,
    DeviceCMDBOut,
    SiteCMDBOut,
    TenantHealthSummary,
)
from app.models.alert import Alert
from app.models.sensor import Sensor
from app.services.health_score import (
    HealthScopeNotFound,
    business_service_health_rollup,
    tenant_health_rollup,
)
from app.services.dependency_graph import (
    get_dependency_chain,
    get_impact_analysis,
    get_full_graph,
)

router = APIRouter(prefix="/assets", tags=["assets"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
# ---------------------------------------------------------------------------
# Devices (full CMDB view)
# ---------------------------------------------------------------------------

@router.get("/devices", response_model=list[DeviceCMDBOut])
async def list_devices(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_type: str | None = None,
    status: str | None = None,
    environment: str | None = None,
    criticality: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List devices with CMDB fields. All filters are tenant-scoped."""
    stmt = select(Device).where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
    if site_id is not None:
        stmt = stmt.where(Device.site_id == site_id)
    if device_type is not None:
        stmt = stmt.where(Device.device_type == device_type)
    if status is not None:
        stmt = stmt.where(Device.status == status)
    if environment is not None:
        stmt = stmt.where(Device.environment == environment)
    if criticality is not None:
        stmt = stmt.where(Device.criticality == criticality)
    if tag is not None:
        # JSONB contains operator - matches if tag is in the array
        stmt = stmt.where(Device.tags.contains([tag]))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                Device.name.ilike(like),
                Device.hostname.ilike(like),
                Device.ip_address.ilike(like),
                Device.serial_number.ilike(like),
            )
        )
    stmt = stmt.order_by(Device.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/devices/{device_id}", response_model=DeviceCMDBOut)
async def get_device(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    device = (
        await db.execute(
            select(Device).where(Device.id == device_id, Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return device


# ---------------------------------------------------------------------------
# Sites (CMDB view)
# ---------------------------------------------------------------------------

@router.get("/sites", response_model=list[SiteCMDBOut])
async def list_sites(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    region: str | None = None,
    building: str | None = None,
    criticality: str | None = None,
    search: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Site).where(Site.tenant_id == tenant_id, Site.is_deleted.is_(False))
    if region is not None:
        stmt = stmt.where(Site.region == region)
    if building is not None:
        stmt = stmt.where(Site.building == building)
    if criticality is not None:
        stmt = stmt.where(Site.criticality == criticality)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Site.name.ilike(like), Site.code.ilike(like), Site.address.ilike(like)))
    stmt = stmt.order_by(Site.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/sites/{site_id}", response_model=SiteCMDBOut)
async def get_site(
    site_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    site = (
        await db.execute(
            select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id, Site.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")
    return site


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

@router.get("/devices/{device_id}/relationships", response_model=list[AssetRelationshipOut])
async def get_device_relationships(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    relationship_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get all relationships for a device (outgoing and incoming)."""
    stmt = select(AssetRelationship).where(AssetRelationship.tenant_id == tenant_id)
    stmt = stmt.where(
        or_(
            AssetRelationship.asset_id == device_id,
            AssetRelationship.related_asset_id == device_id,
        )
    )
    if relationship_type is not None:
        stmt = stmt.where(AssetRelationship.relationship_type == relationship_type)
    result = await db.execute(stmt.order_by(AssetRelationship.created_at.desc()))
    return list(result.scalars().all())


class RelationshipCreate(BaseModel):
    """Inline schema for creating asset relationships."""
    asset_id: uuid.UUID
    related_asset_id: uuid.UUID
    relationship_type: str
    label: str | None = None
    weight: float | None = None


@router.post("/relationships", dependencies=[_active_sub_guard], response_model=AssetRelationshipOut, status_code=201)
async def create_relationship(
    payload: RelationshipCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("tickets.write")),
    db: AsyncSession = Depends(get_db),
):
    """Create an asset relationship."""
    rel = AssetRelationship(
        tenant_id=tenant_id,
        asset_id=payload.asset_id,
        related_asset_id=payload.related_asset_id,
        relationship_type=payload.relationship_type,
        label=payload.label,
        weight=payload.weight,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return rel


@router.get("/business-services/{service_id}/health", response_model=AssetHealthSummary)
async def get_business_service_health(
    service_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        rollup = await business_service_health_rollup(db, service_id, tenant_id)
    except HealthScopeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business service not found")
    return AssetHealthSummary(
        scope="business_service",
        scope_id=service_id,
        health_score=rollup.score,
        status=rollup.status,
        details={"devices_scored": rollup.scored, "devices_total": rollup.total},
    )


@router.get("/tenants/{tenant_id}/health", response_model=TenantHealthSummary)
async def get_tenant_health(
    tenant_id: uuid.UUID,
    session_tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Tenant health summary. The path tenant_id is validated against the
    authenticated principal's tenant_id (404 on mismatch)."""
    if tenant_id != session_tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    rollup = await tenant_health_rollup(db, tenant_id)
    from sqlalchemy import func
    sites_count = (
        await db.execute(select(func.count()).where(Site.tenant_id == tenant_id, Site.is_deleted.is_(False)))
    ).scalar_one()
    devices_count = (
        await db.execute(select(func.count()).where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False)))
    ).scalar_one()
    sensors_count = (
        await db.execute(select(func.count()).where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False)))
    ).scalar_one()
    alerts_active = (
        await db.execute(
            select(func.count()).where(
                Alert.tenant_id == tenant_id, Alert.status != "resolved", Alert.is_deleted.is_(False)
            )
        )
    ).scalar_one()
    return TenantHealthSummary(
        tenant_id=tenant_id,
        health_score=rollup.score,
        status=rollup.status,
        devices_scored=rollup.scored,
        sites_count=sites_count,
        devices_count=devices_count,
        sensors_count=sensors_count,
        alerts_active=alerts_active,
    )


# ---------------------------------------------------------------------------
# Dependency Graph (Sprint 16)
# ---------------------------------------------------------------------------

class GraphResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    root_id: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


@router.get("/devices/{device_id}/dependency-chain", response_model=GraphResponse)
async def get_dependency_chain_endpoint(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    max_depth: int = Query(default=5, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
):
    """What does this device depend on? (upstream dependencies)."""
    result = await get_dependency_chain(db, tenant_id, device_id, max_depth=max_depth)
    return GraphResponse(**result)


@router.get("/devices/{device_id}/impact-analysis", response_model=GraphResponse)
async def get_impact_analysis_endpoint(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    max_depth: int = Query(default=5, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
):
    """What breaks if this device fails? (downstream impact)."""
    result = await get_impact_analysis(db, tenant_id, device_id, max_depth=max_depth)
    return GraphResponse(**result)


@router.get("/devices/{device_id}/full-graph", response_model=GraphResponse)
async def get_full_graph_endpoint(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    max_depth: int = Query(default=5, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
):
    """All connected assets regardless of direction."""
    result = await get_full_graph(db, tenant_id, device_id, max_depth=max_depth)
    return GraphResponse(**result)