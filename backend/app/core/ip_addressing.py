"""
IP address / prefix normalisation shared by collectors and the L3 topology.

Vendors spell the same thing many ways: ``10.0.0.1/24``, ``10.0.0.1
255.255.255.0`` (FortiOS), a separate netmask column (SNMP ipAddrTable), a
prefix integer (iproute2 JSON), an IPv6 address with a ``%zone`` suffix. Every
collector funnels through these functions so one subnet is always written as
one ``network`` string and grouped the same way, whoever reported it.

Pure: stdlib ``ipaddress`` only, no I/O.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class ParsedAddress:
    family: int
    address: str
    prefix_length: int
    #: Normalised CIDR of the subnet the address sits on, e.g. ``10.0.0.0/24``.
    network: str


def _prefix_from(value: int | str | None, family: int) -> int | None:
    """A prefix length from an int, ``"24"``, ``"/24"`` or a dotted netmask."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        prefix = value
    else:
        text = value.strip().lstrip("/")
        if not text:
            return None
        if text.isdigit():
            prefix = int(text)
        elif family == 4:
            try:
                prefix = ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen
            except ValueError:
                return None  # non-contiguous or garbage mask
        else:
            return None
    limit = 32 if family == 4 else 128
    return prefix if 0 <= prefix <= limit else None


def _split(raw: str) -> tuple[str, str | None]:
    text = raw.strip()
    if "/" in text:
        address, prefix = text.split("/", 1)
        return address.strip(), prefix.strip()
    parts = text.split()
    if len(parts) == 2:
        return parts[0], parts[1]
    return text, None


def parse_interface_address(
    raw: str | None, prefix: int | str | None = None
) -> ParsedAddress | None:
    """Parse one interface address. ``None`` when it is missing or malformed.

    ``raw`` may carry its own prefix (``a/n`` or ``a mask``); otherwise
    ``prefix`` supplies it as an int, a digit string or a dotted netmask.
    """
    if not raw or not isinstance(raw, str):
        return None
    address_text, embedded = _split(raw)
    address_text = address_text.split("%", 1)[0]  # IPv6 zone index
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        return None
    length = _prefix_from(embedded if embedded is not None else prefix, address.version)
    if length is None:
        return None
    network = ipaddress.ip_network(f"{address}/{length}", strict=False)
    return ParsedAddress(
        family=address.version,
        address=str(address),
        prefix_length=length,
        network=str(network),
    )


def is_topological(parsed: ParsedAddress) -> bool:
    """Whether an address can join two devices on a shared subnet.

    Host prefixes (/32, /128), loopback, link-local, multicast and unspecified
    addresses are configured on a device but never connect it to anything.
    """
    address = ipaddress.ip_address(parsed.address)
    host_prefix = 32 if parsed.family == 4 else 128
    return not (
        parsed.prefix_length >= host_prefix
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    )


def parse_route_destination(
    raw: str | None, prefix: int | str | None = None
) -> tuple[str, int] | None:
    """Normalise a route destination to ``("10.0.0.0/8", 8)``.

    ``default`` (iproute2) is the IPv4 default route. Host bits are cleared so
    ``10.1.2.3/8`` and ``10.0.0.0/8`` are the same destination.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text == "default":
        return "0.0.0.0/0", 0
    address_text, embedded = _split(text)
    try:
        address = ipaddress.ip_address(address_text.split("%", 1)[0])
    except ValueError:
        return None
    if embedded is None and prefix is None:
        length = 32 if address.version == 4 else 128
    else:
        length = _prefix_from(embedded if embedded is not None else prefix, address.version)
    if length is None:
        return None
    network = ipaddress.ip_network(f"{address}/{length}", strict=False)
    return str(network), length


def parse_next_hop(raw: str | None) -> str | None:
    """A usable next-hop address, or ``None`` for unspecified/garbage values."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        address = ipaddress.ip_address(raw.strip().split("%", 1)[0])
    except ValueError:
        return None
    return None if address.is_unspecified else str(address)


def is_default_route(destination: str | None) -> bool:
    return destination in ("0.0.0.0/0", "::/0")


#: Names vendors use for the global routing table. They all mean the same
#: thing, so they are stored as '-' (the network_routes convention) and two
#: vendors' "default" tables group together.
_GLOBAL_VRF_NAMES = frozenset({"", "-", "0", "default", "global", "main", "vr:default"})


def normalise_vrf(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return "-" if text.lower() in _GLOBAL_VRF_NAMES else text
