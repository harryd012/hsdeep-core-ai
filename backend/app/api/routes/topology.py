"""
Topology API — resource relationship CRUD and graph traversal.

Tenant-scoped. Reads follow the interim pattern (tenant derived from the
JWT principal); writes additionally require an active subscription.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, get_tenant_id
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.topology_link_repository import TopologyLinkRepository
from app.repositories.user_repository import UserRepository
from app.schemas.topology_link import (
    TopologyLinkCreate,
    TopologyLinkPagination,
    TopologyLinkRead,
    TopologyLinkSummary,
    TopologyLinkUpdate,
)
from app.schemas.relationship_materialization import RelationshipMaterializationOut
from app.schemas.topology import (
    VALID_RELATIONSHIP_STATUSES,
    VALID_RELATIONSHIP_TYPES,
    RelationshipTypeCount,
    ResourceRelationshipCreate,
    ResourceRelationshipPagination,
    ResourceRelationshipRead,
    ResourceRelationshipUpdate,
    TopologyDependencyNode,
    TopologyImpactOut,
    TopologyNeighborsOut,
    TopologySummary,
)
from app.services.audit_service import AuditService
from app.services.relationship_evidence_service import RelationshipEvidenceService
from app.services.topology_service import TopologyService

router = APIRouter(prefix="/topology", tags=["topology"])


def get_relationship_repo(db: AsyncSession = Depends(get_db)) -> ResourceRelationshipRepository:
    return ResourceRelationshipRepository(db)


def get_topology_service(db: AsyncSession = Depends(get_db)) -> TopologyService:
    return TopologyService(db)


def get_link_repo(db: AsyncSession = Depends(get_db)) -> TopologyLinkRepository:
    return TopologyLinkRepository(db)


def _relationship_audit_values(edge) -> dict:
    """The recorded fields that explain one graph edge, for the audit trail.

    Every value is a stored column on the row itself, so an audit record can never
    assert more than the relationship does. Nothing derived, nothing inferred and
    no secrets.
    """
    return {
        "relationship_type": edge.relationship_type,
        "source_resource_id": str(edge.source_resource_id),
        "target_resource_id": str(edge.target_resource_id),
        "discovered_by": edge.discovered_by,
        "confidence": edge.confidence,
        "status": edge.status,
        "is_inferred": edge.is_inferred,
    }


async def _resolve_audit_actor(
    db: AsyncSession, principal: Principal
) -> tuple[uuid.UUID | None, str]:
    """Resolve the audited actor against ``users`` before it is used as an FK.

    ``audit_events.actor_id`` is a foreign key to ``users.id``, but
    ``core.auth.get_current_user`` resolves the principal from its claims with NO
    database round-trip. A principal's ``user_id`` is therefore not guaranteed to
    be a real row (a deleted user, an MSP tenant override, an API-key principal).
    Inserting an unknown id raises an IntegrityError, and because a failed flush
    inside the audit SAVEPOINT still leaves the ORM object pending, that error
    poisons the caller's transaction and turns a graph write into a 500.

    So the actor is recorded as an FK only when the row exists in THIS tenant; the
    email is always kept, and the caller preserves the raw claim in the event
    context. This is the smallest change that keeps the audited actor trustworthy
    without altering the shared AuditService for every other caller.
    """
    user = await UserRepository(db).get_by_id(principal.user_id, principal.tenant_id)
    return (user.id if user is not None else None), principal.email


# ---------------------------------------------------------------------------
# Relationship CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/relationships",
    response_model=ResourceRelationshipRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_active_subscription)],
)
async def create_relationship(
    payload: ResourceRelationshipCreate,
    principal: Principal = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: ResourceRelationshipRepository = Depends(get_relationship_repo),
    db: AsyncSession = Depends(get_db),
):
    if payload.relationship_type not in VALID_RELATIONSHIP_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid relationship_type {payload.relationship_type!r}. "
            f"Allowed: {', '.join(VALID_RELATIONSHIP_TYPES)}",
        )
    if payload.status not in VALID_RELATIONSHIP_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status {payload.status!r}. "
            f"Allowed: {', '.join(VALID_RELATIONSHIP_STATUSES)}",
        )
    if payload.source_resource_id == payload.target_resource_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_resource_id and target_resource_id must differ",
        )
    edge, created = await repo.upsert(
        tenant_id,
        source_resource_id=payload.source_resource_id,
        target_resource_id=payload.target_resource_id,
        relationship_type=payload.relationship_type,
        relationship_subtype=payload.relationship_subtype,
        label=payload.label,
        weight=payload.weight,
        confidence=payload.confidence,
        discovered_by=payload.discovered_by,
        status=payload.status,
        attributes=payload.attributes,
    )
    # Audit through the EXISTING AuditService (best-effort: it can never break the
    # write). A first-time insert and an idempotent re-discovery refresh are
    # distinct events, so they get distinct actions - the unique constraint means
    # a repeated POST refreshes last_seen instead of creating a duplicate edge.
    actor_id, actor_email = await _resolve_audit_actor(db, principal)
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="topology_relationship.created" if created else "topology_relationship.refreshed",
        actor_id=actor_id,
        actor_email=actor_email,
        entity_type="resource_relationship",
        entity_id=edge.id,
        description=(
            f"Relationship {edge.relationship_type!r} "
            f"{edge.source_resource_id} -> {edge.target_resource_id} "
            f"{'created' if created else 'refreshed by re-discovery'}"
        ),
        new_values=_relationship_audit_values(edge),
        context={"actor_user_id": str(principal.user_id)},
    )
    await db.commit()
    return edge


@router.post(
    "/relationships/materialize",
    response_model=RelationshipMaterializationOut,
    dependencies=[Depends(require_active_subscription)],
)
async def materialize_relationships(
    dry_run: bool = Query(
        default=True,
        description="Report what would be written without writing anything.",
    ),
    principal: Principal = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Convert ALREADY-RECORDED evidence into relationship rows.

    Passive and non-invasive: no probing, no scanning, no inference. The sources
    are recorded structure only (``devices.parent_resource_id`` -> ``contains``,
    ``bgp_neighbors`` -> ``peers_with``). An endpoint that does not resolve to a
    device in this tenant produces NO edge, and the report explains why any
    source contributed nothing.

    Defaults to a dry run so the plan can be inspected before anything is
    written.
    """
    service = RelationshipEvidenceService(db)
    report = await service.materialize(tenant_id, dry_run=dry_run)
    # Materialisation is a graph WRITE path, so it is audited through the same
    # existing AuditService as the manual relationship routes. Only counts the
    # report actually produced are recorded - a dry run records that nothing was
    # written.
    actor_id, actor_email = await _resolve_audit_actor(db, principal)
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="topology_relationships.materialized",
        actor_id=actor_id,
        actor_email=actor_email,
        entity_type="resource_relationship",
        entity_id=None,
        description=(
            "Relationship materialisation plan inspected (dry run, nothing written)"
            if dry_run
            else "Recorded evidence materialised into relationships"
        ),
        new_values={
            "dry_run": bool(report.get("dry_run", dry_run)),
            "planned_total": report.get("planned_total", 0),
            "written_total": report.get("written_total", 0),
            "refreshed_total": report.get("refreshed_total", 0),
            "unresolved_total": report.get("unresolved_total", 0),
            "skipped_total": report.get("skipped_total", 0),
        },
        context={"actor_user_id": str(principal.user_id)},
    )
    # get_db() does not commit; every write route in this module commits itself.
    # Without this the materialised edges would be rolled back on request exit.
    await db.commit()
    return report


