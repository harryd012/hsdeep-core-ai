"""
Live operational topology read model (site -> device -> interface -> telemetry).

ONE composed response for the live topology view, so the UI never fans out into
per-device interface/telemetry calls. This module is a READ MODEL: it adds no new
domain concepts and owns no thresholds.

Reuse, not duplication
----------------------
* Nodes and edges come from ``TopologyGraphService.build()`` - the single owner
  of that contract. Nothing here re-derives a node, invents an edge, or decides
  what a relationship type means.
* Availability comes from ``vocabulary.operational_state()`` and freshness from
  ``operational_state.freshness_state_for_age()``: the same mappings and the same
  timer thresholds the resource views already use, so the topology cannot
  disagree with them.
* Evidence classification comes from the vocabulary tables (Phase 9.4).

Honesty rules carried over from the graph contract
--------------------------------------------------
* Every added value is a stored column: interface oper/admin status, speed,
  rx/tx bps, errors, discards, utilisation, timestamps, and the device's
  lifecycle/health columns.
* A missing value stays ``None``. A total is ``None`` - not 0 - when nothing was
  measured, so "no traffic" and "no telemetry" can never be confused.
* ``capacity_bps`` is the slower of the two recorded endpoint speeds; it is None
  unless at least one endpoint records a speed. Interface capacity is never
  assumed from a port name or a device model.

Query budget: a CONSTANT number of round trips per request (graph build, device
list, one batched interface list), independent of how many nodes the tenant has.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_types import UNCLASSIFIED
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_repository import InterfaceRepository
from app.services.infrastructure_intelligence import operational_state as O
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.topology_graph_service import (
    PREFIX_DEVICE,
    PREFIX_SITE,
    TopologyGraphService,
)

#: Bounded fetch, matching TopologyGraphService._FETCH_LIMIT.
MAX_FETCH_LIMIT = 100_000

#: Interface oper_status spellings that mean carrying / not carrying traffic.
_OPER_UP = "up"
_OPER_DOWN = "down"

#: Canonical device_type values that mean "we could not classify this resource".
#: ``UNCLASSIFIED`` is the honest bucket ``normalize_device_type()`` produces for
#: None / "" / "other" / "unknown" / "network" / "generic", so a device carrying
#: it is valid evidence rather than an identified resource. The set is a constant
#: so no caller re-derives the rule from a spelling.
_UNCLASSIFIED_TYPES: frozenset[str] = frozenset({UNCLASSIFIED, "unknown"})

#: Relationship keys under which a writer records the participating interfaces.
#: Both spellings are accepted because both exist in the platform: the discovery
#: contract uses local/remote (``NeighborEvidence``), and direction-neutral read
#: models use source/target. A relationship that records neither simply has no
#: interface telemetry - nothing is guessed from the device pair.
_LOCAL_INTERFACE_KEYS = ("local_interface", "source_interface")
_REMOTE_INTERFACE_KEYS = ("remote_interface", "target_interface")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def interface_facts(iface) -> dict[str, Any]:
    """Per-interface live facts, verbatim. No arithmetic, no defaults."""
    return {
        "id": str(iface.id),
        "name": iface.name,
        "interface_type": iface.interface_type,
        "oper_status": iface.oper_status,
        "admin_status": iface.admin_status,
        "vlan_id": iface.vlan_id,
        "mac_address": iface.mac_address,
        "speed_bps": iface.speed_bps,
        "rx_bps": iface.rx_bps,
        "tx_bps": iface.tx_bps,
        "utilization_pct": iface.utilization_pct,
        "in_errors": iface.in_errors,
        "out_errors": iface.out_errors,
        "discards_in": iface.discards_in,
        "discards_out": iface.discards_out,
        "last_telemetry_at": _iso(iface.last_telemetry_at),
    }


def interface_summary(ifaces: Sequence[Any]) -> dict[str, Any]:
    """Counts and traffic totals for one device, over REAL telemetry only.

    ``has_telemetry`` distinguishes the two ways a total can be absent: no
    interface has ever reported rx/tx (``False``, totals ``None``) versus
    measured zero (``True``, totals ``0.0``). The UI renders those differently,
    which is the whole point of returning ``None`` instead of 0.
    """
    up = down = 0
    rx_total = 0.0
    tx_total = 0.0
    has_telemetry = False
    newest: datetime | None = None

    for iface in ifaces:
        oper = (iface.oper_status or "").strip().lower()
        if oper == _OPER_UP:
            up += 1
        elif oper == _OPER_DOWN:
            down += 1

        if iface.rx_bps is not None or iface.tx_bps is not None:
            has_telemetry = True
        rx_total += iface.rx_bps or 0.0
        tx_total += iface.tx_bps or 0.0

        stamp = iface.last_telemetry_at
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp

    return {
        "total": len(ifaces),
        "up": up,
        "down": down,
        "has_telemetry": has_telemetry,
        "rx_bps": rx_total if has_telemetry else None,
        "tx_bps": tx_total if has_telemetry else None,
        "last_telemetry_at": _iso(newest),
    }


def device_freshness(device, ifaces: Sequence[Any], *, now: datetime) -> dict[str, Any]:
    """Newest telemetry we hold for a device, classified with shared thresholds.

    The newest timestamp wins - the device row's own column or any interface's.
    When neither exists the state is ``never_collected`` rather than a guessed
    bucket, so a device nobody polls is never presented as live.
    """
    stamps = [device.last_telemetry_at]
    stamps.extend(iface.last_telemetry_at for iface in ifaces)
    present = [stamp for stamp in stamps if stamp is not None]
    if not present:
        return {
            "state": V.FRESHNESS_NEVER_COLLECTED,
            "age_seconds": None,
            "last_telemetry_at": None,
        }
    newest = max(present)
    age = O.age_seconds(newest, now=now)
    return {
        "state": O.freshness_state_for_age(age),
        "age_seconds": None if age is None else round(age, 3),
        "last_telemetry_at": _iso(newest),
    }


def live_node(
    *,
    device,
    graph_node: dict[str, Any],
    ifaces: Sequence[Any],
    relationship_count: int,
    now: datetime,
) -> dict[str, Any]:
    """Additive live block for ONE device node.

    ``recognition`` reuses the platform's existing rule (discovery.service):
    a resource whose canonical device type is ``unknown`` is UNIDENTIFIED - it is
    valid evidence, not a classified resource. The UI keys its "Unknown Resource"
    presentation off this, so a vendor is never inferred from an address or a
    name. ``identity`` carries only stored columns.
    """
    device_type = graph_node.get("device_type")
    identified = bool(device_type) and device_type not in _UNCLASSIFIED_TYPES
    return {
        "operational_state": V.operational_state(device.status),
        "status_raw": device.status,
        "lifecycle_state": device.lifecycle_state,
        "site_id": str(device.site_id) if device.site_id else None,
        "recognition": {
            "status": V.RECOGNITION_IDENTIFIED if identified else V.RECOGNITION_UNKNOWN,
            "device_type": device_type,
        },
        "identity": {
            "vendor": device.vendor,
            "model": device.model,
            "device_type": device_type,
            "device_type_raw": device.device_type,
            "ip": device.ip_address,
            "hostname": device.hostname,
            "serial": device.serial_number,
        },
        "freshness": device_freshness(device, ifaces, now=now),
        "interfaces": interface_summary(ifaces),
        "relationship_count": relationship_count,
    }


def edge_interface_names(edge: dict[str, Any]) -> tuple[str | None, str | None]:
    """Interfaces RECORDED on a relationship, as ``(local, remote)``.

    Never guessed: only the keys a writer can actually record are read, and the
    result is ``(None, None)`` when the relationship carries none. A relationship
    with no recorded interfaces simply has no interface telemetry.
    """
    meta = edge.get("metadata")
    if not isinstance(meta, dict):
        return (None, None)
    local = next((meta[key] for key in _LOCAL_INTERFACE_KEYS if meta.get(key)), None)
    remote = next((meta[key] for key in _REMOTE_INTERFACE_KEYS if meta.get(key)), None)
    return (
        str(local) if local is not None else None,
        str(remote) if remote is not None else None,
    )


def _find_interface(ifaces: Sequence[Any], name: str | None):
    """The interface with this recorded name, or None. Names match as stored."""
    if not name:
        return None
    for iface in ifaces:
        if iface.name == name:
            return iface
    return None


def _utilisation(
    local: dict[str, Any] | None, remote: dict[str, Any] | None
) -> tuple[float | None, str | None]:
    """``(percent, source)`` for one link - ``(None, None)`` when unknowable.

    A collector-written ``utilization_pct`` always wins: it is the provider's own
    measurement. Only when neither endpoint recorded one is a value derived, and
    then only from that endpoint's own numbers (both directions measured and a
    known speed), as ``max(rx, tx) / speed`` - the standard interface-utilisation
    reading, which cannot exceed 100%. ``source`` says which path produced it, so
    the UI can label a derived number as derived.
    """
    for side in (local, remote):
        if side and side.get("utilization_pct") is not None:
            return (float(side["utilization_pct"]), "recorded")

    for side in (local, remote):
        if not side:
            continue
        speed = side.get("speed_bps")
        rx = side.get("rx_bps")
        tx = side.get("tx_bps")
        if not speed or speed <= 0 or rx is None or tx is None:
            continue
        return (round(min(max(rx, tx) / speed * 100.0, 100.0), 3), "computed")

    return (None, None)


def link_freshness(observed_at: str | None, *, now: datetime) -> dict[str, Any]:
    """Freshness of a link's telemetry, using the platform's shared thresholds.

    Computed here rather than in the UI so a "stale link" can never disagree with
    the device/resource views, which apply the same numbers. Returns
    ``never_collected`` when no endpoint ever reported a timestamp - which is NOT
    the same as ``stale``: a relationship with no interface telemetry can still
    be active, and its state must not be downgraded for that reason.
    """
    if not observed_at:
        return {"state": V.FRESHNESS_NEVER_COLLECTED, "age_seconds": None}
    age = O.age_seconds(datetime.fromisoformat(observed_at), now=now)
    return {
        "state": O.freshness_state_for_age(age),
        "age_seconds": None if age is None else round(age, 3),
    }


def link_facts(
    local: dict[str, Any] | None,
    remote: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capacity, utilisation, traffic totals and freshness for ONE edge.

    * ``capacity_bps`` is the slower of the two recorded endpoint speeds, because
      a link cannot be faster than its slower end. None unless at least one
      endpoint records a speed - capacity is never assumed from a port name or a
      device model.
    * traffic totals are ``None`` (not 0) when neither endpoint reported traffic,
      so "no traffic" and "no telemetry" stay distinguishable.
    """
    speeds = [
        side["speed_bps"]
        for side in (local, remote)
        if side and side.get("speed_bps") is not None
    ]
    has_traffic = any(
        side and (side.get("rx_bps") is not None or side.get("tx_bps") is not None)
        for side in (local, remote)
    )
    rx_total = sum(side.get("rx_bps") or 0.0 for side in (local, remote) if side)
    tx_total = sum(side.get("tx_bps") or 0.0 for side in (local, remote) if side)
    stamps = [
        side.get("last_telemetry_at")
        for side in (local, remote)
        if side and side.get("last_telemetry_at")
    ]
    observed_at = max(stamps) if stamps else None
    util, util_source = _utilisation(local, remote)
    return {
        "capacity_bps": min(speeds) if speeds else None,
        "utilization_pct": util,
        "utilization_source": util_source,
        "has_telemetry": has_traffic,
        "rx_bps": rx_total if has_traffic else None,
        "tx_bps": tx_total if has_traffic else None,
        "observed_at": observed_at,
        "freshness": link_freshness(observed_at, now=now or datetime.now(timezone.utc)),
    }


