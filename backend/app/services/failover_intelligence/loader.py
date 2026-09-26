"""
Live-scope loader — one place that reads the current reality of a site's WAN
edge, so the detector, the recovery verifier and the counterfactual simulator
all reason over the SAME snapshot instead of three subtly different ones.

Scope of a snapshot is a *site edge*: the candidate WAN paths attached to the
site's edge device, the routes that device currently has, and its BGP sessions.
Everything is read through repositories, tenant-scoped, and every read is
bounded — the post-collection hook must never turn into an unbounded scan.

The row builders return **plain JSON-safe dicts** (datetimes as ISO strings).
Those dicts are what gets persisted into ``failover_detector_state`` and into a
``FailoverEvent``'s captured transitions, so they must survive JSONB
serialisation unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.repositories.bgp_neighbor_repository import BgpNeighborRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.network_route_repository import NetworkRouteRepository
from app.repositories.topology_link_repository import TopologyLinkRepository
from app.services.failover_intelligence.paths import (
    is_candidate_link,
    unique_links_by_name,
)

#: Bounded reads. A tenant larger than this still gets intelligence, just over
#: the highest-priority slice, and the snapshot says it was truncated.
LINK_LIMIT = 300
DEVICE_LIMIT = 300
ROUTE_LIMIT = 500
BGP_LIMIT = 200

#: Device types that plausibly sit on a WAN edge. Used only to pick WHICH
#: device's routes/BGP to read for a site - never to invent connectivity.
EDGE_DEVICE_TYPES: frozenset[str] = frozenset(
    {
        "firewall", "router", "gateway", "sdwan", "sd-wan", "sd_wan",
        "edge", "switch", "vpn", "concentrator",
    }
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def link_row(link: Any) -> dict[str, Any]:
    attributes = getattr(link, "attributes", None) or {}
    jitter = attributes.get("jitter_ms") if isinstance(attributes, dict) else None
    return {
        "link_id": str(getattr(link, "id", "")),
        "name": getattr(link, "name", None),
        "link_type": getattr(link, "link_type", None),
        "oper_status": getattr(link, "oper_status", None),
        "admin_status": getattr(link, "admin_status", None),
        "latency_ms": getattr(link, "latency_ms", None),
        "jitter_ms": jitter,
        "packet_loss_pct": getattr(link, "packet_loss_pct", None),
        "utilization_pct": getattr(link, "utilization_pct", None),
        "bandwidth_mbps": getattr(link, "bandwidth_mbps", None),
        "provider": getattr(link, "provider", None),
        "redundancy_group": getattr(link, "redundancy_group", None),
        "device_a_id": str(getattr(link, "device_a_id", "") or "") or None,
        "device_b_id": str(getattr(link, "device_b_id", "") or "") or None,
        "last_state_change_at": _iso(getattr(link, "last_state_change_at", None)),
        "updated_at": _iso(getattr(link, "updated_at", None)),
    }


def route_row(route: Any) -> dict[str, Any]:
    return {
        "resource_id": str(getattr(route, "resource_id", "")),
        "vrf": getattr(route, "vrf", None),
        "destination": getattr(route, "destination", None),
        "prefix_length": getattr(route, "prefix_length", None),
        "protocol": getattr(route, "protocol", None),
        "next_hop": getattr(route, "next_hop", None),
        "gateway": getattr(route, "gateway", None),
        "outgoing_interface": getattr(route, "outgoing_interface", None),
        "state": getattr(route, "state", None),
        "is_best": bool(getattr(route, "is_best", False)),
        "metric": getattr(route, "metric", None),
        "administrative_distance": getattr(route, "administrative_distance", None),
        "last_change_at": _iso(getattr(route, "last_change_at", None)),
        "last_seen_at": _iso(getattr(route, "last_seen_at", None)),
    }


def route_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(part) or "")
        for part in ("resource_id", "vrf", "destination", "protocol")
    )


def bgp_row(session: Any) -> dict[str, Any]:
    return {
        "resource_id": str(getattr(session, "resource_id", "")),
        "vrf": getattr(session, "vrf", None),
        "neighbor_ip": getattr(session, "neighbor_ip", None),
        "state": getattr(session, "state", None),
        "admin_status": getattr(session, "admin_status", None),
        "remote_as": getattr(session, "remote_as", None),
        "uptime_seconds": getattr(session, "uptime_seconds", None),
        "prefixes_received": getattr(session, "prefixes_received", None),
        "last_state_change_at": _iso(getattr(session, "last_state_change_at", None)),
    }


def bgp_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(part) or "")
        for part in ("resource_id", "vrf", "neighbor_ip")
    )


@dataclass
class ScopeSnapshot:
    """One site edge's current WAN reality."""

    tenant_id: Any
    site_id: Any
    scope_key: str
    links: list[Any] = field(default_factory=list)
    duplicate_names: list[str] = field(default_factory=list)
    links_truncated: bool = False
    site_devices: list[Any] = field(default_factory=list)
    edge_device: Any | None = None
    routes: list[dict[str, Any]] = field(default_factory=list)
    bgp: list[dict[str, Any]] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def edge_device_id(self) -> Any:
        return getattr(self.edge_device, "id", None)

    @property
    def has_candidates(self) -> bool:
        return bool(self.links)

    def link_snapshot(self) -> dict[str, dict[str, Any]]:
        return {str(link.id): link_row(link) for link in self.links}

    def route_snapshot(self) -> dict[str, dict[str, Any]]:
        return {route_key(row): row for row in self.routes}

    def bgp_snapshot(self) -> dict[str, dict[str, Any]]:
        return {bgp_key(row): row for row in self.bgp}

    def link_by_id(self, link_id: Any) -> Any:
        target = str(link_id)
        for link in self.links:
            if str(link.id) == target:
                return link
        return None