@router.get("/relationships", response_model=ResourceRelationshipPagination)
async def list_relationships(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    source_resource_id: uuid.UUID | None = Query(None),
    target_resource_id: uuid.UUID | None = Query(None),
    relationship_type: str | None = Query(None),
    edge_status: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    repo: ResourceRelationshipRepository = Depends(get_relationship_repo),
):
    items = await repo.list(
        tenant_id,
        source_resource_id=source_resource_id,
        target_resource_id=target_resource_id,
        relationship_type=relationship_type,
        status=edge_status,
        limit=limit,
        offset=offset,
    )
    total = await repo.count(
        tenant_id,
        relationship_type=relationship_type,
        status=edge_status,
    )
    return ResourceRelationshipPagination(
        total=total, limit=limit, offset=offset,
        items=[ResourceRelationshipRead.model_validate(e) for e in items],
    )


@router.get("/summary", response_model=TopologySummary)
async def topology_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: ResourceRelationshipRepository = Depends(get_relationship_repo),
):
    data = await repo.summary(tenant_id)
    return TopologySummary(
        tenant_id=tenant_id,
        total_relationships=data["total"],
        by_type=[RelationshipTypeCount(**t) for t in data["by_type"]],
        by_status=data["by_status"],
    )


# ---------------------------------------------------------------------------
# Topology links (Section 17: physical/logical transport links, WAN, MPLS,
# VPN, OSFP/BGP/VRF attributes; wired into the dashboard topology view, 22)
# ---------------------------------------------------------------------------


