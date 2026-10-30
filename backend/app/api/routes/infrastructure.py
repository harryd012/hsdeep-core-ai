"""
Infrastructure API.

Powers the Infrastructure page's Site cards, Site detail view, and the
per-site paginated Device table.

NOTE(auth): same interim pattern as Monitoring/Alerts/Devices (see
monitoring.py, alerts.py, devices.py) - tenant_id is an explicit query
parameter on every endpoint because there is no authenticated-principal
dependency yet. Once the Auth module exists, tenant_id should be derived
from the current user/session instead.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, get_db
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.resource_relationship_repository import ResourceRelationshipRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.device import DeviceOut
from app.schemas.infrastructure_overview import InfrastructureOverviewOut
from app.schemas.pagination import Page
from app.schemas.site import SiteOut, SiteSummary
from app.schemas.topology_graph import TopologyGraphOut, TopologyQuickSummary
from app.schemas.topology_l3 import TopologyL3Out
from app.schemas.topology_live import TopologyLiveOut
from app.services.infrastructure_overview_service import InfrastructureOverviewService
from app.services.dependency_impact_service import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    MAX_ALLOWED_DEPTH,
    MAX_ALLOWED_NODES,
    DependencyImpactService,
    ResourceNotFound,
)
from app.services.infrastructure_service import InfrastructureService
from app.services.topology_graph_service import TopologyGraphService
from app.services.topology_l3_service import SiteNotFound, TopologyL3Service
from app.services.topology_live_service import TopologyLiveService
from app.services.topology_service import TopologyService
from app.schemas.topology import TopologyOut

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])


def get_device_repository(db: AsyncSession = Depends(get_db)) -> DeviceRepository:
    return DeviceRepository(db)


def get_site_repository(db: AsyncSession = Depends(get_db)) -> SiteRepository:
    return SiteRepository(db)


def get_infrastructure_service(db: AsyncSession = Depends(get_db)) -> InfrastructureService:
    return InfrastructureService(
        site_repo=SiteRepository(db),
        device_repo=DeviceRepository(db),
    )


def get_topology_service(db: AsyncSession = Depends(get_db)) -> TopologyService:
    return TopologyService(db)


def get_overview_service(
    db: AsyncSession = Depends(get_db),
) -> InfrastructureOverviewService:
    return InfrastructureOverviewService(db)


def get_topology_graph_service(
    db: AsyncSession = Depends(get_db),
) -> TopologyGraphService:
    return TopologyGraphService(db)


def get_topology_live_service(
    db: AsyncSession = Depends(get_db),
) -> TopologyLiveService:
    return TopologyLiveService(db)


def get_topology_l3_service(
    db: AsyncSession = Depends(get_db),
) -> TopologyL3Service:
    return TopologyL3Service(db)


def get_monitoring_source_repository(
    db: AsyncSession = Depends(get_db),
) -> MonitoringSourceRepository:
    return MonitoringSourceRepository(db)


def get_sensor_repository(
    db: AsyncSession = Depends(get_db),
) -> SensorRepository:
    return SensorRepository(db)


def get_interface_repository(
    db: AsyncSession = Depends(get_db),
) -> InterfaceRepository:
    return InterfaceRepository(db)


def get_resource_relationship_repository(
    db: AsyncSession = Depends(get_db),
) -> ResourceRelationshipRepository:
    return ResourceRelationshipRepository(db)


@router.get("/topology/summary", response_model=TopologyQuickSummary)
async def get_topology_quick_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Fast aggregate topology summary — six COUNT queries, no row materialisation.

    All counts are fired concurrently via asyncio.gather(), so the endpoint
    resolves in well under 500ms even on large tenants.  Never calls
    TopologyGraphService.build() — the full graph is only needed when the
    caller explicitly requests the /topology/graph endpoint.

    SESSION SAFETY: each count runs on its OWN short-lived session drawn from
    the shared engine pool. FastAPI caches `get_db` once per request, so all
    request-scoped repositories share a single AsyncSession — and gathering
    concurrent operations on one AsyncSession is illegal in SQLAlchemy (it
    raises IllegalStateChangeError: "_connection_for_bind() is already in
    progress"). Separate pooled sessions keep the queries concurrent AND safe.

    by_health maps the raw collector-reported status strings to the canonical
    four-bucket vocabulary (healthy / warning / critical / unknown) using the
    same device_health_bucket() function the Infrastructure Overview uses,
    so the numbers here agree with the overview dashboard KPIs.
    """
    from app.services.infrastructure_overview_service import device_health_bucket

    async def _count(run):
        """One COUNT = one short-lived session (never the shared request one)."""
        async with AsyncSessionLocal() as session:
            return await run(session)

    (
        devices_total,
        sites_total,
        sources_total,
        relationships_total,
        interfaces_total,
        sensors_total,
        status_counts,
        devices_linked,
    ) = await asyncio.gather(
        _count(lambda s: DeviceRepository(s).count(tenant_id)),
        _count(lambda s: SiteRepository(s).count(tenant_id)),
        _count(lambda s: MonitoringSourceRepository(s).count(tenant_id)),
        _count(lambda s: ResourceRelationshipRepository(s).count(tenant_id)),
        _count(lambda s: InterfaceRepository(s).count_for_tenant(tenant_id)),
        _count(lambda s: SensorRepository(s).count_for_tenant(tenant_id)),
        _count(lambda s: DeviceRepository(s).counts_by_status(tenant_id)),
        _count(lambda s: ResourceRelationshipRepository(s).count_linked_devices(tenant_id)),
    )

    # Map raw status strings → canonical 4-bucket health vocabulary.
    by_health: dict[str, int] = {}
    for raw_status, count in status_counts.items():
        bucket = device_health_bucket(raw_status)
        by_health[bucket] = by_health.get(bucket, 0) + count

    return TopologyQuickSummary(
        tenant_id=str(tenant_id),
        generated_at=datetime.now(timezone.utc).isoformat(),
        devices_total=devices_total,
        sites_total=sites_total,
        monitoring_sources_total=sources_total,
        relationships_total=relationships_total,
        devices_linked=devices_linked,
        interfaces_total=interfaces_total,
        sensors_total=sensors_total,
        by_health=by_health,
        status="ok" if devices_total > 0 else "empty",
    )


