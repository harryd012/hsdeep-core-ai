"""
Scope -> probe integration (Phase 6.3 §8).

This is the ONLY new discovery behaviour in this phase. It reads a
DiscoveryScope's policy and calls the EXISTING bounded probe; it does not scan,
model devices, resolve identity or write anything.

Invariant preserved: a scope is a source of observations. Nothing here creates
or matches canonical resources - that stays with ``DeviceRepository.upsert()``
and the tenant-scoped serial identity on the existing collection path.
"""
from __future__ import annotations

import logging

from app.models.discovery_scope import DiscoveryScope
from app.services.discovery.network_probe import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MAX_HOSTS,
    DEFAULT_TIMEOUT_SECONDS,
    PROTOCOL_PORTS,
    NetworkProbeReport,
    discover_network,
)

logger = logging.getLogger(__name__)

#: Scope types the bounded TCP/ICMP probe can actually execute today. The cloud
#: and dns_* types are valid scopes but are handled by later phases; running the
#: network probe for them would be meaningless, so they report skipped.
PROBEABLE_SCOPE_TYPES = frozenset({"private_cidr", "public_cidr", "public_ip", "seed_device"})


class ScopeNotProbeable(Exception):
    """Raised when a valid scope has no probe implementation yet."""


def protocols_for_scope(scope: DiscoveryScope) -> dict[str, int]:
    """TCP protocol map implied by the scope's ``allowed_protocols``.

    ICMP reachability is always attempted (it is how a host is discovered at
    all). TCP protocols are only probed when the scope explicitly allows them,
    so a scope that permits only ICMP never opens a TCP socket.
    """
    allowed = {str(p).strip().lower() for p in (scope.allowed_protocols or [])}
    if not allowed:
        return {}
    return {name: port for name, port in PROTOCOL_PORTS.items() if name in allowed}


async def probe_scope(scope: DiscoveryScope) -> NetworkProbeReport:
    """Run the existing bounded probe using ``scope``'s target and policy.

    Every tunable comes from the scope - nothing about a tenant's network is
    hard-coded in discovery code.
    """
    if scope.scope_type not in PROBEABLE_SCOPE_TYPES:
        raise ScopeNotProbeable(
            f"scope_type {scope.scope_type!r} has no network probe "
            f"(supported: {sorted(PROBEABLE_SCOPE_TYPES)})"
        )

    report = await discover_network(
        scope.target,
        max_hosts=int(scope.max_hosts or DEFAULT_MAX_HOSTS),
        max_concurrency=int(scope.max_concurrency or DEFAULT_MAX_CONCURRENCY),
        timeout=float(scope.timeout or DEFAULT_TIMEOUT_SECONDS),
        protocols=protocols_for_scope(scope),
        exclude=list(scope.excluded_targets or []),
    )
    logger.info(
        "Scope %s (%s) probed %s: reachable=%s",
        scope.name,
        scope.id,
        scope.target,
        len(report.reachable),
    )
    return report