@router.post(
    "/links",
    response_model=TopologyLinkRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_active_subscription)],
)
async def create_link(
    payload: TopologyLinkCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: TopologyLinkRepository = Depends(get_link_repo),
    db: AsyncSession = Depends(get_db),
):
    if payload.device_a_id == payload.device_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="device_a_id and device_b_id must differ",
        )
    problems = payload.validate_vocabulary()
    if problems:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(problems),
        )
    link, _created = await repo.upsert(
        tenant_id,
        device_a_id=payload.device_a_id,
        device_b_id=payload.device_b_id,
        name=payload.name,
        link_type=payload.link_type,
        source_interface_id=payload.source_interface_id,
        dest_interface_id=payload.dest_interface_id,
        provider=payload.provider,
        circuit_id=payload.circuit_id,
        external_id=payload.external_id,
        bandwidth_mbps=payload.bandwidth_mbps,
        latency_ms=payload.latency_ms,
        packet_loss_pct=payload.packet_loss_pct,
        utilization_pct=payload.utilization_pct,
        admin_status=payload.admin_status,
        oper_status=payload.oper_status,
        routing_protocols=payload.routing_protocols,
        ospf=payload.ospf,
        bgp=payload.bgp,
        vrfs=payload.vrfs,
        vpn_name=payload.vpn_name,
        vpn_type=payload.vpn_type,
        redundancy_group=payload.redundancy_group,
        is_redundant=payload.is_redundant,
        discovered_by=payload.discovered_by,
        attributes=payload.attributes,
    )
    await db.commit()
    return link


@router.get("/links", response_model=TopologyLinkPagination)
async def list_links(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: TopologyLinkRepository = Depends(get_link_repo),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    link_type: str | None = Query(None),
    oper_status: str | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
):
    items, total = await repo.list(
        tenant_id,
        limit=limit,
        offset=offset,
        link_type=link_type,
        oper_status=oper_status,
        device_id=device_id,
    )
    return TopologyLinkPagination(
        total=total,
        limit=limit,
        offset=offset,
        items=[TopologyLinkRead.model_validate(l) for l in items],
    )


@router.get("/links/summary", response_model=TopologyLinkSummary)
async def link_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: TopologyLinkRepository = Depends(get_link_repo),
):
    data = await repo.summary(tenant_id)
    return TopologyLinkSummary(
        tenant_id=tenant_id,
        total=data["total"],
        up=data["up"],
        down=data["down"],
        by_type=data["by_type"],
        by_status=data["by_status"],
    )


