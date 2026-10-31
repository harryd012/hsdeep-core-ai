"""
L3 topology read model (Meraki-style L3 view).

Devices are connected when they hold addresses on the same subnet; a subnet
shared by three or more devices becomes a "pipe" node; a static or default
route whose next hop is not a known device ends at a "next hop" diamond; and the
view is rooted where the default route leaves the known network.

Evidence, not inference
-----------------------
* Every edge comes from stored ``interface_addresses`` / ``network_routes`` rows
  that a collector read from the device itself. Nothing is derived from names,
  management IPs or reachability.
* Subnets are grouped by (site, VRF, network). MSP tenants reuse the same
  RFC 1918 ranges at every customer site, so two sites' ``192.168.1.0/24`` are
  two subnets, never one. Devices with no site share one "unsited" scope.
* Host prefixes, loopback and link-local addresses never connect anything
  (``ip_addressing.is_topological``); they still appear in the hover details.
* Only static and default routes draw edges. Connected routes duplicate the
  subnet edges, and a dynamic table would turn the map into a mesh.

Device state, icon and display name are NOT computed here: node ids use the
live graph's ``device:<uuid>`` convention so the UI joins this structure onto
the live topology it already holds. One health model, one place.

Query budget: four round trips (site check, devices, addresses, routes),
independent of tenant size.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ip_addressing import ParsedAddress, is_default_route, is_topological
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_address_repository import InterfaceAddressRepository
from app.repositories.network_route_repository import NetworkRouteRepository
from app.repositories.site_repository import SiteRepository
from app.services.topology_graph_service import PREFIX_DEVICE

MAX_FETCH_LIMIT = 100_000

PREFIX_SUBNET = "subnet:"
PREFIX_NEXT_HOP = "nexthop:"
#: Devices with no site share this scope for subnet grouping.
UNSITED = "-"
#: A subnet with at least this many devices is drawn as a pipe node.
PIPE_MIN_MEMBERS = 3
#: Route protocols that draw edges.
_EDGE_PROTOCOLS = frozenset({"static"})


class SiteNotFound(LookupError):
    pass


@dataclass
class _Member:
    node_id: str
    address: str
    interface: str | None


@dataclass
class _Subnet:
    scope: str
    vrf: str
    network: str
    members: dict[str, _Member] = field(default_factory=dict)


def _device_node(device_id: Any) -> str:
    return f"{PREFIX_DEVICE}{device_id}"


def _scope(site_id: Any) -> str:
    return str(site_id) if site_id else UNSITED


def _parsed(row) -> ParsedAddress:
    return ParsedAddress(
        family=row.family,
        address=row.address,
        prefix_length=row.prefix_length,
        network=row.network,
    )


def build_l3_topology(
    devices: Sequence[Any],
    addresses: Iterable[Any],
    routes: Iterable[Any],
) -> dict[str, Any]:
    """Pure builder: device / address / route rows in, L3 graph out.

    Rows are duck-typed (ORM rows in production, simple namespaces in tests).
    Output is deterministic for the same input.
    """
    device_by_id = {str(d.id): d for d in devices}
    scope_by_device = {str(d.id): _scope(getattr(d, "site_id", None)) for d in devices}

    details: dict[str, dict[str, Any]] = {}

    def _detail(device_id: str) -> dict[str, Any]:
        node_id = _device_node(device_id)
        if node_id not in details:
            details[node_id] = {
                "addresses": [],
                "static_routes": [],
                "default_route": None,
                "routes_by_protocol": {},
            }
        return details[node_id]

    providers: set[str] = set()
    subnets: dict[tuple[str, str, str], _Subnet] = {}
    #: (scope, vrf, address) -> device node id, for next-hop resolution.
    owner_of: dict[tuple[str, str, str], str] = {}

    for row in sorted(addresses, key=lambda r: (str(r.device_id), r.vrf, r.address)):
        device_id = str(row.device_id)
        if device_id not in device_by_id:
            continue  # outside the requested scope
        if row.provider:
            providers.add(row.provider)
        node_id = _device_node(device_id)
        scope = scope_by_device[device_id]
        _detail(device_id)["addresses"].append({
            "address": row.address,
            "prefix_length": row.prefix_length,
            "network": row.network,
            "interface": row.interface_name,
            "vrf": row.vrf,
            "is_primary": row.is_primary,
        })
        owner_of.setdefault((scope, row.vrf, row.address), node_id)
        if not is_topological(_parsed(row)):
            continue
        key = (scope, row.vrf, row.network)
        subnet = subnets.setdefault(key, _Subnet(scope=scope, vrf=row.vrf, network=row.network))
        subnet.members.setdefault(node_id, _Member(node_id, row.address, row.interface_name))

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    connected_pairs: set[frozenset[str]] = set()

    for key in sorted(subnets):
        subnet = subnets[key]
        members = [subnet.members[n] for n in sorted(subnet.members)]
        if len(members) < 2:
            continue
        if len(members) >= PIPE_MIN_MEMBERS:
            pipe_id = f"{PREFIX_SUBNET}{subnet.scope}:{subnet.vrf}:{subnet.network}"
            nodes.append({
                "id": pipe_id,
                "kind": "subnet",
                "label": subnet.network,
                "network": subnet.network,
                "vrf": subnet.vrf,
                "site_id": None if subnet.scope == UNSITED else subnet.scope,
                "member_count": len(members),
            })
            for member in members:
                edges.append({
                    "id": f"l3:{pipe_id}:{member.node_id}",
                    "source": pipe_id,
                    "target": member.node_id,
                    "kind": "subnet",
                    "network": subnet.network,
                    "vrf": subnet.vrf,
                    "source_address": None,
                    "target_address": member.address,
                    "target_interface": member.interface,
                })
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    connected_pairs.add(frozenset((a.node_id, b.node_id)))
        else:
            a, b = members
            connected_pairs.add(frozenset((a.node_id, b.node_id)))
            edges.append({
                "id": f"l3:{subnet.scope}:{subnet.vrf}:{subnet.network}",
                "source": a.node_id,
                "target": b.node_id,
                "kind": "subnet",
                "network": subnet.network,
                "vrf": subnet.vrf,
                "source_address": a.address,
                "source_interface": a.interface,
                "target_address": b.address,
                "target_interface": b.interface,
            })

    next_hops: dict[str, dict[str, Any]] = {}
    route_edges: dict[str, dict[str, Any]] = {}
    #: next-hop node id -> devices whose DEFAULT route leaves through it.
    default_exits: dict[str, set[str]] = {}
    route_device_ids: set[str] = set()

    for row in sorted(
        routes, key=lambda r: (str(r.resource_id), r.vrf, r.destination, r.next_hop or "")
    ):
        device_id = str(row.resource_id)
        if device_id not in device_by_id:
            continue
        route_device_ids.add(device_id)
        if row.provider:
            providers.add(row.provider)
        detail = _detail(device_id)
        counts = detail["routes_by_protocol"]
        counts[row.protocol] = counts.get(row.protocol, 0) + 1

        default = is_default_route(row.destination)
        if row.protocol not in _EDGE_PROTOCOLS and not default:
            continue
        node_id = _device_node(device_id)
        scope = scope_by_device[device_id]
        resolved: str | None = None
        if row.next_hop:
            resolved = owner_of.get((scope, row.vrf, row.next_hop))
            if resolved is None:
                resolved = f"{PREFIX_NEXT_HOP}{scope}:{row.vrf}:{row.next_hop}"
                hop = next_hops.setdefault(resolved, {
                    "id": resolved,
                    "kind": "next_hop",
                    "label": row.next_hop,
                    "address": row.next_hop,
                    "vrf": row.vrf,
                    "site_id": None if scope == UNSITED else scope,
                    "route_count": 0,
                })
                hop["route_count"] += 1

        route_info = {
            "destination": row.destination,
            "next_hop": row.next_hop,
            "interface": row.outgoing_interface,
            "protocol": row.protocol,
            "metric": row.metric,
            "vrf": row.vrf,
            "next_hop_node": resolved if resolved != node_id else None,
        }
        if row.protocol in _EDGE_PROTOCOLS:
            detail["static_routes"].append(route_info)
        if default and (detail["default_route"] is None or row.is_best):
            detail["default_route"] = route_info

        if resolved is None or resolved == node_id:
            continue
        if default and resolved.startswith(PREFIX_NEXT_HOP):
            default_exits.setdefault(resolved, set()).add(node_id)
        if frozenset((node_id, resolved)) in connected_pairs:
            continue  # already joined by a shared subnet
        edge_id = f"route:{node_id}:{resolved}"
        edge = route_edges.setdefault(edge_id, {
            "id": edge_id,
            "source": resolved,
            "target": node_id,
            "kind": "route",
            "network": None,
            "vrf": row.vrf,
            "source_address": row.next_hop,
            "target_address": None,
            "destinations": [],
        })
        edge["destinations"].append(row.destination)

    nodes.extend(next_hops[k] for k in sorted(next_hops))
    edges.extend(route_edges[k] for k in sorted(route_edges))

    # Root along the default route: the exits most devices leave through first.
    roots = sorted(default_exits, key=lambda hop: (-len(default_exits[hop]), hop))

    linked = {e["source"] for e in edges} | {e["target"] for e in edges}
    with_addresses = {n for n, d in details.items() if d["addresses"]}
    unlinked = sorted(n for n in with_addresses if n not in linked)

    return {
        "nodes": nodes,
        "edges": edges,
        "devices": {
            node_id: {
                "name": getattr(device_by_id[node_id[len(PREFIX_DEVICE):]], "name", None),
                "device_type": getattr(device_by_id[node_id[len(PREFIX_DEVICE):]], "device_type", None),
                **details[node_id],
            }
            for node_id in sorted(details)
        },
        "roots": roots,
        "unlinked": unlinked,
        "coverage": {
            "devices_total": len(device_by_id),
            "devices_with_addresses": len(with_addresses),
            "devices_with_routes": len(route_device_ids),
            "subnets": sum(1 for s in subnets.values() if len(s.members) >= 2),
            "providers": sorted(providers),
        },
    }


class TopologyL3Service:
    def __init__(self, db: AsyncSession):
        self.devices = DeviceRepository(db)
        self.sites = SiteRepository(db)
        self.addresses = InterfaceAddressRepository(db)
        self.routes = NetworkRouteRepository(db)

    async def build(
        self, tenant_id: uuid.UUID, *, site_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        if site_id is not None and await self.sites.get(site_id, tenant_id) is None:
            raise SiteNotFound(str(site_id))
        devices = await self.devices.list(
            tenant_id, limit=MAX_FETCH_LIMIT, offset=0, site_id=site_id
        )
        # Tenant-wide reads when unscoped (no IN list of every device id);
        # site-scoped reads are bounded to that site's devices.
        device_ids = [d.id for d in devices] if site_id is not None else None
        addresses = await self.addresses.list_for_tenant(tenant_id, device_ids=device_ids)
        routes = await self.routes.list_for_tenant(tenant_id, resource_ids=device_ids)
        result = build_l3_topology(devices, addresses, routes)
        result["site_id"] = str(site_id) if site_id else None
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        return result
