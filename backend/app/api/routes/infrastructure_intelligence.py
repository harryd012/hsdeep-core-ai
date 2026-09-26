"""
Universal Infrastructure Intelligence API (spec section 31).

Mounts under ``/api/infrastructure/intelligence`` so it sits BESIDE the existing
``/api/infrastructure`` contract instead of changing it.

Every endpoint:
  * resolves the tenant from the authenticated principal
    (``Depends(get_tenant_id)``) - never from a query parameter, so cross-tenant
    reads are structurally impossible;
  * requires the same read permission the rest of the infrastructure surface
    uses (``devices.read`` for resources/interfaces/telemetry, ``sites.read`` for
    site views, ``dashboard.read`` for tenant rollups);
  * paginates and caps its limits, and validates enum-style filters against the
    canonical vocabulary rather than silently ignoring unknown values.

Read-only. The single POST is a READ (a correlation-context query), mirroring
the existing ``/network-intelligence/evaluate`` convention.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.infrastructure_intelligence.resources import UniversalResource
from app.services.infrastructure_intelligence.service import (
    MAX_LIMIT,
    UniversalInfrastructureService,
)

router = APIRouter(
    prefix="/infrastructure/intelligence", tags=["universal-infrastructure"]
)


def get_service(db: AsyncSession = Depends(get_db)) -> UniversalInfrastructureService:
    return UniversalInfrastructureService(db)


def _validate(value: str | None, allowed: tuple[str, ...], field: str) -> None:
    if value is None:
        return
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"invalid {field}={value!r}; expected one of {sorted(allowed)}",
        )


def _not_found(kind: str, resource_id: uuid.UUID) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{kind} {resource_id} not found")


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------
@router.get(
    "/catalog",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_catalog(
    tenant_id: uuid.UUID = Depends(get_tenant_id),  # noqa: ARG001 - authz only
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Canonical capabilities, resource types, states and reasons."""
    return service.capability_catalog()


