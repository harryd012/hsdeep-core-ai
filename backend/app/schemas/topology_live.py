"""
Live topology API schemas.

Typed mirror of ``TopologyLiveService.build()``. The node/edge payloads extend the
existing graph contract (``schemas/topology_graph.py``) with a ``live`` block, so
the two responses can never drift apart in naming.

Deliberately permissive where the underlying column is: ``metadata`` and
``attributes`` are recorded JSON and stay free-form. Everything a consumer needs
to make an operational decision - state, freshness, interfaces, traffic,
provenance - is typed.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LiveInterfaceOut(BaseModel):
    """One interface, exactly as stored. ``None`` means not measured."""

    id: str
    name: str
    interface_type: str | None = None
    oper_status: str | None = None
    admin_status: str | None = None
    vlan_id: int | None = None
    mac_address: str | None = None
    speed_bps: float | None = None
    rx_bps: float | None = None
    tx_bps: float | None = None
    utilization_pct: float | None = None
    in_errors: float | None = None
    out_errors: float | None = None
    discards_in: float | None = None
    discards_out: float | None = None
    last_telemetry_at: str | None = None


class InterfaceSummaryOut(BaseModel):
    """Per-device interface counts and traffic totals.

    ``has_telemetry`` is what separates "measured zero" from "never measured":
    when it is false the totals are ``None``, never 0.
    """

    total: int = 0
    up: int = 0
    down: int = 0
    has_telemetry: bool = False
    rx_bps: float | None = None
    tx_bps: float | None = None
    last_telemetry_at: str | None = None


class FreshnessOut(BaseModel):
    """Telemetry freshness, classified with the platform's shared thresholds."""

    state: str
    age_seconds: float | None = None
    last_telemetry_at: str | None = None


class RecognitionOut(BaseModel):
    """Whether this node is an IDENTIFIED resource or evidence only."""

    status: str
    device_type: str | None = None


class NodeIdentityOut(BaseModel):
    """Stored identity columns. Nothing here is inferred from an address or name."""

    vendor: str | None = None
    model: str | None = None
    device_type: str | None = None
    device_type_raw: str | None = None
    ip: str | None = None
    hostname: str | None = None
    serial: str | None = None


class LiveNodeBlockOut(BaseModel):
    """The live dimension of one device node."""

    operational_state: str
    status_raw: str | None = None
    lifecycle_state: str | None = None
    site_id: str | None = None
    recognition: RecognitionOut
    identity: NodeIdentityOut
    freshness: FreshnessOut
    interfaces: InterfaceSummaryOut
    relationship_count: int = 0


class LiveNodeOut(BaseModel):
    """A graph node plus its live block (``None`` for site/source nodes)."""

    id: str
    type: str
    name: str
    health: str | None = None
    status: str | None = None
    site_id: str | None = None
    device_type: str | None = None
    device_type_raw: str | None = None
    is_network_device: bool | None = None
    source_type: str | None = None
    code: str | None = None
    region: str | None = None
    site_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    has_coordinates: bool | None = None
    monitoring_source: dict[str, Any] | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    live: LiveNodeBlockOut | None = None


class LiveLinkOut(BaseModel):
    """Capacity / utilisation / traffic totals for one edge.

    ``utilization_source`` records whether the percentage was recorded by the
    collector (``recorded``) or derived from that endpoint's own rx/tx and speed
    (``computed``); ``None`` means neither was possible.
    """

    capacity_bps: float | None = None
    utilization_pct: float | None = None
    utilization_source: str | None = None
    has_telemetry: bool = False
    rx_bps: float | None = None
    tx_bps: float | None = None
    observed_at: str | None = None
    #: Freshness of the link's telemetry, classified by the backend with the same
    #: thresholds the resource views use - the UI never applies its own.
    freshness: FreshnessOut


class LiveEdgeBlockOut(BaseModel):
    """The live dimension of one edge. Endpoints are ``None`` when the
    relationship recorded no interface (or it was never collected)."""

    local_interface_name: str | None = None
    remote_interface_name: str | None = None
    local_interface: LiveInterfaceOut | None = None
    remote_interface: LiveInterfaceOut | None = None
    link: LiveLinkOut
    status_raw: str | None = None
    classification: str | None = None
    is_inferred: bool = False


class LiveEdgeOut(BaseModel):
    """A graph edge plus its live block. Provenance fields are the graph's own."""

    id: str
    source_id: str
    target_id: str
    relationship_type: str
    unknown_relationship_type: bool = False
    relationship_subtype: str | None = None
    label: str | None = None
    evidence: str
    discovered_by: str | None = None
    is_inferred: bool = False
    confidence: float | None = None
    status: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    weight: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    live: LiveEdgeBlockOut


class TopologyScopeOut(BaseModel):
    """Which slice of the tenant this response covers."""

    site_id: str | None = None
    filtered: bool = False
    nodes_hidden: int = 0
    edges_hidden: int = 0


class TopologyLiveSummaryOut(BaseModel):
    """Header counts. ``extra="allow"`` so the graph's own summary fields
    (nodes_total, edges_total, sensors_total, ...) pass through unchanged instead
    of being silently dropped."""

    model_config = ConfigDict(extra="allow")

    devices_total: int = 0
    sites_total: int = 0
    monitoring_sources_total: int = 0
    links_total: int = 0
    unidentified_devices_total: int = 0
    by_health: dict[str, int] = Field(default_factory=dict)
    by_operational_state: dict[str, int] = Field(default_factory=dict)
    by_freshness: dict[str, int] = Field(default_factory=dict)
    interfaces_total: int = 0
    interfaces_up: int = 0
    interfaces_down: int = 0
    traffic_measured: bool = False
    rx_bps_total: float | None = None
    tx_bps_total: float | None = None


class ViewFreshnessOut(FreshnessOut):
    """Freshness of the whole view, plus how many devices actually reported."""

    devices_reporting: int = 0


class TopologyLiveOut(BaseModel):
    """The live topology read model: site -> device -> interface -> telemetry."""

    tenant_id: str
    generated_at: str
    status: str | None = None
    scope: TopologyScopeOut
    summary: TopologyLiveSummaryOut
    telemetry_freshness: ViewFreshnessOut
    nodes: list[LiveNodeOut] = Field(default_factory=list)
    edges: list[LiveEdgeOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