def scope_key_for(site_id: Any) -> str:
    """Stable, greppable scope key; ``site:none`` for links with no site."""
    return f"site:{site_id}" if site_id is not None else "site:none"


def dedup_key_for(scope_key: str, edge_device_id: Any, link_from_id: Any) -> str:
    """Identity of one episode: which edge, which failed path."""
    edge = str(edge_device_id) if edge_device_id else "-"
    failed = str(link_from_id) if link_from_id else "-"
    return f"{scope_key}|{edge}|{failed}"


async def load_scope(
    db,
    tenant_id,
    *,
    site_id=None,
    link_limit: int = LINK_LIMIT,
    device_limit: int = DEVICE_LIMIT,
    route_limit: int = ROUTE_LIMIT,
    bgp_limit: int = BGP_LIMIT,
) -> ScopeSnapshot:
    """Read one site edge's candidate paths, routes and BGP sessions."""
    link_repo = TopologyLinkRepository(db)
    device_repo = DeviceRepository(db)
    route_repo = NetworkRouteRepository(db)
    bgp_repo = BgpNeighborRepository(db)

    snapshot = ScopeSnapshot(
        tenant_id=tenant_id, site_id=site_id, scope_key=scope_key_for(site_id)
    )

    links, total_links = await link_repo.list(tenant_id, limit=link_limit)
    snapshot.links_truncated = total_links > len(links)

    # Which scope OWNS a circuit is decided by its site-bearing endpoint, so the
    # same physical link is never evaluated twice (once under its site edge and
    # again under the "no site" scope via the carrier-side endpoint). Without
    # this, a single failure would open two episodes for one circuit.
    if site_id is not None:
        devices = await device_repo.list(
            tenant_id, site_id=site_id, limit=device_limit
        )
        snapshot.site_devices = devices
        device_ids = {str(device.id) for device in devices}
        scoped = [
            link
            for link in links
            if str(link.device_a_id) in device_ids
            or str(link.device_b_id) in device_ids
        ]
    else:
        all_devices = await device_repo.list(tenant_id, limit=device_limit)
        sited_ids = {str(device.id) for device in all_devices if device.site_id}
        snapshot.site_devices = [
            device for device in all_devices if device.site_id is None
        ]
        scoped = [
            link
            for link in links
            if str(link.device_a_id) not in sited_ids
            and str(link.device_b_id) not in sited_ids
        ]

    candidates = [link for link in scoped if is_candidate_link(link)]
    candidates, duplicates = unique_links_by_name(candidates)
    snapshot.links = candidates
    snapshot.duplicate_names = duplicates

    if not candidates:
        snapshot.skipped_reason = "no candidate WAN links discovered for this site edge"
        return snapshot

    snapshot.edge_device = _pick_edge_device(candidates, snapshot.site_devices)

    if snapshot.edge_device is not None:
        route_rows = await route_repo.list_for_resource(
            snapshot.edge_device.id, tenant_id, limit=route_limit
        )
        snapshot.routes = [route_row(row) for row in route_rows]
        bgp_rows = await bgp_repo.list_for_resource(
            snapshot.edge_device.id, tenant_id, limit=bgp_limit
        )
        snapshot.bgp = [bgp_row(row) for row in bgp_rows]
    return snapshot


def _pick_edge_device(candidates: list[Any], devices: list[Any]) -> Any | None:
    """Deterministic edge-device choice: most WAN links, edge-capable, then name.

    Only devices the candidate links actually attach to are eligible — the
    decision's routes/BGP must be read from a device that carries these paths.
    """
    counts: dict[str, int] = {}
    for link in candidates:
        for device_id in (link.device_a_id, link.device_b_id):
            key = str(device_id)
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    by_id = {str(device.id): device for device in devices}
    eligible = [by_id[key] for key in counts if key in by_id]
    if not eligible:
        return None
    eligible.sort(
        key=lambda device: (
            -counts.get(str(device.id), 0),
            -int(
                str(getattr(device, "device_type", "") or "").lower()
                in EDGE_DEVICE_TYPES
            ),
            str(getattr(device, "name", "")),
        )
    )
    return eligible[0]


async def list_scopes(db, tenant_id, *, device_limit: int = DEVICE_LIMIT):
    """Site ids that should be evaluated, including a trailing ``None``.

    The ``None`` entry covers WAN links whose endpoints belong to no site:
    those still exist and still fail over, so they get their own scope.
    """
    device_repo = DeviceRepository(db)
    devices = await device_repo.list(tenant_id, limit=device_limit)
    site_ids: list[Any] = []
    seen: set[str] = set()
    has_siteless = False
    for device in devices:
        if device.site_id is None:
            has_siteless = True
            continue
        key = str(device.site_id)
        if key not in seen:
            seen.add(key)
            site_ids.append(device.site_id)
    site_ids.sort(key=str)
    if has_siteless:
        site_ids.append(None)
    return site_ids
