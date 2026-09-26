"""
Target validation for DiscoveryScope (Phase 6.3 §5).

Kept as a pure function so it is testable without a database, and so the same
rules are enforced by both the API layer and any caller that builds a scope
directly. It never touches credentials.
"""
from __future__ import annotations

import ipaddress
import re

from app.models.discovery_scope import SCOPE_TYPES

#: A public scope may never be unbounded. 0.0.0.0/0 and ::/0 are refused, as is
#: any prefix broader than this many bits, unless a caller explicitly opts in
#: through the existing authorization policy.
MIN_PUBLIC_PREFIX_V4 = 16
MIN_PUBLIC_PREFIX_V6 = 48

_DOMAIN_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,63}$"
)
_CLOUD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$")


class ScopeTargetInvalid(ValueError):
    """Raised when a target does not match its scope type."""


def _private(cidr: str) -> str:
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise ScopeTargetInvalid(f"{cidr!r} is not a valid CIDR") from exc
    return str(network)


def _public_cidr(cidr: str, *, allow_broad_public: bool = False) -> str:
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise ScopeTargetInvalid(f"{cidr!r} is not a valid CIDR") from exc
    if allow_broad_public:
        return str(network)
    floor = (
        MIN_PUBLIC_PREFIX_V4
        if network.version == 4
        else MIN_PUBLIC_PREFIX_V6
    )
    if network.prefixlen < floor:
        raise ScopeTargetInvalid(
            f"{cidr!r} is broader than /{floor}; unrestricted public discovery is "
            "not permitted"
        )
    return str(network)


def _host(target: str, *, require_public: bool = False) -> str:
    try:
        address = ipaddress.ip_address(target)
    except ValueError as exc:
        raise ScopeTargetInvalid(f"{target!r} is not a valid IP address") from exc
    if require_public and not address.is_global:
        raise ScopeTargetInvalid(f"{target!r} is not a public address")
    return str(address)


def validate_target(scope_type: str, target: str, *, allow_broad_public: bool = False) -> str:
    """Return the canonical target for ``scope_type`` or raise.

    Ownership is never derived from the target - only validated by shape.
    """
    if scope_type not in SCOPE_TYPES:
        raise ScopeTargetInvalid(
            f"unknown scope_type {scope_type!r}; expected one of {sorted(SCOPE_TYPES)}"
        )
    value = (target or "").strip()
    if not value:
        raise ScopeTargetInvalid("target is required")

    if scope_type == "private_cidr":
        return _private(value)
    if scope_type == "public_cidr":
        return _public_cidr(value, allow_broad_public=allow_broad_public)
    if scope_type == "public_ip":
        return _host(value, require_public=True)
    if scope_type == "seed_device":
        # A seed device may be an address or a resolvable hostname; it is an
        # entry point, not an identity claim.
        try:
            return _host(value)
        except ScopeTargetInvalid:
            if not _DOMAIN_RE.match(value):
                raise ScopeTargetInvalid(
                    f"{value!r} is not a valid IP address or hostname"
                ) from None
            return value.lower()
    if scope_type == "dns_domain":
        if not _DOMAIN_RE.match(value):
            raise ScopeTargetInvalid(f"{value!r} is not a valid domain")
        return value.lower()
    # cloud_account / cloud_subscription / cloud_project:
    # a provider-side identifier, structure only (no provider behaviour yet).
    if not _CLOUD_ID_RE.match(value):
        raise ScopeTargetInvalid(f"{value!r} is not a valid cloud identifier")
    return value


def validate_protocols(protocols: list[str] | None) -> list[str]:
    """Canonical, de-duplicated, lower-cased protocol allow-list."""
    if not protocols:
        return []
    from app.models.discovery_scope import ALLOWED_PROTOCOLS

    cleaned: list[str] = []
    for raw in protocols:
        name = str(raw).strip().lower()
        if not name:
            continue
        if name not in ALLOWED_PROTOCOLS:
            raise ScopeTargetInvalid(
                f"unknown protocol {name!r}; expected one of {sorted(ALLOWED_PROTOCOLS)}"
            )
        if name not in cleaned:
            cleaned.append(name)
    return cleaned
