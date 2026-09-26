"""
Universal network intelligence models (spec sections 15, 18, 19, 20).

Provider-neutral projections of:
  * interfaces           (UniversalInterface)
  * WAN / SD-WAN members  (WanMember)
  * VPN / IPsec tunnels   (VpnTunnel)
  * routing entries       (RouteEntry)
  * BGP sessions          (BgpNeighborState)

Where the data already lives
----------------------------
* Interfaces -> ``interfaces`` (extended by this milestone with the canonical
  speed/rate/discard/latency snapshot columns).
* WAN + VPN  -> ``topology_links``. That model was already designed for this
  (``link_type`` wan/serial/mpls/vpn_tunnel, ``provider``, ``circuit_id``,
  ``bandwidth_mbps``, ``latency_ms``, ``packet_loss_pct``, ``utilization_pct``,
  ``vpn_name``, ``vpn_type``, ``redundancy_group``, ``bgp``/``ospf``/``vrfs``).
  Reusing it keeps WAN and VPN inside the SAME topology graph as every other
  link instead of living in a parallel structure.
* Routing    -> ``network_routes`` (new).
* BGP        -> ``bgp_neighbors`` (new).

SD-WAN scoring deliberately REUSES the existing
``app.services.network_intelligence.sdwan`` engine rather than re-implementing
path scoring: one scoring implementation, one set of semantics.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.services.infrastructure_intelligence import vocabulary as V

_MEASURED_IFACE_FIELDS = (
    "rx_bps", "tx_bps", "utilization_pct", "errors_in", "errors_out",
    "discards_in", "discards_out", "packet_loss_pct", "latency_ms", "jitter_ms",
)


# ---------------------------------------------------------------------------
# Interfaces (spec section 15)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UniversalInterface:
    interface_id: uuid.UUID
    tenant_id: uuid.UUID
    resource_id: uuid.UUID
    name: str
    interface_type: str = V.IFACE_UNKNOWN
    description: str | None = None
    mac_address: str | None = None
    vlan_id: int | None = None
    admin_state: str = V.OPER_STATE_UNKNOWN
    oper_state: str = V.OPER_STATE_UNKNOWN
    admin_status_raw: str = "unknown"
    oper_status_raw: str = "unknown"
    speed_bps: float | None = None
    negotiated_speed_mbps: float | None = None
    rx_bytes: float | None = None
    tx_bytes: float | None = None
    rx_bps: float | None = None
    tx_bps: float | None = None
    utilization_pct: float | None = None
    errors_in: float | None = None
    errors_out: float | None = None
    discards_in: float | None = None
    discards_out: float | None = None
    packet_loss_pct: float | None = None
    latency_ms: float | None = None
    jitter_ms: float | None = None
    uptime_seconds: float | None = None
    last_state_change_at: datetime | None = None
    last_telemetry_at: datetime | None = None
    provider: str | None = None
    provider_resource_id: str | None = None

    @classmethod
    def from_interface(
        cls, interface, *, provider: str | None = None
    ) -> "UniversalInterface":
        """Project a stored ``Interface`` row.

        ``speed_bps`` is only filled from the ``speed_bps`` column; the legacy
        ``speed_mbps`` value is surfaced separately as ``negotiated_speed_mbps``
        instead of being silently converted, so a consumer can tell which column
        the provider actually populated.
        """
        return cls(
            interface_id=interface.id,
            tenant_id=interface.tenant_id,
            resource_id=interface.device_id,
            name=interface.name,
            interface_type=getattr(interface, "interface_type", None) or V.IFACE_UNKNOWN,
            description=getattr(interface, "description", None),
            mac_address=interface.mac_address,
            vlan_id=getattr(interface, "vlan_id", None),
            admin_state=V.operational_state(interface.admin_status),
            oper_state=V.operational_state(interface.oper_status),
            admin_status_raw=interface.admin_status,
            oper_status_raw=interface.oper_status,
            speed_bps=getattr(interface, "speed_bps", None),
            negotiated_speed_mbps=interface.speed_mbps,
            rx_bytes=interface.in_octets,
            tx_bytes=interface.out_octets,
            rx_bps=getattr(interface, "rx_bps", None),
            tx_bps=getattr(interface, "tx_bps", None),
            utilization_pct=getattr(interface, "utilization_pct", None),
            errors_in=interface.in_errors,
            errors_out=interface.out_errors,
            discards_in=getattr(interface, "discards_in", None),
            discards_out=getattr(interface, "discards_out", None),
            packet_loss_pct=getattr(interface, "packet_loss_pct", None),
            latency_ms=getattr(interface, "latency_ms", None),
            jitter_ms=getattr(interface, "jitter_ms", None),
            last_state_change_at=interface.last_status_change_at,
            last_telemetry_at=getattr(interface, "last_telemetry_at", None),
            provider=provider,
            provider_resource_id=interface.external_id,
        )

    def to_dict(self) -> dict:
        data = {
            "interface_id": str(self.interface_id),
            "tenant_id": str(self.tenant_id),
            "resource_id": str(self.resource_id),
            "name": self.name,
            "interface_type": self.interface_type,
            "description": self.description,
            "mac_address": self.mac_address,
            "vlan_id": self.vlan_id,
            "admin_state": self.admin_state,
            "oper_state": self.oper_state,
            "admin_status_raw": self.admin_status_raw,
            "oper_status_raw": self.oper_status_raw,
            "speed_bps": self.speed_bps,
            "negotiated_speed_mbps": self.negotiated_speed_mbps,
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
            "rx_bps": self.rx_bps,
            "tx_bps": self.tx_bps,
            "utilization_pct": self.utilization_pct,
            "errors_in": self.errors_in,
            "errors_out": self.errors_out,
            "discards_in": self.discards_in,
            "discards_out": self.discards_out,
            "packet_loss_pct": self.packet_loss_pct,
            "latency_ms": self.latency_ms,
            "jitter_ms": self.jitter_ms,
            "uptime_seconds": self.uptime_seconds,
            "last_state_change_at": (
                self.last_state_change_at.isoformat()
                if self.last_state_change_at else None
            ),
            "last_telemetry_at": (
                self.last_telemetry_at.isoformat() if self.last_telemetry_at else None
            ),
            "provider": self.provider,
            "provider_resource_id": self.provider_resource_id,
        }
        # ``measured`` lists the rate/latency fields that actually carry a value,
        # so the UI can render "not measured by this provider" instead of 0.
        data["measured"] = sorted(
            key for key in _MEASURED_IFACE_FIELDS if data.get(key) is not None
        )
        return data


# ---------------------------------------------------------------------------
# WAN / SD-WAN members (spec section 18)
# ---------------------------------------------------------------------------
#: ``TopologyLink.link_type`` values that describe a WAN transport.
WAN_LINK_TYPES: frozenset[str] = frozenset({"wan", "serial", "mpls", "backbone"})
#: ``TopologyLink.link_type`` values that describe a tunnel.
VPN_LINK_TYPES: frozenset[str] = frozenset({"vpn_tunnel"})


def _wan_role(link) -> str:
    """active | standby | backup | disabled, from what the row actually says.

    An explicitly recorded role wins; otherwise a link that declares a
    redundancy group is a standby member BY DECLARATION (that is what the field
    is for), and a link with neither is reported as active because it is
    carrying traffic. Nothing is invented for a disabled link.
    """
    attributes = dict(getattr(link, "attributes", None) or {})
    declared = str(attributes.get("role") or "").strip().lower()
    if declared in V.WAN_ROLES:
        return declared
    if link.oper_status and V.operational_state(link.oper_status) == V.OPER_STATE_DOWN:
        return V.WAN_ROLE_DISABLED if link.admin_status == "down" else V.WAN_ROLE_ACTIVE
    if getattr(link, "redundancy_group", None):
        return V.WAN_ROLE_STANDBY
    return V.WAN_ROLE_ACTIVE


@dataclass(frozen=True)
class WanMember:
    """One WAN/SD-WAN path member for a resource or site."""

    link_id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    site_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    interface_id: uuid.UUID | None = None
    link_type: str = "wan"
    role: str = V.WAN_ROLE_ACTIVE
    oper_state: str = V.OPER_STATE_UNKNOWN
    oper_status_raw: str = "unknown"
    isp: str | None = None
    circuit_id: str | None = None
    is_sdwan_member: bool = False
    bandwidth_mbps: float | None = None
    latency_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None
    utilization_pct: float | None = None
    availability_pct: float | None = None
    sla_target_latency_ms: float | None = None
    sla_target_packet_loss_pct: float | None = None
    failover_events: int | None = None
    last_state_change_at: datetime | None = None
    provider: str | None = None

    @classmethod
    def from_link(cls, link) -> "WanMember":
        attributes = dict(getattr(link, "attributes", None) or {})
        return cls(
            link_id=link.id,
            tenant_id=link.tenant_id,
            name=link.name,
            device_id=link.device_a_id,
            interface_id=link.source_interface_id,
            link_type=link.link_type,
            role=_wan_role(link),
            oper_state=V.operational_state(link.oper_status),
            oper_status_raw=link.oper_status,
            isp=link.provider,
            circuit_id=link.circuit_id,
            is_sdwan_member=bool(attributes.get("sdwan_member"))
            or bool(attributes.get("sdwan_profile")),
            bandwidth_mbps=link.bandwidth_mbps,
            latency_ms=link.latency_ms,
            jitter_ms=_as_float(attributes.get("jitter_ms")),
            packet_loss_pct=link.packet_loss_pct,
            utilization_pct=link.utilization_pct,
            availability_pct=_as_float(attributes.get("availability_pct")),
            sla_target_latency_ms=_as_float(attributes.get("sla_latency_ms")),
            sla_target_packet_loss_pct=_as_float(attributes.get("sla_packet_loss_pct")),
            failover_events=_as_int(attributes.get("failover_events")),
            last_state_change_at=link.last_state_change_at,
            provider=link.provider,
        )

    def to_dict(self) -> dict:
        return {
            "link_id": str(self.link_id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "site_id": str(self.site_id) if self.site_id else None,
            "device_id": str(self.device_id) if self.device_id else None,
            "interface_id": str(self.interface_id) if self.interface_id else None,
            "link_type": self.link_type,
            "role": self.role,
            "oper_state": self.oper_state,
            "oper_status_raw": self.oper_status_raw,
            "isp": self.isp,
            "circuit_id": self.circuit_id,
            "is_sdwan_member": self.is_sdwan_member,
            "bandwidth_mbps": self.bandwidth_mbps,
            "latency_ms": self.latency_ms,
            "jitter_ms": self.jitter_ms,
            "packet_loss_pct": self.packet_loss_pct,
            "utilization_pct": self.utilization_pct,
            "availability_pct": self.availability_pct,
            "sla_target_latency_ms": self.sla_target_latency_ms,
            "sla_target_packet_loss_pct": self.sla_target_packet_loss_pct,
            "failover_events": self.failover_events,
            "last_state_change_at": (
                self.last_state_change_at.isoformat()
                if self.last_state_change_at else None
            ),
            "provider": self.provider,
            # Which measured fields are real for this member - the API returns
            # this so a client never renders a missing metric as 0.
            "measured": sorted(
                key for key in (
                    "bandwidth_mbps", "latency_ms", "jitter_ms",
                    "packet_loss_pct", "utilization_pct", "availability_pct",
                ) if getattr(self, key) is not None
            ),
        }


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# VPN / tunnels (spec section 19)
# ---------------------------------------------------------------------------
_VPN_TYPE_ALIASES: dict[str, str] = {
    "ipsec": V.VPN_TYPE_IPSEC,
    "ipsec_vpn": V.VPN_TYPE_IPSEC,
    "site-to-site": V.VPN_TYPE_SITE_TO_SITE,
    "site_to_site": V.VPN_TYPE_SITE_TO_SITE,
    "s2s": V.VPN_TYPE_SITE_TO_SITE,
    "remote-access": V.VPN_TYPE_REMOTE_ACCESS,
    "remote_access": V.VPN_TYPE_REMOTE_ACCESS,
    "ssl_vpn": V.VPN_TYPE_SSL,
    "ssl": V.VPN_TYPE_SSL,
    "tunnel": V.VPN_TYPE_TUNNEL,
    "gre": V.VPN_TYPE_TUNNEL,
    "dmvpn": V.VPN_TYPE_TUNNEL,
}


def normalize_vpn_type(value: str | None) -> str:
    """Provider tunnel spelling -> canonical VPN type. Unknown stays ``other``."""
    token = (value or "").strip().lower().replace(" ", "_")
    return _VPN_TYPE_ALIASES.get(token, V.VPN_TYPE_OTHER if token else V.VPN_TYPE_OTHER)


@dataclass(frozen=True)
class VpnTunnel:
    """One VPN/tunnel, provider-neutral."""

    link_id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    site_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    interface_id: uuid.UUID | None = None
    vpn_type: str = V.VPN_TYPE_OTHER
    oper_state: str = V.OPER_STATE_UNKNOWN
    oper_status_raw: str = "unknown"
    local_endpoint: str | None = None
    remote_endpoint: str | None = None
    peer: str | None = None
    uptime_seconds: float | None = None
    rx_bytes: float | None = None
    tx_bytes: float | None = None
    latency_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None
    last_state_change_at: datetime | None = None
    provider: str | None = None
    is_encrypted: bool = True

    @classmethod
    def from_link(cls, link) -> "VpnTunnel":
        attributes = dict(getattr(link, "attributes", None) or {})
        return cls(
            link_id=link.id,
            tenant_id=link.tenant_id,
            name=link.vpn_name or link.name,
            device_id=link.device_a_id,
            interface_id=link.source_interface_id,
            vpn_type=normalize_vpn_type(link.vpn_type or link.link_type),
            oper_state=V.operational_state(link.oper_status),
            oper_status_raw=link.oper_status,
            local_endpoint=attributes.get("local_gateway")
            or attributes.get("local_endpoint"),
            remote_endpoint=attributes.get("remote_gateway")
            or attributes.get("remote_endpoint"),
            peer=attributes.get("peer") or attributes.get("remote_gateway"),
            uptime_seconds=_as_float(attributes.get("uptime_seconds")),
            rx_bytes=_as_float(attributes.get("rx_bytes")),
            tx_bytes=_as_float(attributes.get("tx_bytes")),
            latency_ms=link.latency_ms,
            jitter_ms=_as_float(attributes.get("jitter_ms")),
            packet_loss_pct=link.packet_loss_pct,
            last_state_change_at=link.last_state_change_at,
            provider=link.provider,
            is_encrypted=bool(attributes.get("encrypted", True)),
        )

    def to_dict(self) -> dict:
        return {
            "link_id": str(self.link_id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "site_id": str(self.site_id) if self.site_id else None,
            "device_id": str(self.device_id) if self.device_id else None,
            "interface_id": str(self.interface_id) if self.interface_id else None,
            "vpn_type": self.vpn_type,
            "oper_state": self.oper_state,
            "oper_status_raw": self.oper_status_raw,
            "local_endpoint": self.local_endpoint,
            "remote_endpoint": self.remote_endpoint,
            "peer": self.peer,
            "uptime_seconds": self.uptime_seconds,
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
            "latency_ms": self.latency_ms,
            "jitter_ms": self.jitter_ms,
            "packet_loss_pct": self.packet_loss_pct,
            "last_state_change_at": (
                self.last_state_change_at.isoformat()
                if self.last_state_change_at else None
            ),
            "provider": self.provider,
            "is_encrypted": self.is_encrypted,
            "measured": sorted(
                key for key in (
                    "uptime_seconds", "rx_bytes", "tx_bytes", "latency_ms",
                    "jitter_ms", "packet_loss_pct",
                ) if getattr(self, key) is not None
            ),
        }


# ---------------------------------------------------------------------------
# Routing (spec section 20)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteEntry:
    route_id: uuid.UUID
    tenant_id: uuid.UUID
    resource_id: uuid.UUID
    destination: str
    protocol: str = V.ROUTE_PROTOCOL_OTHER
    vrf: str = "-"
    prefix_length: int | None = None
    next_hop: str | None = None
    gateway: str | None = None
    interface_id: uuid.UUID | None = None
    outgoing_interface: str | None = None
    metric: int | None = None
    administrative_distance: int | None = None
    priority: int | None = None
    state: str = "unknown"
    is_best: bool = False
    learned: bool | None = None
    advertised: bool | None = None
    age_seconds: float | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_change_at: datetime | None = None
    provider: str | None = None

    @classmethod
    def from_route(cls, route) -> "RouteEntry":
        return cls(
            route_id=route.id,
            tenant_id=route.tenant_id,
            resource_id=route.resource_id,
            destination=route.destination,
            protocol=route.protocol,
            vrf=route.vrf,
            prefix_length=route.prefix_length,
            next_hop=route.next_hop,
            gateway=route.gateway,
            interface_id=route.interface_id,
            outgoing_interface=route.outgoing_interface,
            metric=route.metric,
            administrative_distance=route.administrative_distance,
            priority=route.priority,
            state=route.state,
            is_best=route.is_best,
            learned=route.learned,
            advertised=route.advertised,
            age_seconds=route.age_seconds,
            first_seen_at=route.first_seen_at,
            last_seen_at=route.last_seen_at,
            last_change_at=route.last_change_at,
            provider=route.provider,
        )

    def to_dict(self) -> dict:
        return {
            "route_id": str(self.route_id),
            "tenant_id": str(self.tenant_id),
            "resource_id": str(self.resource_id),
            "destination": self.destination,
            "prefix_length": self.prefix_length,
            "protocol": self.protocol,
            "vrf": self.vrf,
            "next_hop": self.next_hop,
            "gateway": self.gateway,
            "interface_id": str(self.interface_id) if self.interface_id else None,
            "outgoing_interface": self.outgoing_interface,
            "metric": self.metric,
            "administrative_distance": self.administrative_distance,
            "priority": self.priority,
            "state": self.state,
            "is_best": self.is_best,
            "learned": self.learned,
            "advertised": self.advertised,
            "age_seconds": self.age_seconds,
            "first_seen_at": (
                self.first_seen_at.isoformat() if self.first_seen_at else None
            ),
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at else None
            ),
            "last_change_at": (
                self.last_change_at.isoformat() if self.last_change_at else None
            ),
            "provider": self.provider,
        }


@dataclass(frozen=True)
class BgpNeighborState:
    neighbor_id: uuid.UUID
    tenant_id: uuid.UUID
    resource_id: uuid.UUID
    neighbor_ip: str
    vrf: str = "-"
    local_ip: str | None = None
    remote_as: str | None = None
    local_as: str | None = None
    description: str | None = None
    state: str = V.BGP_STATE_UNKNOWN
    admin_status: str = "unknown"
    uptime_seconds: float | None = None
    prefixes_received: int | None = None
    prefixes_advertised: int | None = None
    last_state_change_at: datetime | None = None
    last_seen_at: datetime | None = None
    provider: str | None = None

    @property
    def is_established(self) -> bool:
        return self.state == V.BGP_STATE_ESTABLISHED

    @classmethod
    def from_neighbor(cls, neighbor) -> "BgpNeighborState":
        return cls(
            neighbor_id=neighbor.id,
            tenant_id=neighbor.tenant_id,
            resource_id=neighbor.resource_id,
            neighbor_ip=neighbor.neighbor_ip,
            vrf=neighbor.vrf,
            local_ip=neighbor.local_ip,
            remote_as=neighbor.remote_as,
            local_as=neighbor.local_as,
            description=neighbor.description,
            state=V.normalize_bgp_state(neighbor.state),
            admin_status=neighbor.admin_status,
            uptime_seconds=neighbor.uptime_seconds,
            prefixes_received=neighbor.prefixes_received,
            prefixes_advertised=neighbor.prefixes_advertised,
            last_state_change_at=neighbor.last_state_change_at,
            last_seen_at=neighbor.last_seen_at,
            provider=neighbor.provider,
        )

    def to_dict(self) -> dict:
        return {
            "neighbor_id": str(self.neighbor_id),
            "tenant_id": str(self.tenant_id),
            "resource_id": str(self.resource_id),
            "neighbor_ip": self.neighbor_ip,
            "vrf": self.vrf,
            "local_ip": self.local_ip,
            "remote_as": self.remote_as,
            "local_as": self.local_as,
            "description": self.description,
            "state": self.state,
            "is_established": self.is_established,
            "admin_status": self.admin_status,
            "uptime_seconds": self.uptime_seconds,
            "prefixes_received": self.prefixes_received,
            "prefixes_advertised": self.prefixes_advertised,
            "last_state_change_at": (
                self.last_state_change_at.isoformat()
                if self.last_state_change_at else None
            ),
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at else None
            ),
            "provider": self.provider,
        }


# ---------------------------------------------------------------------------
# SD-WAN path evaluation (spec section 18)
# ---------------------------------------------------------------------------
#: Metrics a WAN member MUST have before it can be scored. Scoring on defaults
#: would invent a bad score for a member nobody measured (the existing engine's
#: ``WanPath.from_dict`` falls back to *worst case* for missing numbers), so a
#: member without these is excluded WITH A REASON instead of being scored.
SDWAN_REQUIRED_METRICS: tuple[str, ...] = ("latency_ms", "packet_loss_pct")


def evaluate_sdwan_members(members: list[WanMember]) -> dict:
    """Score SD-WAN members using the EXISTING network-intelligence engine.

    Returns the engine's own decision payload plus an explicit
    ``not_evaluated`` list naming each member that was excluded and why. Never
    produces a score for a member with no measured telemetry.
    """
    from app.services.network_intelligence import evaluate_paths

    scorable: list[dict] = []
    not_evaluated: list[dict] = []
    for member in members:
        missing = [
            metric for metric in SDWAN_REQUIRED_METRICS
            if getattr(member, metric) is None
        ]
        if missing:
            not_evaluated.append({
                "link_id": str(member.link_id),
                "name": member.name,
                "reason": V.REASON_NOT_YET_COLLECTED,
                "missing_metrics": missing,
            })
            continue
        scorable.append({
            "name": member.name,
            "latency_ms": member.latency_ms,
            "jitter_ms": member.jitter_ms or 0.0,
            "packet_loss_pct": member.packet_loss_pct,
            "bandwidth_mbps": member.bandwidth_mbps or 0.0,
            "available": member.oper_state == V.OPER_STATE_UP,
        })

    if not scorable:
        return {
            "available": False,
            "supported": False,
            "status": V.COLLECTION_NOT_AVAILABLE,
            "reason": V.REASON_NO_TELEMETRY_EVER,
            "not_evaluated": not_evaluated,
        }

    evaluation = evaluate_paths(scorable)
    return {
        "available": True,
        "supported": True,
        "status": V.COLLECTION_COLLECTED,
        "evaluation": evaluation,
        "not_evaluated": not_evaluated,
    }
