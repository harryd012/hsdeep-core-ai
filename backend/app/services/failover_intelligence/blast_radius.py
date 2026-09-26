"""
Blast radius — who is actually affected when this WAN path / edge fails?

Computed by traversing the REAL topology graph (``ResourceRelationship`` edges
between resource ids), never by guessing from names or from the site grouping.

Direction matters and is the part that is easy to get backwards. A dependency
edge means "source needs target": ``branch-fw depends_on mpls-circuit``. If the
MPLS circuit fails, the firewall is affected — so traversal walks *backwards*
along the dependency (from the failing node to the things that depend on it).
Symmetric relationships (``connected_to``, ``redundant_with``, ``part_of``) are
walked in both directions, because a failing member genuinely does affect its
peers.

Severity is a function of distance and relationship strength:

* hop 1 over a hard dependency (depends_on / routes_through / protected_by /
  hosted_on / member_of) -> **critical**
* hop 1 over any other relationship -> **warning**
* hop 2 -> **warning**
* hop 3+ -> **info**

The traversal is bounded by ``max_depth`` and ``max_nodes`` so a large or
accidentally cyclic graph can never make the post-collection hook expensive;
when a bound is hit the result says so explicitly (``truncated``) rather than
quietly under-reporting the blast radius.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEVERITY_ORDER: tuple[str, ...] = (SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO)

#: Relationships where "source needs target" - failing the target breaks the
#: source hard enough to call the impact critical at one hop.
HARD_DEPENDENCY_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "depends_on", "routes_through", "protected_by", "hosted_on",
        "member_of", "serves", "powers",
    }
)

#: Relationships with no meaningful direction - traversed both ways.
SYMMETRIC_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "connected_to", "peers_with", "adjacent_to", "redundant_with",
        "part_of", "linked_to", "ha_pair",
    }
)

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_NODES = 100


@dataclass(frozen=True)
class AffectedNode:
    resource_id: str
    severity: str
    hops: int
    via: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    name: str | None = None
    resource_type: str | None = None
    site_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "resource_type": self.resource_type,
            "site_id": self.site_id,
            "severity": self.severity,
            "hops": self.hops,
            "via": list(self.via),
            "path": list(self.path),
        }


def _severity(hops: int, relationship_type: str) -> str:
    if hops <= 1:
        return (
            SEVERITY_CRITICAL
            if relationship_type in HARD_DEPENDENCY_RELATIONSHIPS
            else SEVERITY_WARNING
        )
    if hops == 2:
        return SEVERITY_WARNING
    return SEVERITY_INFO


def _affected_neighbours(
    node: str, edges: Iterable[dict[str, Any]],
) -> list[tuple[str, str]]:
    """``(neighbour, relationship_type)`` pairs affected by ``node`` failing."""
    out: list[tuple[str, str]] = []
    for edge in edges or ():
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        rel = str(edge.get("relationship_type") or "related_to")
        if not source or not target:
            continue
        if target == node and source != node:
            # Something depends on the failing node.
            out.append((source, rel))
        elif source == node and rel in SYMMETRIC_RELATIONSHIPS and target != node:
            out.append((target, rel))
    return out


def traverse(
    seeds: Iterable[str],
    edges: Iterable[dict[str, Any]],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    node_meta: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bounded breadth-first blast radius from one or more failing resources."""
    edge_list = list(edges or ())
    seed_ids = [str(s) for s in (seeds or ()) if s]
    meta = node_meta or {}
    seen: set[str] = set(seed_ids)
    nodes: list[AffectedNode] = []
    queue: deque[tuple[str, int, list[str], list[str]]] = deque(
        (seed, 0, [], [seed]) for seed in seed_ids
    )
    truncated = False
    truncated_reason: str | None = None

    while queue:
        node, hops, via, path = queue.popleft()
        if hops >= max_depth:
            continue
        for neighbour, rel in _affected_neighbours(node, edge_list):
            if neighbour in seen:
                continue
            next_hops = hops + 1
            next_via = via + [rel]
            next_path = path + [neighbour]
            if len(nodes) >= max_nodes:
                truncated = True
                truncated_reason = f"max_nodes={max_nodes} reached"
                queue.clear()
                break
            seen.add(neighbour)
            info = meta.get(neighbour) or {}
            nodes.append(
                AffectedNode(
                    resource_id=neighbour,
                    severity=_severity(next_hops, rel),
                    hops=next_hops,
                    via=next_via,
                    path=next_path,
                    name=info.get("name"),
                    resource_type=info.get("resource_type"),
                    site_id=info.get("site_id"),
                )
            )
            queue.append((neighbour, next_hops, next_via, next_path))

    by_severity = {level: 0 for level in SEVERITY_ORDER}
    for affected in nodes:
        by_severity[affected.severity] = by_severity.get(affected.severity, 0) + 1
    nodes.sort(key=lambda n: (SEVERITY_ORDER.index(n.severity), n.hops, n.resource_id))
    return {
        "seed": seed_ids,
        "total": len(nodes),
        "by_severity": by_severity,
        "nodes": [node.to_dict() for node in nodes],
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "truncated": truncated,
        "truncated_reason": truncated_reason,
    }


