"""
SNMP L3 discovery: interface addresses and the routing table (L3 topology).

Standard IP-MIB / IP-FORWARD-MIB objects only, so every vendor is read the same
way:

  addresses  ipAddressTable  1.3.6.1.2.1.4.34.1   (RFC 4293; IPv4 + IPv6)
             ipAddrTable     1.3.6.1.2.1.4.20.1   (RFC 1213; IPv4, near-universal)
  routes     inetCidrRouteTable 1.3.6.1.2.1.4.24.7.1  (RFC 4292)
             ipCidrRouteTable   1.3.6.1.2.1.4.24.4.1  (RFC 2096)
             ipRouteTable       1.3.6.1.2.1.4.21.1    (RFC 1213)

Both address tables are read and merged (a device may implement either). For
routes the first table that returns rows wins, newest first.

Most of what we need is encoded in the row INDEX (destination, mask, next hop),
so only one or two columns are walked per table. Walks are capped at
``MAX_ROUTE_ROWS`` rows: GETNEXT is one round trip per row, and a router
carrying a full BGP table must not stall the poll.

The parsers are pure (rows in, normalized records out) and unit-tested with
recorded walk rows; ``collect_l3`` only drives the walks.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Awaitable, Callable

from app.collectors.base import NormalizedInterfaceAddress, NormalizedRoute
from app.core.ip_addressing import parse_interface_address, parse_next_hop, parse_route_destination

#: (oid, value) rows plus an error reason; same contract as
#: ``SNMPCollector._walk_with_status``.
WalkFn = Callable[..., Awaitable[tuple[list[tuple[str, str]], str | None]]]

MAX_ROUTE_ROWS = 2000

IP_ADDRESS_IFINDEX = "1.3.6.1.2.1.4.34.1.3"
IP_ADDRESS_PREFIX = "1.3.6.1.2.1.4.34.1.5"
IP_ADDR_IFINDEX = "1.3.6.1.2.1.4.20.1.2"
IP_ADDR_NETMASK = "1.3.6.1.2.1.4.20.1.3"

INET_CIDR_ROUTE_IFINDEX = "1.3.6.1.2.1.4.24.7.1.7"
INET_CIDR_ROUTE_PROTO = "1.3.6.1.2.1.4.24.7.1.9"
IP_CIDR_ROUTE_IFINDEX = "1.3.6.1.2.1.4.24.4.1.5"
IP_CIDR_ROUTE_PROTO = "1.3.6.1.2.1.4.24.4.1.7"
IP_ROUTE_IFINDEX = "1.3.6.1.2.1.4.21.1.2"
IP_ROUTE_NEXT_HOP = "1.3.6.1.2.1.4.21.1.7"
IP_ROUTE_PROTO = "1.3.6.1.2.1.4.21.1.9"
IP_ROUTE_MASK = "1.3.6.1.2.1.4.21.1.11"

#: IANAipRouteProtocol. local(2) is a directly connected network.
_ROUTE_PROTOCOLS = {"2": "connected", "3": "static", "13": "ospf", "14": "bgp"}


def _suffix(oid: str, base: str) -> str | None:
    oid = oid.lstrip(".")
    prefix = base + "."
    return oid[len(prefix):] if oid.startswith(prefix) else None


def _column(rows: list[tuple[str, str]], base: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for oid, value in rows:
        suffix = _suffix(oid, base)
        if suffix:
            out[suffix] = value
    return out


def _arcs(suffix: str) -> list[int] | None:
    parts = suffix.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    return [int(p) for p in parts]


def _inet_address(arcs: list[int], offset: int) -> tuple[str | None, int] | None:
    """Decode an InetAddressType + length-prefixed InetAddress at ``offset``.

    Returns (address or None for an unknown/empty address, next offset).
    """
    if offset + 2 > len(arcs):
        return None
    addr_type, length = arcs[offset], arcs[offset + 1]
    start = offset + 2
    octets = arcs[start:start + length]
    if len(octets) != length or any(o > 255 for o in octets):
        return None
    address: str | None = None
    if addr_type in (1, 3) and length >= 4:  # ipv4 / ipv4z (zone index trails)
        address = str(ipaddress.IPv4Address(bytes(octets[:4])))
    elif addr_type in (2, 4) and length >= 16:  # ipv6 / ipv6z
        address = str(ipaddress.IPv6Address(bytes(octets[:16])))
    return address, start + length


def _dotted(arcs: list[int]) -> str | None:
    if len(arcs) != 4 or any(o > 255 for o in arcs):
        return None
    return ".".join(str(o) for o in arcs)


def _if_name(if_index: Any, names: dict[str, str]) -> tuple[str | None, str | None]:
    key = str(if_index).strip() if if_index is not None else ""
    if not key.isdigit() or key == "0":
        return None, None
    return key, names.get(key)


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
def parse_ip_address_table(
    if_index_rows: list[tuple[str, str]],
    prefix_rows: list[tuple[str, str]],
    *,
    device_external_id: str,
    if_names: dict[str, str],
) -> list[NormalizedInterfaceAddress]:
    """ipAddressTable. Index: <addrType>.<len>.<octets>; ipAddressPrefix is a
    RowPointer whose LAST arc is the prefix length."""
    prefixes = _column(prefix_rows, IP_ADDRESS_PREFIX)
    out: list[NormalizedInterfaceAddress] = []
    for suffix, if_index in _column(if_index_rows, IP_ADDRESS_IFINDEX).items():
        arcs = _arcs(suffix)
        if arcs is None:
            continue
        decoded = _inet_address(arcs, 0)
        if decoded is None or decoded[0] is None:
            continue
        pointer = (prefixes.get(suffix) or "").strip().rstrip(".")
        last = pointer.rsplit(".", 1)[-1] if pointer else ""
        if not last.isdigit() or pointer in ("0.0", "0"):
            continue  # zeroDotZero: the agent does not know the prefix
        parsed = parse_interface_address(decoded[0], int(last))
        if parsed is None:
            continue
        external_id, name = _if_name(if_index, if_names)
        out.append(NormalizedInterfaceAddress(
            device_external_id=device_external_id,
            interface_external_id=external_id,
            interface_name=name,
            address=parsed.address,
            prefix_length=parsed.prefix_length,
        ))
    return out


def parse_ip_addr_table(
    if_index_rows: list[tuple[str, str]],
    netmask_rows: list[tuple[str, str]],
    *,
    device_external_id: str,
    if_names: dict[str, str],
    coerce_ipv4: Callable[[Any], str | None],
) -> list[NormalizedInterfaceAddress]:
    """ipAddrTable. Index: the IPv4 address itself; netmask is an IpAddress
    value (raw octets or dotted text depending on how pysnmp stringified it)."""
    masks = _column(netmask_rows, IP_ADDR_NETMASK)
    out: list[NormalizedInterfaceAddress] = []
    for suffix, if_index in _column(if_index_rows, IP_ADDR_IFINDEX).items():
        arcs = _arcs(suffix)
        address = _dotted(arcs) if arcs else None
        mask = coerce_ipv4(masks.get(suffix))
        if address is None or mask is None:
            continue
        parsed = parse_interface_address(address, mask)
        if parsed is None:
            continue
        external_id, name = _if_name(if_index, if_names)
        out.append(NormalizedInterfaceAddress(
            device_external_id=device_external_id,
            interface_external_id=external_id,
            interface_name=name,
            address=parsed.address,
            prefix_length=parsed.prefix_length,
        ))
    return out


def merge_addresses(*groups: list[NormalizedInterfaceAddress]) -> list[NormalizedInterfaceAddress]:
    """First table wins per address; later tables only add what it lacked."""
    seen: set[str] = set()
    out: list[NormalizedInterfaceAddress] = []
    for group in groups:
        for item in group:
            if item.address in seen:
                continue
            seen.add(item.address)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _route(
    device_external_id: str,
    destination: str | None,
    prefix: int | str | None,
    next_hop: str | None,
    proto: str | None,
    if_index: Any,
    if_names: dict[str, str],
) -> NormalizedRoute | None:
    parsed = parse_route_destination(destination, prefix)
    if parsed is None:
        return None
    _external_id, name = _if_name(if_index, if_names)
    return NormalizedRoute(
        device_external_id=device_external_id,
        destination=parsed[0],
        prefix_length=parsed[1],
        protocol=_ROUTE_PROTOCOLS.get((proto or "").strip(), "other"),
        next_hop=parse_next_hop(next_hop),
        outgoing_interface=name,
    )


def parse_inet_cidr_route_table(
    proto_rows: list[tuple[str, str]],
    if_index_rows: list[tuple[str, str]],
    *,
    device_external_id: str,
    if_names: dict[str, str],
) -> list[NormalizedRoute]:
    """inetCidrRouteTable. Index: destType.destLen.dest.pfxLen.policyLen.policy.
    nextHopType.nextHopLen.nextHop."""
    if_indexes = _column(if_index_rows, INET_CIDR_ROUTE_IFINDEX)
    out: list[NormalizedRoute] = []
    for suffix, proto in _column(proto_rows, INET_CIDR_ROUTE_PROTO).items():
        arcs = _arcs(suffix)
        if arcs is None:
            continue
        dest = _inet_address(arcs, 0)
        if dest is None or dest[0] is None or dest[1] + 1 >= len(arcs):
            continue
        prefix = arcs[dest[1]]
        policy_len = arcs[dest[1] + 1]
        hop = _inet_address(arcs, dest[1] + 2 + policy_len)
        if hop is None:
            continue
        route = _route(
            device_external_id, dest[0], prefix, hop[0], proto,
            if_indexes.get(suffix), if_names,
        )
        if route is not None:
            out.append(route)
    return out


def parse_ip_cidr_route_table(
    proto_rows: list[tuple[str, str]],
    if_index_rows: list[tuple[str, str]],
    *,
    device_external_id: str,
    if_names: dict[str, str],
) -> list[NormalizedRoute]:
    """ipCidrRouteTable. Index: dest(4).mask(4).tos(1).nextHop(4)."""
    if_indexes = _column(if_index_rows, IP_CIDR_ROUTE_IFINDEX)
    out: list[NormalizedRoute] = []
    for suffix, proto in _column(proto_rows, IP_CIDR_ROUTE_PROTO).items():
        arcs = _arcs(suffix)
        if arcs is None or len(arcs) != 13:
            continue
        dest, mask, hop = _dotted(arcs[0:4]), _dotted(arcs[4:8]), _dotted(arcs[9:13])
        if dest is None or mask is None:
            continue
        route = _route(
            device_external_id, dest, mask, hop, proto,
            if_indexes.get(suffix), if_names,
        )
        if route is not None:
            out.append(route)
    return out


def parse_ip_route_table(
    proto_rows: list[tuple[str, str]],
    mask_rows: list[tuple[str, str]],
    next_hop_rows: list[tuple[str, str]],
    if_index_rows: list[tuple[str, str]],
    *,
    device_external_id: str,
    if_names: dict[str, str],
    coerce_ipv4: Callable[[Any], str | None],
) -> list[NormalizedRoute]:
    """Legacy ipRouteTable. Index: dest(4); one route per destination."""
    masks = _column(mask_rows, IP_ROUTE_MASK)
    hops = _column(next_hop_rows, IP_ROUTE_NEXT_HOP)
    if_indexes = _column(if_index_rows, IP_ROUTE_IFINDEX)
    out: list[NormalizedRoute] = []
    for suffix, proto in _column(proto_rows, IP_ROUTE_PROTO).items():
        arcs = _arcs(suffix)
        dest = _dotted(arcs) if arcs else None
        mask = coerce_ipv4(masks.get(suffix))
        if dest is None or mask is None:
            continue
        route = _route(
            device_external_id, dest, mask, coerce_ipv4(hops.get(suffix)), proto,
            if_indexes.get(suffix), if_names,
        )
        if route is not None:
            out.append(route)
    return out


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def _status(rows_found: bool, errors: list[str | None]) -> str:
    if rows_found:
        return "collected"
    if errors and all(errors):
        return "failed"
    return "empty"


async def collect_l3(walk: WalkFn) -> dict[str, Any]:
    """Walk the address and route tables. Returns raw rows + per-table status.

    A table the agent does not implement returns no rows and no error, so an
    empty result is "empty"; only when EVERY walk errored is it "failed" (and
    stored rows are then left untouched by the saver).
    """
    raw: dict[str, Any] = {}
    unfiltered = walk

    async def walk(base: str, **kwargs):  # noqa: F811 - wraps the injected walk
        # An agent at endOfMibView echoes the requested OID itself (no row
        # index); that is "no rows", not a row.
        rows, err = await unfiltered(base, **kwargs)
        return [(oid, value) for oid, value in rows if _suffix(oid, base)], err

    errors: list[str | None] = []
    for key, base in (
        ("ip_address_ifindex", IP_ADDRESS_IFINDEX),
        ("ip_address_prefix", IP_ADDRESS_PREFIX),
        ("ip_addr_ifindex", IP_ADDR_IFINDEX),
        ("ip_addr_netmask", IP_ADDR_NETMASK),
    ):
        rows, err = await walk(base)
        raw[key] = rows
        errors.append(err)
    raw["addresses_status"] = _status(
        bool(raw["ip_address_ifindex"] or raw["ip_addr_ifindex"]), errors
    )

    errors = []
    raw["route_table"] = None
    for table, columns in (
        ("inet_cidr", (("proto", INET_CIDR_ROUTE_PROTO), ("ifindex", INET_CIDR_ROUTE_IFINDEX))),
        ("ip_cidr", (("proto", IP_CIDR_ROUTE_PROTO), ("ifindex", IP_CIDR_ROUTE_IFINDEX))),
        ("ip_route", (
            ("proto", IP_ROUTE_PROTO), ("mask", IP_ROUTE_MASK),
            ("next_hop", IP_ROUTE_NEXT_HOP), ("ifindex", IP_ROUTE_IFINDEX),
        )),
    ):
        proto_rows, err = await walk(columns[0][1], max_rows=MAX_ROUTE_ROWS)
        errors.append(err)
        if not proto_rows:
            continue
        raw["route_table"] = table
        raw["route_proto"] = proto_rows
        for name, base in columns[1:]:
            rows, _err = await walk(base, max_rows=MAX_ROUTE_ROWS)
            raw[f"route_{name}"] = rows
        raw["routes_truncated"] = len(proto_rows) >= MAX_ROUTE_ROWS
        break
    raw["routes_status"] = _status(raw["route_table"] is not None, errors)
    return raw


def normalize_l3(
    raw: dict[str, Any],
    *,
    device_external_id: str,
    if_names: dict[str, str],
    coerce_ipv4: Callable[[Any], str | None],
) -> tuple[list[NormalizedInterfaceAddress], list[NormalizedRoute], dict[str, str]]:
    addresses = merge_addresses(
        parse_ip_address_table(
            raw.get("ip_address_ifindex") or [], raw.get("ip_address_prefix") or [],
            device_external_id=device_external_id, if_names=if_names,
        ),
        parse_ip_addr_table(
            raw.get("ip_addr_ifindex") or [], raw.get("ip_addr_netmask") or [],
            device_external_id=device_external_id, if_names=if_names, coerce_ipv4=coerce_ipv4,
        ),
    )
    table = raw.get("route_table")
    if table == "inet_cidr":
        routes = parse_inet_cidr_route_table(
            raw.get("route_proto") or [], raw.get("route_ifindex") or [],
            device_external_id=device_external_id, if_names=if_names,
        )
    elif table == "ip_cidr":
        routes = parse_ip_cidr_route_table(
            raw.get("route_proto") or [], raw.get("route_ifindex") or [],
            device_external_id=device_external_id, if_names=if_names,
        )
    elif table == "ip_route":
        routes = parse_ip_route_table(
            raw.get("route_proto") or [], raw.get("route_mask") or [],
            raw.get("route_next_hop") or [], raw.get("route_ifindex") or [],
            device_external_id=device_external_id, if_names=if_names, coerce_ipv4=coerce_ipv4,
        )
    else:
        routes = []

    status: dict[str, str] = {}
    if "addresses_status" in raw:
        status["addresses"] = raw["addresses_status"]
        if status["addresses"] == "collected" and not addresses:
            status["addresses"] = "empty"
    if "routes_status" in raw:
        # A truncated walk did not see the whole table, so it must not retire
        # the routes it never reached.
        status["routes"] = "partial" if raw.get("routes_truncated") else raw["routes_status"]
    return addresses, routes, status