@router.get("/topology/graph", response_model=TopologyGraphOut)
async def get_topology_graph(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    node_type: str | None = Query(default=None),
    site_id: uuid.UUID | None = Query(default=None),
    service: TopologyGraphService = Depends(get_topology_graph_service),
):
    """Node/edge topology graph built ONLY from real rows.

    Deliberately mounted at `/topology/graph` rather than `/topology`: the
    existing `/infrastructure/topology` endpoint already owns that path and
    returns a different contract (a site tree, `TopologyOut`), which the
    current Infrastructure Topology page consumes. Adding the graph beside it
    preserves that contract instead of silently changing it.

    Every edge comes from a `ResourceRelationship` row. When none exist the
    response reports `status: "not_discovered"` with `limitations` explaining
    why - it never synthesises an edge from subnets, hostnames or device types.

    Pagination via `limit`/`offset` bounds the device list; `node_type` and
    `site_id` filter nodes before pagination is applied.  The response includes
    a `pagination` envelope with `total_devices` so the UI can calculate pages.
    """
    return await service.build(
        tenant_id,
        limit=limit,
        offset=offset,
        node_type=node_type,
        site_id=site_id,
    )


@router.get("/topology/graph/live", response_model=TopologyLiveOut)
async def get_topology_live_graph(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(
        default=None,
        description="Bound the view to one site (the site node plus its members).",
    ),
    service: TopologyLiveService = Depends(get_topology_live_service),
):
    """The live operational topology: the graph PLUS interface/telemetry facts.

    Composed in ONE response so the UI never fans out into a per-device
    interface/telemetry call (an N+1 that grows with the graph).

    Every added value is a stored column - interface oper/admin status, speed,
    rx/tx bps, errors, discards, utilisation, timestamps - and anything never
    measured stays `null`, so "no traffic" and "no telemetry" cannot be confused.
    It reuses the existing graph builder, availability mapping and freshness
    thresholds, so there is no second graph, health or telemetry engine.
    """
    return await service.build(tenant_id, site_id=site_id)