#: Relationship statuses that mean "this edge no longer describes reality".
INACTIVE_RELATIONSHIP_STATUSES: frozenset[str] = frozenset(
    {"removed", "deleted", "expired"}
)


# ---------------------------------------------------------------------------
# Persistence-facing wrapper
# ---------------------------------------------------------------------------
# Everything above is pure; the class below is the only part that touches the
# database. It loads the tenant's relationship edges and resource metadata in a
# bounded number of queries and hands them to `traverse()`.
class BlastRadiusService:
    #: Bounded reads - a tenant with a huge topology must not turn the
    #: post-collection hook into a full-graph scan.
    EDGE_LIMIT = 2000
    NODE_LIMIT = 500

    def __init__(
        self,
        db,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> None:
        from app.repositories.device_repository import DeviceRepository
        from app.repositories.resource_relationship_repository import (
            ResourceRelationshipRepository,
        )
        from app.repositories.topology_link_repository import TopologyLinkRepository

        self.db = db
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.relationship_repo = ResourceRelationshipRepository(db)
        self.link_repo = TopologyLinkRepository(db)
        self.device_repo = DeviceRepository(db)

    async def load_graph(
        self, tenant_id, *, edge_limit: int | None = None, node_limit: int | None = None,
    ) -> tuple[list[dict], dict[str, dict]]:
        """``(edges, node_meta)`` for the tenant's real topology."""
        edges: list[dict] = []
        rows = await self.relationship_repo.list(
            tenant_id, limit=edge_limit or self.EDGE_LIMIT
        )
        for row in rows:
            if (row.status or "") in INACTIVE_RELATIONSHIP_STATUSES:
                continue
            edges.append(
                {
                    "source": str(row.source_resource_id),
                    "target": str(row.target_resource_id),
                    "relationship_type": row.relationship_type,
                    "subtype": row.relationship_subtype,
                    "label": row.label,
                    "weight": row.weight,
                    "confidence": row.confidence,
                    "is_inferred": row.is_inferred,
                    "status": row.status,
                }
            )

        meta: dict[str, dict] = {}
        devices = await self.device_repo.list(
            tenant_id, limit=node_limit or self.NODE_LIMIT, sort_by="name"
        )
        for device in devices:
            meta[str(device.id)] = {
                "name": device.name,
                "resource_type": device.device_type,
                "site_id": str(device.site_id) if device.site_id else None,
            }
        links, _ = await self.link_repo.list(
            tenant_id, limit=node_limit or self.NODE_LIMIT
        )
        for link in links:
            meta[str(link.id)] = {
                "name": link.name,
                "resource_type": f"link:{link.link_type}",
                "site_id": None,
            }
        return edges, meta

    async def compute(
        self, tenant_id, seeds, *, node_meta: dict[str, dict] | None = None,
    ) -> dict:
        """Blast radius for arbitrary seed resource ids."""
        edges, meta = await self.load_graph(tenant_id)
        if node_meta:
            meta = {**meta, **node_meta}
        result = traverse(
            seeds, edges,
            max_depth=self.max_depth, max_nodes=self.max_nodes, node_meta=meta,
        )
        result["graph"] = {"edges": len(edges), "nodes_known": len(meta)}
        result["source"] = "resource_relationships"
        return result

    async def compute_for_link(self, tenant_id, link) -> dict:
        """Blast radius for a failed WAN link.

        Seeds are the link itself plus BOTH endpoint devices. The endpoints are
        the ones that actually matter: ``resource_relationships`` foreign-keys
        its endpoints to ``devices.id``, so a link is never itself a graph node
        and everything that depends on a circuit depends on it *through* the
        device that terminates it.
        """
        seeds = [str(link.id)]
        if getattr(link, "device_a_id", None):
            seeds.append(str(link.device_a_id))
        if getattr(link, "device_b_id", None):
            seeds.append(str(link.device_b_id))
        meta = {
            str(link.id): {
                "name": link.name,
                "resource_type": f"link:{link.link_type}",
                "site_id": None,
            }
        }
        result = await self.compute(tenant_id, seeds, node_meta=meta)
        result["link_id"] = str(link.id)
        result["link_name"] = link.name
        return result

    async def compute_for_device(self, tenant_id, device_id) -> dict:
        result = await self.compute(tenant_id, [str(device_id)])
        result["device_id"] = str(device_id)
        return result