@router.get(
    "/providers",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_providers(
    tenant_id: uuid.UUID = Depends(get_tenant_id),  # noqa: ARG001
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Declared providers and which of them are actually wired."""
    return service.provider_catalog()


# ---------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------
@router.get(
    "/tenant/overview",
    dependencies=[Depends(require_permission("dashboard.read"))],
)
async def get_tenant_overview(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Tenant-wide resource/health/telemetry summary from persisted rows."""
    return await service.tenant_overview(tenant_id)


@router.get(
    "/tenant/health",
    dependencies=[Depends(require_permission("dashboard.read"))],
)
async def get_tenant_health(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    rollup = await service.health_service.tenant_health(tenant_id)
    return rollup.to_dict()


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------
@router.get(
    "/sites/{site_id}/overview",
    dependencies=[Depends(require_permission("sites.read"))],
)
async def get_site_overview(
    site_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    result = await service.site_overview(tenant_id, site_id)
    if not result.get("found"):
        raise _not_found("Site", site_id)
    return result


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
@router.get(
    "/resources",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_resources(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    family: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    lifecycle_state: str | None = Query(default=None),
    health: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Paginated universal resource list with capability-aware filters."""
    _validate(family, V.RESOURCE_FAMILIES, "family")
    _validate(lifecycle_state, V.LIFECYCLE_STATES, "lifecycle_state")
    _validate(health, V.HEALTH_STATES, "health")
    if resource_type is not None and V.universal_resource_type(
        resource_type
    ) == V.UNKNOWN_RESOURCE_TYPE:
        raise HTTPException(
            status_code=422, detail=f"unknown resource_type={resource_type!r}"
        )
    return await service.list_resources(
        tenant_id,
        site_id=site_id,
        resource_type=resource_type,
        family=family,
        provider=provider,
        lifecycle_state=lifecycle_state,
        health=health,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/resources/{resource_id}",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Universal resource overview (identity, location, health, capabilities)."""
    device = await service.get_resource(tenant_id, resource_id)
    if device is None:
        raise _not_found("Resource", resource_id)
    provider_by_source = await service._provider_by_source(tenant_id)
    resource = UniversalResource.from_device(
        device, provider=service._provider_of(device, provider_by_source)
    )
    health = await service.health_service.resource_health(tenant_id, device)
    capabilities = await service.resource_capabilities(tenant_id, resource_id)
    return {
        "resource": resource.to_dict(),
        "health": health.to_dict(),
        "capabilities": capabilities,
        # Kept OUT of `health` deliberately: health answers "how is it doing",
        # while this answers "how far do we trust what we know about it".
        "operational_state": await service.resource_operational_state(
            tenant_id, resource_id
        ),
    }


@router.get(
    "/resources/{resource_id}/operational-state",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_operational_state(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Identity stage, monitoring binding, telemetry freshness and topology state.

    Four separate answers, never one combined "status": a resource can be
    identified and unbound (no telemetry expected), or bound with expired
    telemetry (not an operational fact). ``blockers`` names the missing evidence
    that holds the resource at its current stage.
    """
    state = await service.resource_operational_state(tenant_id, resource_id)
    if state is None:
        raise _not_found("Resource", resource_id)
    return state


@router.get(
    "/resources/{resource_id}/capabilities",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_capabilities(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Capability report: supported / unsupported / unknown, each with a reason."""
    result = await service.resource_capabilities(tenant_id, resource_id)
    if not result.get("found"):
        raise _not_found("Resource", resource_id)
    return result


@router.get(
    "/resources/{resource_id}/health",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_health(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    device = await service.get_resource(tenant_id, resource_id)
    if device is None:
        raise _not_found("Resource", resource_id)
    health = await service.health_service.resource_health(tenant_id, device)
    return health.to_dict()


@router.get(
    "/resources/{resource_id}/interfaces",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_interfaces(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    result = await service.resource_interfaces(tenant_id, resource_id)
    if not result.get("found"):
        raise _not_found("Resource", resource_id)
    return result


@router.get(
    "/resources/{resource_id}/telemetry",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_telemetry(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    capability: str | None = Query(default=None),
    metric_name: str | None = Query(default=None, max_length=100),
    interface_id: uuid.UUID | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Bounded canonical telemetry: current values + summarised series."""
    if capability is not None and capability not in V.ALL_CAPABILITIES:
        raise HTTPException(
            status_code=422, detail=f"unknown capability={capability!r}"
        )
    result = await service.resource_telemetry(
        tenant_id,
        resource_id,
        capability=capability,
        metric_name=metric_name,
        interface_id=interface_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    if not result.get("found"):
        raise _not_found("Resource", resource_id)
    return result


@router.get(
    "/resources/{resource_id}/network",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_network(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """WAN / SD-WAN / VPN / routing / BGP for one resource."""
    result = await service.resource_network(tenant_id, resource_id)
    if not result.get("found"):
        raise _not_found("Resource", resource_id)
    return result


@router.get(
    "/resources/{resource_id}/dependencies",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_resource_dependencies(
    resource_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Dependency evidence with real health, as used by correlation/priority."""
    device = await service.get_resource(tenant_id, resource_id)
    if device is None:
        raise _not_found("Resource", resource_id)
    health = await service.health_service.resource_health(tenant_id, device)
    return {
        "resource_id": str(resource_id),
        "dependencies": health.dependencies,
        "count": len(health.dependencies),
    }


# ---------------------------------------------------------------------------
# Resource-type views (spec section 31: FIREWALL / SWITCH / ACCESS_POINT /
# SERVER / CLOUD / ENDPOINT / STORAGE)
#
# These are the SAME read model filtered by unified resource type - not separate
# implementations. Type-specific detail (interfaces / network / telemetry /
# dependencies) lives under /resources/{id}, so a firewall, a switch and an
# access point all expose the same contract.
# ---------------------------------------------------------------------------
async def _list_by_type(
    service: UniversalInfrastructureService,
    tenant_id: uuid.UUID,
    resource_type: str,
    *,
    site_id: uuid.UUID | None,
    provider: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> dict:
    result = await service.list_resources(
        tenant_id,
        site_id=site_id,
        resource_type=resource_type,
        provider=provider,
        search=search,
        limit=limit,
        offset=offset,
    )
    result["resource_type"] = V.universal_resource_type(resource_type)
    return result


@router.get(
    "/firewalls",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_firewalls(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    return await _list_by_type(
        service, tenant_id, "firewall",
        site_id=site_id, provider=provider, search=search,
        limit=limit, offset=offset,
    )


@router.get(
    "/switches",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_switches(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    return await _list_by_type(
        service, tenant_id, "switch",
        site_id=site_id, provider=provider, search=search,
        limit=limit, offset=offset,
    )


@router.get(
    "/access-points",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_access_points(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    return await _list_by_type(
        service, tenant_id, "access_point",
        site_id=site_id, provider=provider, search=search,
        limit=limit, offset=offset,
    )


@router.get(
    "/servers",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_servers(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    return await _list_by_type(
        service, tenant_id, "server",
        site_id=site_id, provider=provider, search=search,
        limit=limit, offset=offset,
    )


@router.get(
    "/cloud",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_cloud_resources(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    family: str = Query(default=V.FAMILY_CLOUD),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Cloud resources/services (family defaults to cloud; database is allowed)."""
    _validate(family, (V.FAMILY_CLOUD, V.FAMILY_DATABASE), "family")
    return await service.list_resources(
        tenant_id,
        site_id=site_id,
        family=family,
        provider=provider,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/endpoints",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_endpoints(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    return await service.list_resources(
        tenant_id,
        site_id=site_id,
        family=V.FAMILY_ENDPOINT,
        provider=provider,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/storage",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_storage(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(default=None),
    provider: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    return await service.list_resources(
        tenant_id,
        site_id=site_id,
        family=V.FAMILY_STORAGE,
        provider=provider,
        search=search,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# HSI-GNCE correlation context (spec section 28). A READ expressed as POST
# because the resource set is a body parameter - the same convention the
# existing /network-intelligence/evaluate uses.
# ---------------------------------------------------------------------------
class CorrelationContextIn(BaseModel):
    resource_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    include_dependencies: bool = True


@router.post(
    "/correlation/context",
    dependencies=[Depends(require_permission("devices.read"))],
)
async def post_correlation_context(
    payload: CorrelationContextIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: UniversalInfrastructureService = Depends(get_service),
) -> dict:
    """Normalized dependency/topology/alert context for graph-based correlation.

    Read-only: creates no incident and executes nothing. Resources outside the
    caller's tenant are simply absent from the result, so this cannot be used to
    probe another tenant.
    """
    if not payload.resource_ids:
        raise HTTPException(
            status_code=422, detail="resource_ids must contain at least one id"
        )
    return await service.correlation_context(
        tenant_id,
        payload.resource_ids,
        include_dependencies=payload.include_dependencies,
    )
