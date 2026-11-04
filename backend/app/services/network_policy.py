"""
Per-tenant IP allowlist (enterprise security programme S3).

A tenant with no rules is open to every network. With at least one rule, its
users can only sign in, refresh and call the API, and its API keys can only be
used, from an address inside one of the rules. The tenant is the caller's own
(home) tenant: platform support staff acting under a break-glass grant are
governed by that grant, not by the customer's allowlist.

Checks run on every authenticated request, so each process caches a tenant's
networks for CACHE_SECONDS; a change made through the API clears this
process's cache at once and other processes pick it up within that time.
"""
from __future__ import annotations

import ipaddress
import time
import uuid

from sqlalchemy import select

from app.models.tenant_ip_rule import TenantIpRule

CACHE_SECONDS = 30.0
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_cache: dict[uuid.UUID, tuple[float, tuple[IpNetwork, ...]]] = {}


class InvalidNetwork(ValueError):
    pass


def normalize_cidr(value: str) -> str:
    """'10.1.2.3/8' -> '10.0.0.0/8'; a bare address becomes a /32 or /128."""
    try:
        net = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise InvalidNetwork(f"{value!r} is not an IP address or CIDR range") from exc
    if net.prefixlen == 0:
        raise InvalidNetwork("A rule for every address (/0) would allow everything; remove all rules instead.")
    return str(net)


def ip_in(networks: tuple[IpNetwork, ...] | list[IpNetwork], ip: str | None) -> bool:
    if not networks:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in networks)


async def tenant_networks(tenant_id: uuid.UUID) -> tuple[IpNetwork, ...]:
    hit = _cache.get(tenant_id)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        cidrs = (await db.execute(select(TenantIpRule.cidr).where(TenantIpRule.tenant_id == tenant_id))).scalars().all()
    networks = tuple(ipaddress.ip_network(c) for c in cidrs)
    _cache[tenant_id] = (now + CACHE_SECONDS, networks)
    if len(_cache) > 50_000:
        _cache.clear()
    return networks


async def ip_allowed(tenant_id: uuid.UUID, ip: str | None) -> bool:
    return ip_in(await tenant_networks(tenant_id), ip)


def forget(tenant_id: uuid.UUID | None = None) -> None:
    """Drop cached rules (one tenant, or all)."""
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(tenant_id, None)
