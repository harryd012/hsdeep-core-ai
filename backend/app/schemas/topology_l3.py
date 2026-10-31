"""
L3 topology API schemas - typed mirror of ``TopologyL3Service.build()``.

Device nodes are not listed here: devices are referenced by the live graph's
``device:<uuid>`` ids and the UI takes their state, icon and name from the live
topology. ``nodes`` holds only the synthetic L3 nodes (subnet pipes and
next-hop diamonds).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class L3NodeOut(BaseModel):
    id: str
    kind: Literal["subnet", "next_hop"]
    label: str
    vrf: str
    site_id: str | None = None
    #: subnet pipes only
    network: str | None = None
    member_count: int | None = None
    #: next-hop diamonds only
    address: str | None = None
    route_count: int | None = None


class L3EdgeOut(BaseModel):
    id: str
    source: str
    target: str
    #: subnet = shared subnet; route = static/default route to a next hop
    kind: Literal["subnet", "route"]
    network: str | None = None
    vrf: str
    source_address: str | None = None
    source_interface: str | None = None
    target_address: str | None = None
    target_interface: str | None = None
    #: route edges only: destinations routed over this edge
    destinations: list[str] = Field(default_factory=list)


class L3AddressOut(BaseModel):
    address: str
    prefix_length: int
    network: str
    interface: str | None = None
    vrf: str
    is_primary: bool


class L3RouteOut(BaseModel):
    destination: str
    next_hop: str | None = None
    interface: str | None = None
    protocol: str
    metric: int | None = None
    vrf: str
    #: node the next hop resolved to (a device or a next-hop diamond)
    next_hop_node: str | None = None


class L3DeviceOut(BaseModel):
    name: str | None = None
    device_type: str | None = None
    addresses: list[L3AddressOut]
    static_routes: list[L3RouteOut]
    default_route: L3RouteOut | None = None
    routes_by_protocol: dict[str, int]


class L3CoverageOut(BaseModel):
    """How much of the tenant actually reported L3 data - an empty map with
    zero coverage means "not collected", not "no subnets"."""

    devices_total: int
    devices_with_addresses: int
    devices_with_routes: int
    subnets: int
    providers: list[str]


class TopologyL3Out(BaseModel):
    nodes: list[L3NodeOut]
    edges: list[L3EdgeOut]
    #: keyed by device node id (``device:<uuid>``)
    devices: dict[str, L3DeviceOut]
    #: preferred tree roots, most-used default-route exit first
    roots: list[str]
    #: devices that reported addresses but share no subnet or route
    unlinked: list[str]
    coverage: L3CoverageOut
    site_id: str | None = None
    generated_at: str
