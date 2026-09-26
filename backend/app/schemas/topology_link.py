"""Schemas for the topology link API (Sections 17/22)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.topology_standard import (
    LINK_KINDS,
    LINK_STATUSES,
    ROUTING_PROTOCOLS,
    VPN_TYPES,
)


class TopologyLinkBase(BaseModel):
    device_a_id: uuid.UUID
    device_b_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    link_type: str = "other"
    source_interface_id: Optional[uuid.UUID] = None
    dest_interface_id: Optional[uuid.UUID] = None
    provider: Optional[str] = None
    circuit_id: Optional[str] = None
    external_id: Optional[str] = None
    bandwidth_mbps: Optional[float] = None
    latency_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    utilization_pct: Optional[float] = None
    admin_status: str = "up"
    oper_status: str = "up"
    routing_protocols: Optional[list[str]] = None
    ospf: Optional[dict] = None
    bgp: Optional[dict] = None
    vrfs: Optional[list[str]] = None
    vpn_name: Optional[str] = None
    vpn_type: Optional[str] = None
    redundancy_group: Optional[str] = None
    is_redundant: bool = False
    discovered_by: str = "manual"
    attributes: dict = Field(default_factory=dict)


class TopologyLinkCreate(TopologyLinkBase):
    def validate_vocabulary(self) -> list[str]:
        """Reject invalid link kinds / statuses / protocols / VPN types.

        Returns a list of human-readable problems (empty = valid).
        """
        problems: list[str] = []
        if self.link_type and self.link_type not in LINK_KINDS:
            problems.append(f"link_type {self.link_type!r} not in {LINK_KINDS}")
        if self.oper_status and self.oper_status not in LINK_STATUSES:
            problems.append(f"oper_status {self.oper_status!r} not in {LINK_STATUSES}")
        if self.admin_status and self.admin_status not in LINK_STATUSES:
            problems.append(f"admin_status {self.admin_status!r} not in {LINK_STATUSES}")
        for proto in self.routing_protocols or []:
            if proto not in ROUTING_PROTOCOLS:
                problems.append(f"routing_protocol {proto!r} not in {ROUTING_PROTOCOLS}")
        if self.vpn_type and self.vpn_type not in VPN_TYPES:
            problems.append(f"vpn_type {self.vpn_type!r} not in {VPN_TYPES}")
        return problems


class TopologyLinkUpdate(BaseModel):
    name: Optional[str] = None
    link_type: Optional[str] = None
    provider: Optional[str] = None
    circuit_id: Optional[str] = None
    bandwidth_mbps: Optional[float] = None
    latency_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    utilization_pct: Optional[float] = None
    admin_status: Optional[str] = None
    oper_status: Optional[str] = None
    routing_protocols: Optional[list[str]] = None
    ospf: Optional[dict] = None
    bgp: Optional[dict] = None
    vrfs: Optional[list[str]] = None
    vpn_name: Optional[str] = None
    vpn_type: Optional[str] = None
    redundancy_group: Optional[str] = None
    is_redundant: Optional[bool] = None
    attributes: Optional[dict] = None


class TopologyLinkRead(TopologyLinkBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_state_change_at: Optional[datetime] = None


class TopologyLinkPagination(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TopologyLinkRead]


class TopologyLinkSummary(BaseModel):
    tenant_id: uuid.UUID
    total: int
    up: int = 0
    down: int = 0
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
