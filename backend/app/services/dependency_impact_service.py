"""Phase 9.11 - deterministic dependency & impact over the CANONICAL graph.

Why this is not a duplicate of ``app/services/dependency_graph.py``: that
Sprint-16 service traverses the legacy CMDB table ``asset_relationships`` whose
nodes are devices/sites/business services. THIS module traverses the canonical
evidence graph (``resource_relationships``) that Phases 9.4-9.10 produce and
annotate (LLDP/CDP, PNETLab links, containment, BGP peering), and it reuses
Phase 9.10's canonical view - freshness, resolution, conflict, evidence sources -
instead of inventing a parallel graph.

Physical adjacency is NOT a dependency
--------------------------------------
``connected_to`` means two ports are patched together; it does NOT mean one
resource needs the other to serve. Turning every adjacency into a dependency
would manufacture service relationships that no evidence supports, so the types
are classified explicitly (from the existing ``REL_*`` vocabulary) and only
OPERATIONAL types create dependencies:

* OPERATIONAL (a dependency edge): depends_on, runs_on, hosted_on,
  routes_through, logged_to, backed_by, protected_by, managed_by, backup_for,
  failover_to, member_of, contains
* PHYSICAL (propagates IMPACT, never a dependency): connected_to, attached_to,
  peers_with, communicates_with
* MONITORING (neither): monitored_by, monitors

An unknown/unexpected type is treated as PHYSICAL and reported as
``unclassified``, so new evidence cannot silently become a service dependency.

Traversal is bounded and deterministic
--------------------------------------
One bounded read of the tenant's relationship rows builds an in-memory adjacency
map (no recursive SQL, no N+1), then BFS with ``max_depth`` and ``max_nodes``,
a visited set (cycle protection), de-duplicated paths, and a stable sort order.
Tenant isolation is structural: rows and devices are both read tenant-scoped.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.infrastructure_intelligence import vocabulary as V
from app.services.relationship_evidence_service import (
    MAX_FETCH_LIMIT,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    RESOLUTION_RESOLVED,
    RelationshipEvidenceService,
)


class ResourceNotFound(Exception):
    """The requested resource is not a device in this tenant (API -> 404)."""

#: Bounds: a dependency view is an operator aid, not an unbounded graph dump.
DEFAULT_MAX_DEPTH = 3
MAX_ALLOWED_DEPTH = 6
DEFAULT_MAX_NODES = 200
MAX_ALLOWED_NODES = 1000

#: Relationship types that mean "this resource NEEDS that resource".
OPERATIONAL_TYPES: frozenset[str] = frozenset({
    V.REL_DEPENDS_ON,
    V.REL_RUNS_ON,
    V.REL_HOSTED_ON,
    V.REL_ROUTES_THROUGH,
    V.REL_LOGGED_TO,
    V.REL_BACKED_BY,
    V.REL_PROTECTED_BY,
    V.REL_MANAGED_BY,
    V.REL_BACKUP_FOR,
    V.REL_FAILOVER_TO,
    V.REL_MEMBER_OF,
    V.REL_CONTAINS,
})

#: Types that carry IMPACT (a failure spreads through them) but are not needs.
PHYSICAL_TYPES: frozenset[str] = frozenset({
    V.REL_CONNECTED_TO,
    V.REL_ATTACHED_TO,
    V.REL_PEERS_WITH,
    V.REL_COMMUNICATES_WITH,
})

#: Types about observing a resource: neither a need nor a failure path.
MONITORING_TYPES: frozenset[str] = frozenset({V.REL_MONITORED_BY})

KIND_OPERATIONAL = "operational"
KIND_PHYSICAL = "physical"
KIND_MONITORING = "monitoring"
KIND_UNCLASSIFIED = "unclassified"


def classify(relationship_type: str) -> str:
    """Dependency semantics of one recorded relationship type.

    Data-driven from the existing vocabulary; anything unrecognised is
    ``unclassified`` (treated as physical and reported), never silently promoted
    to a dependency.
    """
    if relationship_type in OPERATIONAL_TYPES:
        return KIND_OPERATIONAL
    if relationship_type in PHYSICAL_TYPES:
        return KIND_PHYSICAL
    if relationship_type in MONITORING_TYPES:
        return KIND_MONITORING
    return KIND_UNCLASSIFIED


@dataclass
class Hop:
    """One traversed edge, with everything needed to justify it."""

    relationship_id: str
    from_resource_id: str
    to_resource_id: str
    from_name: str
    to_name: str
    relationship_type: str
    relationship_subtype: str | None
    kind: str
    discovered_by: str
    confidence: float
    is_inferred: bool
    status: str
    freshness: str
    resolution: str
    conflict: bool
    evidence_sources: list[str]
    local_interface: str | None
    remote_interface: str | None
    depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "from_resource_id": self.from_resource_id,
            "to_resource_id": self.to_resource_id,
            "from_name": self.from_name,
            "to_name": self.to_name,
            "relationship_type": self.relationship_type,
            "relationship_subtype": self.relationship_subtype,
            "kind": self.kind,
            "discovered_by": self.discovered_by,
            "confidence": self.confidence,
            "is_inferred": self.is_inferred,
            "status": self.status,
            "freshness": self.freshness,
            "resolution": self.resolution,
            "conflict": self.conflict,
            "evidence_sources": self.evidence_sources,
            "local_interface": self.local_interface,
            "remote_interface": self.remote_interface,
            "depth": self.depth,
        }


@dataclass
class Traversal:
    """Result of one bounded traversal, including how it was bounded."""

    root_id: str
    root_name: str | None
    hops: list[Hop] = field(default_factory=list)
    resources: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    max_depth_hit: bool = False
    cycles_detected: int = 0
    unresolved: int = 0
    stale: int = 0
    conflicts: int = 0
    neighbours: set[str] = field(default_factory=set)


#: Which end of a recorded row NEEDS the other one.
#: "forward" = the source needs the target (server --hosted_on--> host);
#: "reverse" = the target needs the source (container --contains--> contained).
_REVERSE_DEPENDENCY_TYPES: frozenset[str] = frozenset({V.REL_CONTAINS})


class DependencyImpactService:
    """Bounded, deterministic dependency and impact over recorded evidence."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.evidence = RelationshipEvidenceService(db)

    # -- loading (bounded: one row read + one device read per request) ------

    async def _load(self, tenant_id: uuid.UUID):
        devices = await self.evidence.devices.list(tenant_id, limit=MAX_FETCH_LIMIT)
        rows = await self.evidence.relationships.list(tenant_id, limit=MAX_FETCH_LIMIT)
        return devices, rows

    @staticmethod
    def _hop(
        row: Any,
        *,
        names: dict[str, str],
        freshness_fallback: str,
        depth: int,
    ) -> Hop:
        attributes = row.attributes or {}
        canonical = attributes.get("canonical") or {}
        source_id = str(row.source_resource_id)
        target_id = str(row.target_resource_id)
        return Hop(
            relationship_id=str(row.id),
            from_resource_id=source_id,
            to_resource_id=target_id,
            from_name=names.get(source_id, source_id),
            to_name=names.get(target_id, target_id),
            relationship_type=row.relationship_type,
            relationship_subtype=row.relationship_subtype,
            kind=classify(row.relationship_type),
            discovered_by=row.discovered_by,
            confidence=float(row.confidence or 0.0),
            is_inferred=bool(row.is_inferred),
            status=row.status,
            # Phase 9.10's annotation wins; otherwise the same rule is applied, so
            # a hop can never look fresher than the evidence behind it.
            freshness=canonical.get("freshness") or freshness_fallback,
            resolution=canonical.get("resolution") or RESOLUTION_RESOLVED,
            conflict=bool(canonical.get("conflict")),
            evidence_sources=canonical.get("evidence_sources")
            or sorted((attributes.get("evidence_sources") or {}).keys()),
            local_interface=attributes.get("local_interface"),
            remote_interface=attributes.get("remote_interface"),
            depth=depth,
        )

    def _walk(
        self,
        root_id: str,
        adjacency: dict[str, list[tuple[Any, bool]]],
        *,
        max_depth: int,
        max_nodes: int,
        names: dict[str, str],
    ) -> Traversal:
        """BFS following ``adjacency``, bounded by depth and node count."""
        result = Traversal(root_id=root_id, root_name=names.get(root_id))
        visited: set[str] = {root_id}
        # Which relationship we arrived through, so a walk never bounces straight
        # back along the SAME cable: for a bidirectional physical pair that is the
        # return path of one relationship, not a cycle.
        arrived_via: dict[str, str] = {}
        queue: list[tuple[str, int]] = [(root_id, 0)]
        while queue:
            current, depth = queue.pop(0)
            edges = adjacency.get(current, [])
            if depth >= max_depth:
                if edges:
                    result.max_depth_hit = True
                continue
            for row, forward in edges:
                if arrived_via.get(current) == str(row.id):
                    continue
                other = str(row.target_resource_id) if forward else str(row.source_resource_id)
                if other not in names:
                    # Unresolved: the other endpoint is not a device in this
                    # tenant, so the evidence is counted and never traversed.
                    result.unresolved += 1
                    continue
                hop = self._hop(
                    row,
                    names=names,
                    freshness_fallback=self.evidence._freshness(
                        row, datetime.now(timezone.utc)
                    ),
                    depth=depth + 1,
                )
                if hop.from_resource_id != current:
                    # The walk can move against the row's stored direction (who
                    # depends on whom, the inverse view, physical mirror rows).
                    # The hop must read in the direction it was TRAVERSED, while
                    # the row's own ids stay in the evidence fields.
                    hop.from_resource_id, hop.to_resource_id = (
                        hop.to_resource_id,
                        hop.from_resource_id,
                    )
                    hop.from_name, hop.to_name = hop.to_name, hop.from_name
                    hop.local_interface, hop.remote_interface = (
                        hop.remote_interface,
                        hop.local_interface,
                    )
                if other in visited:
                    # A cycle (or a second path to the same resource): the walk
                    # records that it saw it and never loops.
                    result.cycles_detected += 1
                    continue
                if len(visited) >= max_nodes:
                    result.truncated = True
                    return result
                visited.add(other)
                arrived_via[other] = str(row.id)
                queue.append((other, depth + 1))
                result.hops.append(hop)
                result.resources[other] = names.get(other, other)
                result.neighbours.add(other)
                if hop.freshness == FRESHNESS_STALE:
                    result.stale += 1
                if hop.conflict:
                    result.conflicts += 1
        result.hops.sort(key=lambda hop: (hop.depth, hop.to_name, hop.relationship_id))
        return result

    def _needs_map(self, rows: Iterable[Any], kinds: set[str]) -> dict[str, list[tuple[Any, bool]]]:
        """resource -> (row, forward?) walking FROM a resource TO what it needs."""
        adjacency: dict[str, list[tuple[Any, bool]]] = {}
        for row in rows:
            if classify(row.relationship_type) not in kinds:
                continue
            source_id = str(row.source_resource_id)
            target_id = str(row.target_resource_id)
            if row.relationship_type in _REVERSE_DEPENDENCY_TYPES:
                adjacency.setdefault(target_id, []).append((row, False))
            else:
                adjacency.setdefault(source_id, []).append((row, True))
        return adjacency

    def _dependents_map(
        self, rows: Iterable[Any], kinds: set[str]
    ) -> dict[str, list[tuple[Any, bool]]]:
        """The inverse of :meth:`_needs_map`: who needs THIS resource."""
        adjacency: dict[str, list[tuple[Any, bool]]] = {}
        for row in rows:
            if classify(row.relationship_type) not in kinds:
                continue
            source_id = str(row.source_resource_id)
            target_id = str(row.target_resource_id)
            if row.relationship_type in _REVERSE_DEPENDENCY_TYPES:
                adjacency.setdefault(source_id, []).append((row, True))
            else:
                adjacency.setdefault(target_id, []).append((row, False))
        return adjacency

    def _impact_map(
        self, rows: Iterable[Any], kinds: set[str]
    ) -> dict[str, list[tuple[Any, bool]]]:
        """Impact = needs-direction edges PLUS physical adjacency (a failed switch
        affects whatever is patched to it). Monitoring edges are excluded: losing
        sight of a resource is not an impact on it."""
        # Physical rows are added below as ONE edge per pair, so they must not also
        # arrive through the needs-direction map (that would double-count the same
        # cable and report the mirror row as a cycle).
        adjacency = self._needs_map(rows, kinds - {KIND_PHYSICAL})
        # A physical relationship is stored as TWO directed rows (A->B and B->A).
        # They describe one cable, so they contribute ONE traversable edge from
        # whichever end we walk: otherwise the mirror row is miscounted as a cycle
        # and the peer is counted twice.
        seen_pairs: set[frozenset[str]] = set()
        for row in rows:
            if classify(row.relationship_type) != KIND_PHYSICAL:
                continue
            source_id = str(row.source_resource_id)
            target_id = str(row.target_resource_id)
            pair = frozenset({source_id, target_id})
            if pair in seen_pairs or len(pair) < 2:
                continue
            seen_pairs.add(pair)
            adjacency.setdefault(source_id, []).append((row, True))
            adjacency.setdefault(target_id, []).append((row, False))
        return adjacency

    async def analyze(
        self,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Dependency + impact view for one resource, from recorded evidence."""
        observed_at = now or datetime.now(timezone.utc)
        depth = max(1, min(int(max_depth), MAX_ALLOWED_DEPTH))
        node_cap = max(1, min(int(max_nodes), MAX_ALLOWED_NODES))

        devices, rows = await self._load(tenant_id)
        names = {str(device.id): (device.name or str(device.id)) for device in devices}
        root = str(resource_id)
        if root not in names:
            raise ResourceNotFound(str(resource_id))
        by_id = {device.id: device for device in devices}

        operational = {KIND_OPERATIONAL}
        dependencies = self._walk(
            root, self._needs_map(rows, operational),
            max_depth=depth, max_nodes=node_cap, names=names,
        )
        dependents = self._walk(
            root, self._dependents_map(rows, operational),
            max_depth=depth, max_nodes=node_cap, names=names,
        )
        impact = self._walk(
            root, self._impact_map(rows, operational | {KIND_PHYSICAL}),
            max_depth=depth, max_nodes=node_cap, names=names,
        )

        unresolved_evidence = [
            {
                "relationship_id": str(row.id),
                "relationship_type": row.relationship_type,
                "endpoint": (
                    str(row.target_resource_id)
                    if str(row.target_resource_id) not in names
                    else str(row.source_resource_id)
                ),
                "why": "the recorded endpoint is not a device in this tenant",
            }
            for row in rows
            if str(row.source_resource_id) not in names
            or str(row.target_resource_id) not in names
        ]

        hops = [*dependencies.hops, *dependents.hops, *impact.hops]
        sources = sorted({source for hop in hops for source in hop.evidence_sources})
        freshness = {
            "fresh": sum(1 for hop in hops if hop.freshness == FRESHNESS_FRESH),
            "stale": sum(1 for hop in hops if hop.freshness == FRESHNESS_STALE),
            "unknown": sum(1 for hop in hops if hop.freshness == FRESHNESS_UNKNOWN),
            "worst": FRESHNESS_FRESH,
        }
        if freshness["stale"]:
            freshness["worst"] = FRESHNESS_STALE
        elif freshness["unknown"]:
            freshness["worst"] = FRESHNESS_UNKNOWN

        limitations: list[str] = []
        if not rows:
            limitations.append(
                "No relationship rows exist for this tenant, so no dependency or impact can "
                "be derived (nothing is inferred to fill the gap)."
            )
        elif not (dependencies.hops or dependents.hops):
            limitations.append(
                "No OPERATIONAL dependency evidence involves this resource. Physical "
                "adjacency is reported under impact only: two resources being patched "
                "together does not mean either one depends on the other."
            )
        if freshness["worst"] == FRESHNESS_STALE:
            limitations.append(
                "The supporting evidence is STALE, so this is what the recorded evidence "
                "supports - not a claim about current live impact."
            )
        if impact.truncated:
            limitations.append(
                f"Traversal stopped at the {node_cap}-node bound; the impact set is larger."
            )
        if impact.max_depth_hit:
            limitations.append(f"Traversal stopped at depth {depth}; deeper impact may exist.")
        if unresolved_evidence:
            limitations.append(
                f"{len(unresolved_evidence)} recorded relationship(s) have an endpoint that "
                "does not resolve to a device in this tenant; they are listed as unresolved "
                "evidence and were not traversed."
            )

        return self._contract(
            tenant_id=tenant_id,
            observed_at=observed_at,
            depth=depth,
            node_cap=node_cap,
            root=root,
            names=names,
            by_id=by_id,
            resource_id=resource_id,
            rows=rows,
            dependencies=dependencies,
            dependents=dependents,
            impact=impact,
            hops=hops,
            sources=sources,
            freshness=freshness,
            unresolved_evidence=unresolved_evidence,
            limitations=limitations,
        )

    @staticmethod
    def _contract(**parts: Any) -> dict[str, Any]:
        """The response contract, assembled in one place so it stays stable."""
        dependencies = parts["dependencies"]
        dependents = parts["dependents"]
        impact = parts["impact"]
        hops = parts["hops"]
        return {
            "tenant_id": str(parts["tenant_id"]),
            "generated_at": parts["observed_at"].isoformat(),
            "max_depth": parts["depth"],
            "max_nodes": parts["node_cap"],
            "resource": {
                "id": parts["root"],
                "name": parts["names"].get(parts["root"]),
                "device_type": getattr(
                    parts["by_id"].get(parts["resource_id"]), "device_type", None
                ),
                "status": getattr(parts["by_id"].get(parts["resource_id"]), "status", None),
            },
            "dependencies": [hop.to_dict() for hop in dependencies.hops],
            "dependents": [hop.to_dict() for hop in dependents.hops],
            "impact": [hop.to_dict() for hop in impact.hops],
            "summary": {
                "direct_dependencies": sum(1 for hop in dependencies.hops if hop.depth == 1),
                "direct_dependents": sum(1 for hop in dependents.hops if hop.depth == 1),
                "dependencies_total": len(dependencies.hops),
                "dependents_total": len(dependents.hops),
                "impacted_total": len(impact.hops),
                "evidence_records_seen": len(parts["rows"]),
                "unresolved": impact.unresolved + dependencies.unresolved,
                "unresolved_evidence_total": len(parts["unresolved_evidence"]),
                "stale": sum(1 for hop in hops if hop.freshness == FRESHNESS_STALE),
                "conflicts": impact.conflicts + dependencies.conflicts + dependents.conflicts,
                "cycles_detected": (
                    impact.cycles_detected
                    + dependencies.cycles_detected
                    + dependents.cycles_detected
                ),
                "truncated": bool(
                    impact.truncated or dependencies.truncated or dependents.truncated
                ),
                "max_depth_hit": bool(
                    impact.max_depth_hit or dependencies.max_depth_hit or dependents.max_depth_hit
                ),
            },
            "evidence_sources": parts["sources"],
            "freshness": parts["freshness"],
            "unresolved_evidence": parts["unresolved_evidence"],
            "limitations": parts["limitations"],
        }