@router.get("/links/{link_id}", response_model=TopologyLinkRead)
async def get_link(
    link_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: TopologyLinkRepository = Depends(get_link_repo),
):
    link = await repo.get(link_id, tenant_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    return link


@router.patch(
    "/links/{link_id}",
    response_model=TopologyLinkRead,
    dependencies=[Depends(require_active_subscription)],
)
async def update_link(
    link_id: uuid.UUID,
    payload: TopologyLinkUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: TopologyLinkRepository = Depends(get_link_repo),
    db: AsyncSession = Depends(get_db),
):
    link = await repo.get(link_id, tenant_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    link = await repo.update(
        link,
        name=payload.name,
        link_type=payload.link_type,
        provider=payload.provider,
        circuit_id=payload.circuit_id,
        bandwidth_mbps=payload.bandwidth_mbps,
        latency_ms=payload.latency_ms,
        packet_loss_pct=payload.packet_loss_pct,
        utilization_pct=payload.utilization_pct,
        admin_status=payload.admin_status,
        oper_status=payload.oper_status,
        routing_protocols=payload.routing_protocols,
        ospf=payload.ospf,
        bgp=payload.bgp,
        vrfs=payload.vrfs,
        vpn_name=payload.vpn_name,
        vpn_type=payload.vpn_type,
        redundancy_group=payload.redundancy_group,
        is_redundant=payload.is_redundant,
        attributes=payload.attributes,
    )
    await db.commit()
    return link


@router.delete(
    "/links/{link_id}",
    dependencies=[Depends(require_active_subscription)],
)
async def delete_link(
    link_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: TopologyLinkRepository = Depends(get_link_repo),
    db: AsyncSession = Depends(get_db),
):
    link = await repo.get(link_id, tenant_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    await repo.delete(link)
    await db.commit()
    return {"status": "deleted"}


@router.get("/relationships/{relationship_id}", response_model=ResourceRelationshipRead)
async def get_relationship(
    relationship_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: ResourceRelationshipRepository = Depends(get_relationship_repo),
):
    edge = await repo.get(relationship_id, tenant_id)
    if edge is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return edge


@router.patch(
    "/relationships/{relationship_id}",
    response_model=ResourceRelationshipRead,
    dependencies=[Depends(require_active_subscription)],
)
async def update_relationship(
    relationship_id: uuid.UUID,
    payload: ResourceRelationshipUpdate,
    principal: Principal = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: ResourceRelationshipRepository = Depends(get_relationship_repo),
    db: AsyncSession = Depends(get_db),
):
    edge = await repo.get(relationship_id, tenant_id)
    if edge is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    if payload.status is not None and payload.status not in VALID_RELATIONSHIP_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status {payload.status!r}. "
            f"Allowed: {', '.join(VALID_RELATIONSHIP_STATUSES)}",
        )
    # Captured BEFORE the mutation so the audit record carries the real before/after
    # pair rather than only the new state.
    before = _relationship_audit_values(edge)
    edge = await repo.update(
        edge,
        relationship_subtype=payload.relationship_subtype,
        label=payload.label,
        weight=payload.weight,
        confidence=payload.confidence,
        status=payload.status,
        attributes=payload.attributes,
    )
    actor_id, actor_email = await _resolve_audit_actor(db, principal)
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="topology_relationship.updated",
        actor_id=actor_id,
        actor_email=actor_email,
        entity_type="resource_relationship",
        entity_id=edge.id,
        description=(
            f"Relationship {edge.relationship_type!r} "
            f"{edge.source_resource_id} -> {edge.target_resource_id} updated"
        ),
        old_values=before,
        new_values=_relationship_audit_values(edge),
        context={"actor_user_id": str(principal.user_id)},
    )
    await db.commit()
    return edge


@router.delete(
    "/relationships/{relationship_id}",
    dependencies=[Depends(require_active_subscription)],
)
async def delete_relationship(
    relationship_id: uuid.UUID,
    principal: Principal = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    repo: ResourceRelationshipRepository = Depends(get_relationship_repo),
    db: AsyncSession = Depends(get_db),
):
    edge = await repo.get(relationship_id, tenant_id)
    if edge is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    before = _relationship_audit_values(edge)
    await repo.delete(edge)
    actor_id, actor_email = await _resolve_audit_actor(db, principal)
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="topology_relationship.deleted",
        actor_id=actor_id,
        actor_email=actor_email,
        entity_type="resource_relationship",
        entity_id=edge.id,
        description=(
            f"Relationship {edge.relationship_type!r} "
            f"{edge.source_resource_id} -> {edge.target_resource_id} soft-deleted"
        ),
        old_values=before,
        context={"actor_user_id": str(principal.user_id)},
    )
    await db.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------