def live_edge(
    edge: dict[str, Any],
    interfaces_by_device: dict[str, Sequence[Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Additive live block for ONE edge.

    Interface telemetry is looked up from the interfaces the relationship itself
    recorded. When it recorded none, or the named interface was never collected,
    the endpoint block is ``None`` and the link simply has no telemetry - the
    edge is still drawn, because the RELATIONSHIP is real even when its
    interface detail is missing.
    """
    local_name, remote_name = edge_interface_names(edge)
    local = _find_interface(
        interfaces_by_device.get(edge.get("source_id"), ()), local_name
    )
    remote = _find_interface(
        interfaces_by_device.get(edge.get("target_id"), ()), remote_name
    )
    local_facts = interface_facts(local) if local is not None else None
    remote_facts = interface_facts(remote) if remote is not None else None
    return {
        "local_interface_name": local_name,
        "remote_interface_name": remote_name,
        "local_interface": local_facts,
        "remote_interface": remote_facts,
        "link": link_facts(local_facts, remote_facts, now=now),
        "status_raw": edge.get("status"),
        "classification": V.relationship_evidence_classification(
            edge.get("discovered_by")
        ),
        "is_inferred": bool(edge.get("is_inferred")),
    }


def _bump(mapping: dict[str, int], key: str | None) -> None:
    if key:
        mapping[key] = mapping.get(key, 0) + 1


def build_summary(nodes: Sequence[dict[str, Any]], edges: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Counts and traffic totals for exactly the nodes/edges being returned.

    Every count is derived from the returned graph, so the header can never
    disagree with the canvas. Traffic totals are summed across DEVICES (a link
    reports both of its ends, so summing interfaces would double-count), and they
    are ``None`` - not 0 - when nothing was measured.
    """
    by_health: dict[str, int] = {}
    by_operational_state: dict[str, int] = {}
    by_freshness: dict[str, int] = {}
    devices = sites = sources = unidentified = 0
    interfaces_total = interfaces_up = interfaces_down = 0
    rx_total = tx_total = 0.0
    measured = False

    for node in nodes:
        kind = node.get("type")
        if kind == "site":
            sites += 1
            continue
        if kind == "monitoring_source":
            sources += 1
            continue
        if kind != "device":
            continue

        devices += 1
        _bump(by_health, node.get("health") or V.HEALTH_UNKNOWN)
        live = node.get("live")
        if not isinstance(live, dict):
            continue
        _bump(by_operational_state, live.get("operational_state"))
        _bump(by_freshness, (live.get("freshness") or {}).get("state"))
        if (live.get("recognition") or {}).get("status") == V.RECOGNITION_UNKNOWN:
            unidentified += 1
        ifaces = live.get("interfaces") or {}
        interfaces_total += ifaces.get("total") or 0
        interfaces_up += ifaces.get("up") or 0
        interfaces_down += ifaces.get("down") or 0
        if ifaces.get("has_telemetry"):
            measured = True
            rx_total += ifaces.get("rx_bps") or 0.0
            tx_total += ifaces.get("tx_bps") or 0.0

    return {
        "devices_total": devices,
        "sites_total": sites,
        "monitoring_sources_total": sources,
        "links_total": len(edges),
        "unidentified_devices_total": unidentified,
        "by_health": by_health,
        "by_operational_state": by_operational_state,
        "by_freshness": by_freshness,
        "interfaces_total": interfaces_total,
        "interfaces_up": interfaces_up,
        "interfaces_down": interfaces_down,
        "traffic_measured": measured,
        "rx_bps_total": rx_total if measured else None,
        "tx_bps_total": tx_total if measured else None,
    }


def view_freshness(stamps: Sequence[str | None], *, now: datetime) -> dict[str, Any]:
    """Freshness of the WHOLE view: newest telemetry across the devices shown.

    ``devices_reporting`` is reported alongside the age because "newest reading is
    two minutes old" means something very different when one device of forty has
    ever reported.
    """
    parsed = [
        datetime.fromisoformat(stamp) for stamp in stamps if stamp
    ]
    if not parsed:
        return {
            "state": V.FRESHNESS_NEVER_COLLECTED,
            "age_seconds": None,
            "last_telemetry_at": None,
            "devices_reporting": 0,
        }
    newest = max(parsed)
    age = O.age_seconds(newest, now=now)
    return {
        "state": O.freshness_state_for_age(age),
        "age_seconds": None if age is None else round(age, 3),
        "last_telemetry_at": newest.isoformat(),
        "devices_reporting": len(parsed),
    }


class TopologyLiveService:
    """Composes the existing graph with per-device interface and telemetry facts.

    The graph itself is built by ``TopologyGraphService``; this class attaches the
    live dimension to it and never re-derives a node, an edge or an edge's type.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.graph = TopologyGraphService(db)
        self.devices = DeviceRepository(db)
        self.interfaces = InterfaceRepository(db)

    @staticmethod
    def _device_id(node: dict[str, Any]) -> uuid.UUID | None:
        """The device UUID behind a node id, or None for site/source nodes."""
        node_id = str(node.get("id") or "")
        if not node_id.startswith(PREFIX_DEVICE):
            return None
        try:
            return uuid.UUID(node_id[len(PREFIX_DEVICE):])
        except ValueError:
            return None

    @staticmethod
    def _select(
        graph: dict[str, Any], site_id: uuid.UUID | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Apply the optional site filter BEFORE enrichment.

        Filtering first keeps the header counts equal to what is rendered. An edge
        is kept only when BOTH endpoints are kept, and edges hidden by the filter
        are reported in ``scope`` rather than silently dropped.
        """
        scope: dict[str, Any] = {
            "site_id": None,
            "filtered": False,
            "nodes_hidden": 0,
            "edges_hidden": 0,
        }
        if site_id is None:
            return list(graph["nodes"]), list(graph["edges"]), scope

        wanted = str(site_id)
        nodes = [
            node
            for node in graph["nodes"]
            if node.get("id") == f"{PREFIX_SITE}{wanted}" or node.get("site_id") == wanted
        ]
        kept = {node["id"] for node in nodes}
        edges = [
            edge
            for edge in graph["edges"]
            if edge["source_id"] in kept and edge["target_id"] in kept
        ]
        scope.update({
            "site_id": wanted,
            "filtered": True,
            "nodes_hidden": len(graph["nodes"]) - len(nodes),
            "edges_hidden": len(graph["edges"]) - len(edges),
        })
        return nodes, edges, scope

    async def build(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The live topology for one tenant, optionally bounded to one site."""
        generated_at = now or datetime.now(timezone.utc)

        # Constant number of round trips: graph build, device list, one batched
        # interface list - independent of how many nodes the tenant has.
        # Pass _FETCH_LIMIT to preserve the live service's existing unlimited
        # behaviour — only the HTTP endpoint uses the bounded limit/offset params.
        graph = await self.graph.build(tenant_id, limit=MAX_FETCH_LIMIT)
        devices = await self.devices.list(tenant_id, limit=MAX_FETCH_LIMIT)
        interfaces = await self.interfaces.list_for_devices(
            tenant_id, [device.id for device in devices]
        )

        device_by_id = {device.id: device for device in devices}
        ifaces_by_node: dict[str, list[Any]] = {}
        for iface in interfaces:
            ifaces_by_node.setdefault(
                f"{PREFIX_DEVICE}{iface.device_id}", []
            ).append(iface)

        nodes, edges, scope = self._select(graph, site_id)

        edge_counts: dict[str, int] = {}
        for edge in edges:
            for key in ("source_id", "target_id"):
                node_id = edge.get(key)
                if node_id:
                    edge_counts[node_id] = edge_counts.get(node_id, 0) + 1

        live_nodes: list[dict[str, Any]] = []
        for node in nodes:
            device = device_by_id.get(self._device_id(node))
            if node.get("type") != "device" or device is None:
                # Sites and monitoring sources carry no device telemetry, and a
                # device that vanished between the two reads is reported without a
                # live block rather than with guessed values.
                live_nodes.append({**node, "live": None})
                continue
            live_nodes.append({
                **node,
                "live": live_node(
                    device=device,
                    graph_node=node,
                    ifaces=ifaces_by_node.get(node["id"], []),
                    relationship_count=edge_counts.get(node["id"], 0),
                    now=generated_at,
                ),
            })

        live_edges = [
            {**edge, "live": live_edge(edge, ifaces_by_node, now=generated_at)}
            for edge in edges
        ]

        return {
            "tenant_id": str(tenant_id),
            "generated_at": generated_at.isoformat(),
            # The graph's own status is preserved verbatim, so a partial graph can
            # never be presented as complete.
            "status": graph.get("status"),
            "scope": scope,
            "summary": {**graph.get("summary", {}), **build_summary(live_nodes, live_edges)},
            "telemetry_freshness": view_freshness(
                [
                    ((node.get("live") or {}).get("freshness") or {}).get(
                        "last_telemetry_at"
                    )
                    for node in live_nodes
                    if node.get("type") == "device"
                ],
                now=generated_at,
            ),
            "nodes": live_nodes,
            "edges": live_edges,
            "limitations": list(graph.get("limitations", [])),
        }
