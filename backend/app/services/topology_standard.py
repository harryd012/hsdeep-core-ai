"""
Topology standard service (Sections 14/17).

Pure, dependency-free helpers that encode the enterprise infrastructure
topology standard (CE / PE / P routers, MPLS backbone, OSPF process/areas,
VPN / VRF instances, site-to-site links, redundancy groups) as data, plus
deterministic graph analysis (path tracing, redundant-path discovery,
blast-radius/reachability) that operates on plain link objects.

This module deliberately imports NOTHING from the ORM so the algorithms can
be unit-tested without a database. The ORM-backed ``TopologyLink`` model in
``app.models.topology_link`` carries the same attribute vocabulary here as
columns; this module's constants are the single source of truth for valid
values referenced by both the model, API validation and the frontend filters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Canonical link kinds (Section 17 Link attributes)
# ---------------------------------------------------------------------------
LINK_KINDS: tuple[str, ...] = (
    "wan", "lan", "serial", "mpls", "vpn_tunnel", "backbone",
    "cloud", "loopback", "other",
)

LINK_STATUSES: tuple[str, ...] = ("up", "down", "testing", "degraded", "unknown")

ROUTING_PROTOCOLS: tuple[str, ...] = (
    "static", "ospf", "bgp", "eigrp", "isis", "mpls", "rip", "vrrp",
    "hsrp", "lacp", "stp",
)

VPN_TYPES: tuple[str, ...] = (
    "mpls-vpn", "l2vpn", "ipsec", "gre", "dmvpn", "vxlan", "ssl-vpn",
)

# ---------------------------------------------------------------------------
# Device roles & role classes (Sections 14/15/17)
# ---------------------------------------------------------------------------
# role -> role_class. role is the human topology role; role_class is the
# bucket used for filtering in the dashboard topology view.
DEVICE_ROLES: dict[str, str] = {
    # network
    "ce": "network",            # Customer Edge router
    "pe": "network",            # Provider Edge router
    "p": "network",             # Provider (core) router
    "core": "network",
    "distribution": "network",
    "access": "network",
    "wan_router": "network",
    "mpls_router": "network",
    "edge_router": "network",
    "lan_switch": "network",
    # security
    "firewall_edge": "security",
    "firewall_internal": "security",
    "ids_ips": "security",
    "vpn_concentrator": "security",
    "waf": "security",
    "web_gateway": "security",
    "email_gateway": "security",
    # compute / hosting
    "server": "compute",
    "hypervisor": "compute",
    "virtual_machine": "compute",
    "kubernetes_node": "compute",
    "load_balancer": "compute",
    "application": "compute",
    # storage / backup
    "storage": "storage",
    "backup_appliance": "storage",
    # wireless
    "wireless_controller": "wireless",
    "access_point": "wireless",
    # cloud
    "cloud_resource": "cloud",
    "cloud_load_balancer": "cloud",
    "cloud_firewall": "cloud",
    "cloud_database": "cloud",
    "cloud_storage": "cloud",
}

ROLE_CLASSES: tuple[str, ...] = (
    "network", "security", "compute", "storage", "wireless", "cloud", "other",
)


def role_class_for(role: str | None) -> str:
    """Map a topology role to its filter class, or 'other' if unknown."""
    if not role:
        return "other"
    key = role.strip().lower().replace("-", "_").replace(" ", "_")
    return DEVICE_ROLES.get(key, "other")


# ---------------------------------------------------------------------------
# Lightweight link object used by the pure graph algorithms. Anything that
# exposes an ``endpoints`` property + ``oper_status`` works, so the analysis
# code can run on dicts, dataclasses or ORM ``TopologyLink`` rows.
# ---------------------------------------------------------------------------

@dataclass
class Link:
    id: str
    node_a: str
    node_b: str
    link_type: str = "other"
    oper_status: str = "up"
    redundancy_group: str | None = None
    vpn_name: str | None = None
    vrfs: list[str] = field(default_factory=list)
    protocol: str | None = None

    @property
    def endpoints(self) -> tuple[str, str]:
        return (self.node_a, self.node_b)


def _neighbors(node: str, edges: Sequence[Link]) -> Iterable[tuple[str, Link]]:
    for e in edges:
        if e.node_a == node:
            yield e.node_b, e
        elif e.node_b == node:
            yield e.node_a, e


def normalize_link_order(device_x: str, device_y: str) -> tuple[str, str]:
    """Deterministic (a, b) ordering for idempotent link dedup."""
    if device_x <= device_y:
        return device_x, device_y
    return device_y, device_x


def path_exists(
    start: str, target: str, edges: Sequence[Link],
    up_only: bool = True, max_hops: int = 32,
) -> bool:
    """BFS reachability between two nodes over the given links.

    ``up_only=True`` requires oper_status == 'up' on every traversed link,
    matching the redundant-path / service-availability checks.
    """
    if start == target:
        return True
    visited: set[str] = {start}
    frontier = {start}
    for _ in range(max_hops):
        nxt: set[str] = set()
        for f in frontier:
            for nbr, link in _neighbors(f, edges):
                if up_only and link.oper_status != "up":
                    continue
                if nbr == target:
                    return True
                if nbr not in visited:
                    visited.add(nbr)
                    nxt.add(nbr)
        if not nxt:
            return False
        frontier = nxt
    return False


def trace_path(
    start: str, target: str, edges: Sequence[Link],
    up_only: bool = True, max_hops: int = 32,
) -> list[list[str]]:
    """Return all simple paths (node sequences) from start to target.

    Used for path tracing and redundant-path analysis on the MPLS/OSPF/VPN
    topology. Stops expanding a branch once it revisits a node already on the
    current path (simple paths only).
    """
    results: list[list[str]] = []
    on_path = {start}

    def _walk(node: str, path: list[str]) -> None:
        for nbr, link in _neighbors(node, edges):
            if up_only and link.oper_status != "up":
                continue
            if nbr == target:
                results.append(path + [target])
                continue
            if nbr in on_path or len(path) >= max_hops:
                continue
            on_path.add(nbr)
            _walk(nbr, path + [nbr])
            on_path.remove(nbr)

    _walk(start, [start])
    return results



def redundant_paths(
    start: str, target: str, edges: Sequence[Link],
    up_only: bool = True, max_hops: int = 32,
) -> dict[str, Any]:
    """Analyze path redundancy between two nodes.

    Returns how many link-disjoint paths exist, the full path inventory, and
    whether reachability survives each single-link failure (the redundant-path
    acceptance check). ``link_disjoint_count`` is a greedy count of mutually
    link-disjoint subsets (bounded, adequate for site-scale topologies).
    """
    paths = trace_path(start, target, edges, up_only=up_only, max_hops=max_hops)
    path_sets = [frozenset((p[i], p[i + 1]) for i in range(len(p) - 1)) for p in paths]

    disjoint_count = 0
    used: set[frozenset] = set()
    for ps in sorted(path_sets, key=len):
        if all(len(ps & u) == 0 for u in used):
            used.add(ps)
            disjoint_count += 1

    edges_used: set = set()
    for ps in path_sets:
        edges_used |= ps

    single_failure_survivors: list[str] = []
    for e in edges:
        key = tuple(sorted(e.endpoints))
        if key in edges_used:
            remaining = [x for x in edges if x is not e]
            if path_exists(start, target, remaining, up_only=up_only, max_hops=max_hops):
                single_failure_survivors.append(e.id)

    return {
        "start": start,
        "target": target,
        "path_count": len(paths),
        "paths": paths,
        "link_disjoint_count": disjoint_count,
        "redundant": disjoint_count >= 2,
        "single_link_failure_survivors": single_failure_survivors,
    }


def validate_link_attributes(attrs: dict[str, Any]) -> list[str]:
    """Validate a link attribute dict against the standard vocabulary.

    Returns a list of human-readable problems (empty = valid). Used by the
    API layer to reject invalid link kinds/protocols before persisting.
    """
    problems: list[str] = []
    lt = attrs.get("link_type")
    if lt and lt not in LINK_KINDS:
        problems.append(f"link_type {lt!r} not in {LINK_KINDS}")
    st = attrs.get("oper_status")
    if st and st not in LINK_STATUSES:
        problems.append(f"oper_status {st!r} not in {LINK_STATUSES}")
    for proto in attrs.get("routing_protocols", []) or []:
        if proto not in ROUTING_PROTOCOLS:
            problems.append(f"routing_protocol {proto!r} not in {ROUTING_PROTOCOLS}")
    vpn = attrs.get("vpn_type")
    if vpn and vpn not in VPN_TYPES:
        problems.append(f"vpn_type {vpn!r} not in {VPN_TYPES}")
    return problems