def _edge_dict(rel, source_id: uuid.UUID, target_id: uuid.UUID,
               relationship_type: str, depth: int) -> dict:
    return {
        "source_device_id": str(source_id),
        "target_device_id": str(target_id),
        "relationship_type": relationship_type,
    }


@router.get("/devices/{device_id}/upstream", response_model=TopologyImpactOut)
async def device_upstream(
    device_id: uuid.UUID,
    max_depth: int = Query(5, ge=1, le=10),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    topology: TopologyService = Depends(get_topology_service),
):
    """What does this device depend on? (root-cause direction)."""
    raw = await topology.get_upstream_dependencies(device_id, tenant_id, max_depth)
    edges = [
        {
            "source_device_id": e["source_device_id"],
            "target_device_id": e["target_device_id"],
            "relationship_type": e["relationship_type"],
        }
        for e in raw
    ]
    seen: dict[str, TopologyDependencyNode] = {}
    for e in raw:
        did = e["dependency_device_id"]
        if did not in seen or e["depth"] < seen[did].depth:
            seen[did] = TopologyDependencyNode(
                device_id=uuid.UUID(did),
                device_name=e.get("dependency_device_name"),
                relationship_type=e["relationship_type"],
                depth=e["depth"],
            )
    return TopologyImpactOut(
        tenant_id=tenant_id,
        device_id=device_id,
        direction="upstream",
        max_depth=max_depth,
        affected=list(seen.values()),
        edges=edges,
    )


@router.get("/devices/{device_id}/downstream", response_model=TopologyImpactOut)
async def device_downstream(
    device_id: uuid.UUID,
    max_depth: int = Query(5, ge=1, le=10),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    topology: TopologyService = Depends(get_topology_service),
):
    """What depends on this device? (blast-radius / impact view)."""
    raw = await topology.get_downstream_impact(device_id, tenant_id, max_depth)
    edges = [
        {
            "source_device_id": e["source_device_id"],
            "target_device_id": e["target_device_id"],
            "relationship_type": e["relationship_type"],
        }
        for e in raw
    ]
    seen: dict[str, TopologyDependencyNode] = {}
    for e in raw:
        did = e["dependent_device_id"]
        if did not in seen or e["depth"] < seen[did].depth:
            seen[did] = TopologyDependencyNode(
                device_id=uuid.UUID(did),
                device_name=e.get("dependent_device_name"),
                relationship_type=e["relationship_type"],
                depth=e["depth"],
            )
    return TopologyImpactOut(
        tenant_id=tenant_id,
        device_id=device_id,
        direction="downstream",
        max_depth=max_depth,
        affected=list(seen.values()),
        edges=edges,
    )


@router.get("/devices/{device_id}/connected", response_model=TopologyNeighborsOut)
async def device_connected(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    topology: TopologyService = Depends(get_topology_service),
):
    """Direct neighbors of this device."""
    raw = await topology.get_connected_devices(device_id, tenant_id)
    return TopologyNeighborsOut(
        tenant_id=tenant_id,
        device_id=device_id,
        neighbors=[
            TopologyDependencyNode(
                device_id=uuid.UUID(e["device_id"]),
                device_name=e.get("device_name"),
                relationship_type=e["relationship_type"],
                depth=1,
            )
            for e in raw
        ],
    )