"""
Universal ITSM Framework API routes.

Provides endpoints for:
- ITSM Provider catalog (read-only)
- ITSM Connection management (CRUD + test)
- ITSM Sync job management
- Ticket operations
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.schemas.itsm import (
    ITSMConnectionCreate,
    ITSMConnectionOut,
    ITSMConnectionTestOut,
    ITSMConnectionUpdate,
    ITSMSyncJobOut,
)
from app.services.itsm.connector_manager import ConnectorManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/itsm", tags=["itsm"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

async def get_connector_manager(
    db: AsyncSession = Depends(get_db),
) -> ConnectorManager:
    """Provide a ConnectorManager instance for each request."""
    return ConnectorManager(db)


# ---------------------------------------------------------------------------
# ITSM Providers (system-level catalog)
# ---------------------------------------------------------------------------

@router.get("/providers", response_model=list[dict[str, Any]])
async def list_providers(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """List all registered ITSM providers."""
    registry = manager._registry
    return registry.list_providers()


# ---------------------------------------------------------------------------
# ITSM Connections
# ---------------------------------------------------------------------------

@router.get("/connections", response_model=list[ITSMConnectionOut])
async def list_connections(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """List all ITSM connections for the current tenant."""
    connections = await manager.list_connections(tenant_id)
    return [ITSMConnectionOut.model_validate(c) for c in connections]


@router.get("/connections/{conn_id}", response_model=ITSMConnectionOut)
async def get_connection(
    conn_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """Get a specific ITSM connection."""
    conn = await manager.get_connection(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return ITSMConnectionOut.model_validate(conn)


@router.post("/connections", dependencies=[_active_sub_guard], response_model=ITSMConnectionOut, status_code=201)
async def create_connection(
    payload: ITSMConnectionCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("itsm.write")),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """Create a new ITSM connection."""
    try:
        conn = await manager.create_connection(
            tenant_id=tenant_id,
            provider_type=payload.provider_type,
            name=payload.name,
            instance_url=payload.instance_url,
            auth_type=payload.auth_type,
            credentials=payload.credentials,
            rate_limit_rpm=payload.rate_limit_rpm,
            pull_enabled=payload.pull_enabled,
            config=payload.config,
            webhook_secret=payload.webhook_secret,
        )
        return ITSMConnectionOut.model_validate(conn)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to create ITSM connection")
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/connections/{conn_id}", dependencies=[_active_sub_guard], response_model=ITSMConnectionOut)
async def update_connection(
    conn_id: uuid.UUID,
    payload: ITSMConnectionUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("itsm.write")),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """Update an existing ITSM connection."""
    conn = await manager.get_connection(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        updated = await manager.update_connection_status(
            conn,
            name=payload.name,
            instance_url=payload.instance_url,
            auth_type=payload.auth_type,
            credentials=payload.credentials,
            rate_limit_rpm=payload.rate_limit_rpm,
            pull_enabled=payload.pull_enabled,
            config=payload.config,
            webhook_secret=payload.webhook_secret,
            is_active=payload.is_active,
        )
        return ITSMConnectionOut.model_validate(updated)
    except Exception as exc:
        logger.exception("Failed to update ITSM connection")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/connections/{conn_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_connection(
    conn_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("itsm.write")),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """Delete an ITSM connection (soft delete)."""
    conn = await manager.get_connection(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    await manager.delete_connection(conn)


@router.post("/connections/{conn_id}/test-connection", dependencies=[_active_sub_guard], response_model=ITSMConnectionTestOut)
async def test_connection(
    conn_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_permission("itsm.write")),
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """Test connectivity to the ITSM provider."""
    conn = await manager.get_connection(conn_id, tenant_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    result = await manager.test_connection(conn)
    return ITSMConnectionTestOut(
        connected=result.healthy,
        message=result.message,
        latency_ms=result.latency_ms,
        provider_type=result.provider_type,
    )


# ---------------------------------------------------------------------------
# ITSM Sync Jobs
# ---------------------------------------------------------------------------

@router.get("/sync-jobs", response_model=list[ITSMSyncJobOut])
async def list_sync_jobs(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    status: str | None = None,
    manager: ConnectorManager = Depends(get_connector_manager),
):
    """List ITSM sync jobs for the current tenant."""
    # For now, return an empty list since sync job listing is not fully implemented
    # in ConnectorManager. This stub prevents startup failure.
    return []