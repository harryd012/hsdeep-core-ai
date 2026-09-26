"""
Pydantic v2 schemas for the topology graph API.

Nodes and edges are intentionally permissive (`extra="allow"`): their exact
field set legitimately varies (a site node carries `latitude`/`has_coordinates`,
a device node carries `device_type`/`is_network_device`). Typing them strictly
would either silently drop real fields or force every absent field to be
emitted as a placeholder - and a placeholder is exactly what the data-trust
rule forbids. The fields that MUST exist on every node/edge are declared and
required; anything else passes through verbatim.

`summary` and `status` ARE fully typed, because they are the contract the UI
and the cross-page consistency checks depend on.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

TopologyStatus = Literal["ready", "partial", "not_discovered"]
NodeType = Literal["device", "site", "monitoring_source"]


class TopologyNodeOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: NodeType
    name: str
    #: Canonical Phase 2 health bucket. None for node kinds the overview, not
    #: the graph, is responsible for scoring (sites, monitoring sources).
    health: str | None = None
    status: str | None = None
    site_id: str | None = None
    metadata: dict[str, Any] = {}
    counts: dict[str, int] = {}


class TopologyEdgeOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    source_id: str
    target_id: str
    relationship_type: str
    #: Which verified source produced this edge. Always the
    #: ResourceRelationship row today.
    evidence: str
    #: Real NOT NULL column, returned as stored - never synthesised.
    confidence: float | None = None
    is_inferred: bool = False
    #: True when `relationship_type` is outside the model's declared
    #: vocabulary, so the UI can label it instead of trusting it.
    unknown_relationship_type: bool = False
    status: str | None = None
    metadata: dict[str, Any] = {}


class TopologyGraphSummaryOut(BaseModel):
    nodes_total: int
    edges_total: int
    #: Must equal the canonical overview's device total and
    #: DeviceRepository.count() - see the cross-page consistency tests.
    devices_total: int
    sites_total: int
    monitoring_sources_total: int
    interfaces_total: int
    sensors_total: int


class TopologyGraphOut(BaseModel):
    tenant_id: str
    generated_at: str
    status: TopologyStatus
    nodes: list[TopologyNodeOut]
    edges: list[TopologyEdgeOut]
    summary: TopologyGraphSummaryOut
    #: Human-readable statements of what is missing and why. When the graph is
    #: empty this is never empty - the UI must not show a blank canvas or imply
    #: the network is healthy.
    limitations: list[str]