@router.get("/topology/l3", response_model=TopologyL3Out)
async def get_topology_l3(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = Query(
        default=None,
        description="Bound the view to one site's devices. 404 if the site is not in this tenant.",
    ),
    service: TopologyL3Service = Depends(get_topology_l3_service),
):
    """The L3 (routed) topology: devices joined by shared subnets and routes.

    Built only from stored interface addresses and routing-table entries that
    collectors read from the devices. Subnets are grouped per site and VRF, so
    the same private range at two customer sites never merges. Subnets with
    three or more devices are returned as pipe nodes; static/default-route next
    hops that are not a known device are returned as next-hop nodes; ``roots``
    are the default-route exits. ``coverage`` says how many devices actually
    reported L3 data, so an empty map is never mistaken for "no subnets".
    """
    try:
        return await service.build(tenant_id, site_id=site_id)
    except SiteNotFound:
        raise HTTPException(status_code=404, detail="Site not found")


@router.get("/overview", response_model=InfrastructureOverviewOut)
async def get_infrastructure_overview(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: InfrastructureOverviewService = Depends(get_overview_service),
):
    """Canonical infrastructure overview - the single aggregation authority.

    Every total here is derived once, from existing repositories, so the
    Dashboard, Infrastructure, Topology and Reports pages can consume the same
    numbers instead of each re-counting and disagreeing.
    """
    return await service.build(tenant_id)


@router.get("/sites", response_model=list[SiteSummary])
async def list_site_summaries(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: InfrastructureService = Depends(get_infrastructure_service),
):
    return await service.list_site_summaries(tenant_id)


@router.get("/sites/{site_id}", response_model=SiteOut)
async def get_site(
    site_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: InfrastructureService = Depends(get_infrastructure_service),
):
    site = await service.get_site_detail(site_id, tenant_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")
    return site


@router.get("/sites/{site_id}/devices", response_model=Page[DeviceOut])
async def list_site_devices(
    site_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    device_type: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    search: str | None = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    site_repo: SiteRepository = Depends(get_site_repository),
    device_repo: DeviceRepository = Depends(get_device_repository),
):
    # 404 first, to enforce tenant isolation before exposing device counts
    # for a site that doesn't belong to this tenant.
    site = await site_repo.get(site_id, tenant_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    items = await device_repo.list(
        tenant_id,
        site_id=site_id,
        device_type=device_type,
        vendor=vendor,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    total = await device_repo.count(
        tenant_id,
        site_id=site_id,
        device_type=device_type,
        vendor=vendor,
        status=status,
        search=search,
    )
    return Page[DeviceOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/topology", response_model=TopologyOut)
async def get_topology(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: TopologyService = Depends(get_topology_service),
):
    """Return a tenant-scoped infrastructure topology tree.

    Uses only repository-backed services; honest empty states are returned
    when the tenant has no sites/sources/devices.
    """
    return await service.tenant_topology(tenant_id)


@router.get("/dependencies/{resource_id}")
async def dependency_impact_view(
    resource_id: uuid.UUID,
    max_depth: int = Query(default=DEFAULT_MAX_DEPTH, ge=1, le=MAX_ALLOWED_DEPTH),
    max_nodes: int = Query(default=DEFAULT_MAX_NODES, ge=1, le=MAX_ALLOWED_NODES),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Dependencies and impact for one resource, from RECORDED evidence.

    Tenant-scoped through `get_tenant_id`; the service reads only
    `resource_relationships` and `devices` rows for that tenant, performs no
    network access and needs no credential. Physical adjacency is reported under
    `impact` only - it is never promoted to a service dependency - and every hop
    carries its evidence source, freshness, resolution and conflict state so the
    answer can be justified. Bounded by `max_depth` / `max_nodes`.
    """
    try:
        return await DependencyImpactService(db).analyze(
            tenant_id, resource_id, max_depth=max_depth, max_nodes=max_nodes
        )
    except ResourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
