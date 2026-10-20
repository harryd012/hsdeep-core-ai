"""
HSI-GNCE correlation context (spec sections 26, 28).

Bridges the universal infrastructure layer into HSDEEP's EXISTING graph-based
correlation architecture. It is the "HSDEEP Infrastructure - Graph Network
Correlation Engine" CONTEXT BUILDER: it assembles normalized, evidence-backed
context (resources, telemetry freshness, health, interfaces, WAN/SD-WAN/VPN,
routing, dependencies, topology, alerts, incidents, maintenance) and groups
symptoms that are connected in the dependency graph.

What this is NOT
----------------
There is no trained Graph Neural Network here and none is claimed. Correlation
is the deterministic, inspectable graph traversal implemented below and shared
with ``app/services/dependency_intelligence`` and the RCA/priority engines -
the same architecture those subsystems already use.

Why grouping matters (spec section 26)
--------------------------------------
A WAN failure must not open an unrelated critical incident for every downstream
symptom. Resources reachable from one another through dependency/topology edges
are returned as ONE group with ranked root-cause candidates, so the incident
layer can correlate them instead of fanning out.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.infrastructure_intelligence import vocabulary as V


@dataclass
class CorrelationGroup:
    """One connected symptom context, with ranked root-cause candidates."""

    group_id: str
    members: list[dict] = field(default_factory=list)
    root_cause_candidates: list[dict] = field(default_factory=list)
    affected_sites: list[str] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    affected_resource_types: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    correlation_basis: list[str] = field(default_factory=list)
    priority_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "member_count": len(self.members),
            "members": list(self.members),
            "root_cause_candidates": list(self.root_cause_candidates),
            "affected_sites": list(self.affected_sites),
            "affected_services": list(self.affected_services),
            "affected_resource_types": dict(self.affected_resource_types),
            "evidence": list(self.evidence),
            "correlation_basis": list(self.correlation_basis),
            "priority_context": dict(self.priority_context),
        }


#: Severity order used to rank root-cause candidates: the deepest/earliest
#: failure in the dependency chain is the likeliest cause.
_SEVERITY_RANK = {
    V.HEALTH_CRITICAL: 0,
    V.HEALTH_WARNING: 1,
    V.HEALTH_MAINTENANCE: 2,
    V.HEALTH_UNKNOWN: 3,
    V.HEALTH_HEALTHY: 4,
}


def connected_components(
    adjacency: dict[uuid.UUID, set[uuid.UUID]]
) -> list[list[uuid.UUID]]:
    """Deterministic connected components of an adjacency map.

    Deterministic on purpose: groups come out sorted by their smallest member id,
    and each group's members are sorted, so the same graph always correlates the
    same way (the existing correlation/RCA engines rely on that stability).
    """
    seen: set[uuid.UUID] = set()
    components: list[list[uuid.UUID]] = []
    for start in sorted(adjacency, key=str):
        if start in seen:
            continue
        stack = [start]
        component: list[uuid.UUID] = []
        seen.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in sorted(adjacency.get(node, ()), key=str):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component, key=str))
    return components


def rank_root_causes(
    members: list[dict], adjacency: dict[uuid.UUID, set[uuid.UUID]]
) -> list[dict]:
    """Rank group members as root-cause candidates.

    Ranking is evidence-based, not a score: worst health first, then the number
    of group peers that DEPEND on the member (a shared upstream failure explains
    more symptoms than a leaf), then name for stability.
    """
    explained: dict[uuid.UUID, int] = {}
    for node, neighbors in adjacency.items():
        explained[node] = len(neighbors)
    ranked = sorted(
        members,
        key=lambda m: (
            _SEVERITY_RANK.get(m.get("health", V.HEALTH_UNKNOWN), 3),
            -explained.get(_as_uuid(m.get("resource_id")), 0),
            m.get("name") or "",
        ),
    )
    return [
        {
            "resource_id": m.get("resource_id"),
            "name": m.get("name"),
            "resource_type": m.get("resource_type"),
            "health": m.get("health"),
            "explains_members": explained.get(_as_uuid(m.get("resource_id")), 0),
            "rank": index + 1,
        }
        for index, m in enumerate(ranked)
    ]


def _as_uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


class HsignceContextBuilder:
    """Assembles HSI-GNCE correlation context for a set of resources.

    Every repository is injected (the service-layer convention used throughout
    this codebase) and every read is tenant-scoped, so cross-tenant context is
    impossible by construction.
    """

    def __init__(
        self,
        *,
        device_repo,
        relationship_repo,
        link_repo,
        alert_repo=None,
        incident_repo=None,
        health_service=None,
    ):
        self.device_repo = device_repo
        self.relationship_repo = relationship_repo
        self.link_repo = link_repo
        self.alert_repo = alert_repo
        self.incident_repo = incident_repo
        self.health_service = health_service

    async def _device_links(self, tenant_id: uuid.UUID, device_id: uuid.UUID):
        links, _total = await self.link_repo.list(
            tenant_id, device_id=device_id, limit=200
        )
        return links

    async def _health_for(self, tenant_id, devices, reference):
        """Health per device - the shared HealthService when one was injected."""
        health: dict[uuid.UUID, dict] = {}
        for device in devices:
            if self.health_service is not None:
                health[device.id] = (
                    await self.health_service.resource_health(
                        tenant_id, device, now=reference
                    )
                ).to_dict()
            else:
                health[device.id] = {
                    "resource_id": str(device.id),
                    "health": V.health_from_oper_state(
                        V.operational_state(device.status)
                    ),
                    "status": V.operational_state(device.status),
                }
        return health

    async def _evidence(self, tenant_id: uuid.UUID, component: list[uuid.UUID]) -> list[dict]:
        """Active alerts / open incidents for the group - real rows only."""
        evidence: list[dict] = []
        if self.alert_repo is not None:
            for device_id in component:
                alerts = await self.alert_repo.list(
                    tenant_id, device_id=device_id, limit=50
                )
                for alert in alerts:
                    evidence.append({
                        "kind": "alert",
                        "resource_id": str(device_id),
                        "severity": getattr(alert, "severity", None),
                        "status": getattr(alert, "status", None),
                        "title": getattr(alert, "title", None)
                        or getattr(alert, "message", None),
                    })
        if self.incident_repo is not None:
            for device_id in component:
                incident = await self.incident_repo.get_open_for_device(device_id, tenant_id)
                if incident is not None:
                    evidence.append({
                        "kind": "incident",
                        "resource_id": str(device_id),
                        "incident_id": str(incident.id),
                        "status": getattr(incident, "status", None),
                        "severity": getattr(incident, "severity", None),
                    })
        return evidence

    async def build(
        self,
        tenant_id: uuid.UUID,
        resource_ids: list[uuid.UUID],
        *,
        include_dependencies: bool = True,
        now: datetime | None = None,
    ) -> dict:
        """Build the normalized correlation context for ``resource_ids``."""
        reference = now or datetime.now(timezone.utc)
        seeds = list(dict.fromkeys(resource_ids))
        devices = await self.device_repo.list_by_ids(tenant_id, seeds)
        if not devices:
            return {
                "tenant_id": str(tenant_id),
                "resources": [],
                "groups": [],
                "correlation_basis": [],
                "limitations": [{
                    "reason": V.REASON_NOT_YET_COLLECTED,
                    "detail": "none of the requested resources exist in this tenant",
                }],
            }

        member_ids = {d.id for d in devices}
        adjacency: dict[uuid.UUID, set[uuid.UUID]] = {d.id: set() for d in devices}
        basis: list[str] = []

        if include_dependencies:
            for device in devices:
                edges = await self.relationship_repo.list_edges_from(
                    device.id, tenant_id
                )
                for edge, _name in edges:
                    if edge.target_resource_id in member_ids:
                        adjacency[device.id].add(edge.target_resource_id)
                        adjacency.setdefault(edge.target_resource_id, set())
                        basis.append(
                            f"resource_relationship:{edge.relationship_type}"
                        )
                # Topology links join BOTH endpoint devices: a WAN/VPN bearer
                # carries a failure between two devices, so the graph must too.
                for link in await self._device_links(tenant_id, device.id):
                    other = (
                        link.device_b_id
                        if link.device_a_id == device.id
                        else link.device_a_id
                    )
                    if other in member_ids and other != device.id:
                        adjacency[device.id].add(other)
                        adjacency.setdefault(other, set()).add(device.id)
                        basis.append(f"topology_link:{link.link_type}")

        health_by_id = await self._health_for(tenant_id, devices, reference)
        resource_rows = [
            {
                "resource_id": str(device.id),
                "name": device.name,
                "resource_type": V.universal_resource_type(device.device_type),
                "site_id": str(device.site_id) if device.site_id else None,
                "health": health_by_id[device.id].get("health", V.HEALTH_UNKNOWN),
                "status": health_by_id[device.id].get("status", V.OPER_STATE_UNKNOWN),
                "last_telemetry_at": (
                    device.last_telemetry_at.isoformat()
                    if getattr(device, "last_telemetry_at", None) else None
                ),
            }
            for device in devices
        ]
        rows_by_id = {row["resource_id"]: row for row in resource_rows}

        groups: list[CorrelationGroup] = []
        for index, component in enumerate(connected_components(adjacency)):
            members = [
                rows_by_id[str(node)] for node in component if str(node) in rows_by_id
            ]
            if not members:
                continue
            group = CorrelationGroup(
                group_id=f"group-{index + 1}",
                members=members,
                root_cause_candidates=rank_root_causes(members, adjacency),
                affected_sites=sorted({
                    m["site_id"] for m in members if m.get("site_id")
                }),
                affected_services=sorted({
                    m["resource_type"] for m in members if m.get("resource_type")
                }),
                affected_resource_types=_count_types(members),
                correlation_basis=sorted(set(basis)) or ["no_edges"],
                evidence=await self._evidence(tenant_id, component),
            )
            group.priority_context = {
                "worst_health": min(
                    (m.get("health", V.HEALTH_UNKNOWN) for m in members),
                    key=lambda h: _SEVERITY_RANK.get(h, 3),
                ),
                "member_count": len(members),
                "critical_members": sum(
                    1 for m in members if m.get("health") == V.HEALTH_CRITICAL
                ),
                "affected_sites": len(group.affected_sites),
            }
            groups.append(group)

        return {
            "tenant_id": str(tenant_id),
            "generated_at": reference.isoformat(),
            "resources": resource_rows,
            "groups": [group.to_dict() for group in groups],
            "correlation_basis": sorted(set(basis)),
            "limitations": [] if basis else [{
                "reason": V.REASON_PRIMARY_DATA_MISSING,
                "detail": (
                    "no dependency or topology edges exist between the requested "
                    "resources, so each is reported as its own group"
                ),
            }],
        }


def _count_types(members: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for member in members:
        resource_type = member.get("resource_type") or "unknown"
        counts[resource_type] = counts.get(resource_type, 0) + 1
    return counts
