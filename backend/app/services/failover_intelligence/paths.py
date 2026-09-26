"""
Candidate WAN path construction — the bridge between real topology rows and the
EXISTING decision engine.

``app.services.network_intelligence.sdwan.evaluate_paths()`` already implements
the path scoring / best-path selection / failover semantics this product ships.
This module does not reimplement any of it: it only translates persisted
``TopologyLink`` rows (the real WAN circuits a collector actually discovered)
into the ``WanPath`` shape that engine consumes, and translates the decision
back to the links it names.

Two deliberate choices:

* **Missing telemetry degrades to worst case, not to zero.**
  ``WanPath.from_dict()`` coerces an absent/unparsable numeric to its
  worst-case reference (300 ms latency, 10 % loss, 0 Mbps). Passing the raw
  ``None`` through - rather than substituting 0 - is what stops "we never
  measured jitter on this circuit" from being scored as a perfect path and
  winning the decision.

* **Availability comes from the interface state, not from a score.**
  A link is only a live candidate when ``oper_status`` is up and
  ``admin_status`` is not administratively down. Score is a quality signal;
  admin/oper state is the ground truth for "can this path carry traffic".
"""
from __future__ import annotations

from typing import Any, Iterable

from app.services.infrastructure_intelligence.network import (
    VPN_LINK_TYPES,
    WAN_LINK_TYPES,
)
from app.services.network_intelligence.sdwan import WanPath, evaluate_paths

#: ``TopologyLink.link_type`` values that can participate in WAN path selection.
CANDIDATE_LINK_TYPES: frozenset[str] = WAN_LINK_TYPES | VPN_LINK_TYPES | {
    "sdwan", "sd_wan", "internet", "broadband", "lte", "5g", "other",
}

#: Link types that are never WAN candidates even when discovered in a WAN path
#: role (a LAN hand-off between two switches in the same site, for example).
EXCLUDED_LINK_TYPES: frozenset[str] = frozenset({"lan", "loopback", "cloud"})

UP_STATES: frozenset[str] = frozenset({"up", "connected", "established", "ok", "online"})
DOWN_STATES: frozenset[str] = frozenset({"down", "disconnected", "failed", "notconnect"})


def _state(value: Any) -> str:
    return str(value or "").strip().lower()


def is_candidate_link(link: Any) -> bool:
    """True when a link is a plausible WAN path for its site edge.

    Membership of a redundancy group is sufficient on its own: a collector that
    reports "vpn1 and vpn2 are the redundant pair" has told us this is a
    failover candidate even if it typed the bearer something unexpected.
    """
    if link is None:
        return False
    if getattr(link, "redundancy_group", None):
        return True
    link_type = _state(getattr(link, "link_type", None))
    if link_type in EXCLUDED_LINK_TYPES:
        return False
    return link_type in CANDIDATE_LINK_TYPES


def is_available(link: Any) -> bool:
    """Live-path test driven by real interface state."""
    if link is None:
        return False
    oper = _state(getattr(link, "oper_status", None))
    admin = _state(getattr(link, "admin_status", None))
    if admin in DOWN_STATES:
        return False
    if oper in DOWN_STATES:
        return False
    # Unknown/absent oper state: treat as available only if nothing says
    # otherwise, so a collector that omits the column does not silence a path.
    return True


def link_payload(link: Any) -> dict[str, Any]:
    """``TopologyLink`` row -> the dict shape ``evaluate_paths`` consumes.

    Numerics are forwarded verbatim, including ``None`` (see module docstring).
    """
    attributes = getattr(link, "attributes", None) or {}
    jitter = attributes.get("jitter_ms") if isinstance(attributes, dict) else None
    return {
        "name": str(getattr(link, "name", "") or ""),
        "latency_ms": getattr(link, "latency_ms", None),
        "jitter_ms": jitter,
        "packet_loss_pct": getattr(link, "packet_loss_pct", None),
        "bandwidth_mbps": getattr(link, "bandwidth_mbps", None),
        "utilization_pct": getattr(link, "utilization_pct", None),
        "available": is_available(link),
    }


def to_wan_path(link: Any) -> WanPath:
    return WanPath.from_dict(link_payload(link))


def unique_links_by_name(links: Iterable[Any]) -> tuple[list[Any], list[str]]:
    """Collapse links that share a name, deterministically.

    ``evaluate_paths`` identifies paths by name, so two links with the same
    name would be indistinguishable in the decision. Preference order: a live
    path over a down one, then the most recently created. Returns
    ``(links, duplicate_names)`` so callers can record the ambiguity as
    evidence instead of silently dropping a circuit.
    """
    by_name: dict[str, Any] = {}
    duplicates: set[str] = set()
    for link in links or ():
        name = str(getattr(link, "name", "") or "")
        if not name:
            continue
        current = by_name.get(name)
        if current is None:
            by_name[name] = link
            continue
        duplicates.add(name)
        current_available = is_available(current)
        candidate_available = is_available(link)
        if candidate_available and not current_available:
            by_name[name] = link
            continue
        if candidate_available == current_available:
            current_created = getattr(current, "created_at", None)
            candidate_created = getattr(link, "created_at", None)
            if candidate_created is not None and (
                current_created is None or candidate_created > current_created
            ):
                by_name[name] = link
    ordered = sorted(by_name.values(), key=lambda row: str(getattr(row, "name", "")))
    return ordered, sorted(duplicates)


def build_paths(links: Iterable[Any]) -> list[WanPath]:
    return [to_wan_path(link) for link in links or ()]


def link_by_name(links: Iterable[Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for link in links or ():
        name = str(getattr(link, "name", "") or "")
        if name and name not in mapping:
            mapping[name] = link
    return mapping


def decide(
    links: Iterable[Any], weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the shared decision engine over these links.

    Thin wrapper (not a second engine): weights, scoring, tie-breaking and
    failover semantics all remain owned by ``sdwan.evaluate_paths``.
    """
    return evaluate_paths(build_paths(links), weights)


def selected_link(decision: dict[str, Any], links: Iterable[Any]) -> Any:
    """Resolve the decision's ``selected_path`` back to a topology row."""
    name = (decision or {}).get("selected_path")
    if not name:
        return None
    return link_by_name(links).get(str(name))


def unavailable_links(links: Iterable[Any]) -> list[Any]:
    return [link for link in links or () if not is_available(link)]
