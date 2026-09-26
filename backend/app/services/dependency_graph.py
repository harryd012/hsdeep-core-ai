"""
Service Dependency Graph.

Builds queryable dependency chains from the AssetRelationship table so
operators can answer: "What breaks if this device fails?" and
"What does the Payments API depend on?"

Sprint 15 seeded the AssetRelationship table. This sprint makes it
queryable for impact analysis using graph traversal algorithms.

All functions are tenant-scoped and bounded by max_depth to prevent
runaway recursion on circular graphs.
"""
from __future__ import annotations

import uuid
from collections import deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_relationship import AssetRelationship
from app.models.device import Device
from app.models.site import Site
from app.models.business_service import BusinessService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_edges(db: AsyncSession, tenant_id: uuid.UUID) -> list[AssetRelationship]:
    """Load all active relationship edges for a tenant (cached by caller)."""
    stmt = select(AssetRelationship).where(AssetRelationship.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _load_nodes(db: AsyncSession, tenant_id: uuid.UUID, node_ids: list[uuid.UUID]) -> dict[uuid.UUID, Device | Site | BusinessService]:
    """Batch-load devices/sites/services by ID. Returns a name/type lookup."""
    out: dict[uuid.UUID, Device | Site | BusinessService] = {}
    if not node_ids:
        return out

    devices = (
        await db.execute(
            select(Device).where(Device.tenant_id == tenant_id, Device.id.in_(node_ids), Device.is_deleted.is_(False))
        )
    ).scalars().all()
    for d in devices:
        out[d.id] = d

    sites = (
        await db.execute(
            select(Site).where(Site.tenant_id == tenant_id, Site.id.in_(node_ids), Site.is_deleted.is_(False))
        )
    ).scalars().all()
    for s in sites:
        out[s.id] = s

    services = (
        await db.execute(
            select(BusinessService).where(BusinessService.tenant_id == tenant_id, BusinessService.id.in_(node_ids), BusinessService.is_deleted.is_(False))
        )
    ).scalars().all()
    for svc in services:
        out[svc.id] = svc

    return out


def _adjacency_list(edges: list[AssetRelationship]) -> dict[uuid.UUID, list[tuple[uuid.UUID, str, str | None, float | None]]]:
    """
    Build adjacency map: asset_id -> list of (related_asset_id, rel_type, label, weight).
    Both directions so we can traverse upstream and downstream.
    """
    adj: dict[uuid.UUID, list[tuple[uuid.UUID, str, str | None, float | None]]] = {}
    for e in edges:
        adj.setdefault(e.asset_id, []).append((e.related_asset_id, e.relationship_type, e.label, e.weight))
        adj.setdefault(e.related_asset_id, []).append((e.asset_id, e.relationship_type, e.label, e.weight))
    return adj


def _node_label(node: Any) -> str:
    if isinstance(node, Device):
        return node.name
    if isinstance(node, Site):
        return node.name
    if isinstance(node, BusinessService):
        return node.name
    return str(node)


def _node_type(node: Any) -> str:
    if isinstance(node, Device):
        return "device"
    if isinstance(node, Site):
        return "site"
    if isinstance(node, BusinessService):
        return "business_service"
    return "unknown"


# ---------------------------------------------------------------------------
# BFS traversal
# ---------------------------------------------------------------------------

async def bfs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    root_id: uuid.UUID,
    *,
    direction: str = "downstream",
    relationship_type: str | None = None,
    max_depth: int = 5,
) -> dict[str, Any]:
    """
    Breadth-first traversal from root_id.

    direction:
      - "downstream": follow asset_id -> related_asset_id edges (what depends on root)
      - "upstream":   follow related_asset_id -> asset_id edges (what root depends on)
      - "both":       all connected nodes

    Returns:
      {
        "root_id": ...,
        "nodes": [{"id":..., "name":..., "type":..., "depth":...}],
        "edges": [{"source":..., "target":..., "relationship_type":..., "label":...}]
      }
    """
    if max_depth < 1 or max_depth > 10:
        max_depth = 5

    edges = await _load_edges(db, tenant_id)
    adj = _adjacency_list(edges)

    visited: set[uuid.UUID] = set()
    nodes: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []

    queue: deque[tuple[uuid.UUID, int]] = deque([(root_id, 0)])
    visited.add(root_id)

    # Collect all node IDs we need to resolve
    to_resolve: set[uuid.UUID] = {root_id}

    # Traverse graph to discover nodes and edges
    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        for neighbor_id, rel_type, label, weight in adj.get(current_id, []):
            if neighbor_id in visited:
                continue
            if relationship_type is not None and rel_type != relationship_type:
                continue

            visited.add(neighbor_id)
            to_resolve.add(neighbor_id)
            to_resolve.add(current_id)

            if direction == "downstream":
                source, target = current_id, neighbor_id
            elif direction == "upstream":
                source, target = neighbor_id, current_id
            else:  # both
                source, target = current_id, neighbor_id

            edges_out.append({
                "source": str(source),
                "target": str(target),
                "relationship_type": rel_type,
                "label": label,
                "weight": weight,
            })

            queue.append((neighbor_id, depth + 1))

    # Batch-resolve node metadata
    node_map = await _load_nodes(db, tenant_id, list(to_resolve))

    for nid in visited:
        node_obj = node_map.get(nid)
        nodes.append({
            "id": str(nid),
            "name": _node_label(node_obj) if node_obj else str(nid),
            "type": _node_type(node_obj) if node_obj else "unknown",
            "depth": 0 if nid == root_id else 1,
        })

    return {
        "root_id": str(root_id),
        "nodes": nodes,
        "edges": edges_out,
    }


# ---------------------------------------------------------------------------
# Public API (called by routes)
# ---------------------------------------------------------------------------

async def get_dependency_chain(db: AsyncSession, tenant_id: uuid.UUID, root_id: uuid.UUID, *, max_depth: int = 5) -> dict[str, Any]:
    """What does root depend on? (upstream dependencies)."""
    return await bfs(db, tenant_id, root_id, direction="upstream", max_depth=max_depth)


async def get_impact_analysis(db: AsyncSession, tenant_id: uuid.UUID, root_id: uuid.UUID, *, max_depth: int = 5) -> dict[str, Any]:
    """What breaks if root fails? (downstream impact)."""
    return await bfs(db, tenant_id, root_id, direction="downstream", max_depth=max_depth)


async def get_full_graph(db: AsyncSession, tenant_id: uuid.UUID, root_id: uuid.UUID, *, max_depth: int = 5) -> dict[str, Any]:
    """All connected assets regardless of direction."""
    return await bfs(db, tenant_id, root_id, direction="both", max_depth=max_depth)

